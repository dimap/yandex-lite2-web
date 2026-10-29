package main

import (
	"bufio"
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const token = "test-token-0123456789abcdefghijklmnopqrstuvwxyz"

func newTestAgent(t *testing.T) (*httptest.Server, string) {
	dir := t.TempDir()
	sum := sha256.Sum256([]byte(token))
	if err := os.WriteFile(filepath.Join(dir, "tokens"), []byte(hex.EncodeToString(sum[:])+" test\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	a := &agent{tokens: newTokens(filepath.Join(dir, "tokens")), started: time.Now()}
	srv := httptest.NewServer(a.routes())
	t.Cleanup(srv.Close)
	return srv, dir
}

func do(t *testing.T, method, url, tok string, body io.Reader) (*http.Response, []byte) {
	req, _ := http.NewRequest(method, url, body)
	if tok != "" {
		req.Header.Set("Authorization", "Bearer "+tok)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	return resp, b
}

func execCmd(t *testing.T, base, cmd string, timeout float64) map[string]any {
	body, _ := json.Marshal(map[string]any{"cmd": cmd, "timeout": timeout})
	resp, b := do(t, "POST", base+"/v1/exec", token, bytes.NewReader(body))
	if resp.StatusCode != 200 {
		t.Fatalf("exec %d %s", resp.StatusCode, b)
	}
	var m map[string]any
	_ = json.Unmarshal(b, &m)
	return m
}

func TestAuth(t *testing.T) {
	srv, _ := newTestAgent(t)
	for _, tok := range []string{"", "wrong-token-0123456789abcdefghijklmnop", token[:len(token)-1]} {
		if resp, _ := do(t, "GET", srv.URL+"/v1/ping", tok, nil); resp.StatusCode != 401 {
			t.Fatalf("токен %q пропущен: %d", tok, resp.StatusCode)
		}
	}
	if resp, _ := do(t, "GET", srv.URL+"/v1/ping", token, nil); resp.StatusCode != 200 {
		t.Fatalf("верный токен не принят: %d", resp.StatusCode)
	}
}

func TestExec(t *testing.T) {
	srv, _ := newTestAgent(t)
	m := execCmd(t, srv.URL, "echo привет; echo ошибка >&2; exit 3", 5)
	if m["out"] != "привет\nошибка\n" || m["rc"] != float64(3) {
		t.Fatalf("вывод и код: %v", m)
	}
	start := time.Now()
	m = execCmd(t, srv.URL, "sleep 30", 0.5)
	if m["timeout"] != true || time.Since(start) > 5*time.Second {
		t.Fatalf("тайм-аут не сработал: %v", m)
	}
}

func TestFileAndLs(t *testing.T) {
	srv, dir := newTestAgent(t)
	path := filepath.Join(dir, "sub", "f.bin")
	data := make([]byte, 300000)
	_, _ = rand.Read(data)
	if resp, b := do(t, "PUT", srv.URL+"/v1/file?mode=755&path="+path, token, bytes.NewReader(data)); resp.StatusCode != 200 {
		t.Fatalf("PUT %d %s", resp.StatusCode, b)
	}
	if st, _ := os.Stat(path); st.Mode().Perm() != 0o755 {
		t.Fatalf("режим %v", st.Mode())
	}
	resp, b := do(t, "GET", srv.URL+"/v1/file?path="+path, token, nil)
	if resp.StatusCode != 200 || !bytes.Equal(b, data) {
		t.Fatalf("GET не совпал: %d %d", resp.StatusCode, len(b))
	}
	if resp, _ := do(t, "GET", srv.URL+"/v1/file?path="+filepath.Join(dir, "нет"), token, nil); resp.StatusCode != 404 {
		t.Fatalf("нет файла — %d", resp.StatusCode)
	}
	_, b = do(t, "GET", srv.URL+"/v1/ls?path="+filepath.Join(dir, "sub"), token, nil)
	var ls [][]any
	_ = json.Unmarshal(b, &ls)
	names := []string{}
	for _, e := range ls {
		names = append(names, e[3].(string))
	}
	if strings.Join(names, ",") != ".,..,f.bin" || ls[2][1] != float64(len(data)) {
		t.Fatalf("ls: %v", ls)
	}
	_, b = do(t, "GET", srv.URL+"/v1/ls?path="+filepath.Join(dir, "нет"), token, nil)
	if strings.TrimSpace(string(b)) != "[]" {
		t.Fatalf("нет папки — %s", b)
	}
}

// клиент WebSocket для теста: кадры с маской, как у настоящего клиента
type testWS struct {
	c  net.Conn
	br *bufio.Reader
}

func dialWS(t *testing.T, srv *httptest.Server, query string) *testWS {
	c, err := net.Dial("tcp", strings.TrimPrefix(srv.URL, "http://"))
	if err != nil {
		t.Fatal(err)
	}
	key := base64.StdEncoding.EncodeToString([]byte("0123456789abcdef"))
	req := "GET /v1/stream?" + query + " HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n" +
		"Sec-WebSocket-Key: " + key + "\r\nSec-WebSocket-Version: 13\r\nAuthorization: Bearer " + token + "\r\n\r\n"
	_, _ = c.Write([]byte(req))
	br := bufio.NewReader(c)
	resp, err := http.ReadResponse(br, nil)
	if err != nil || resp.StatusCode != 101 {
		t.Fatalf("upgrade: %v %v", err, resp)
	}
	return &testWS{c, br}
}

func (w *testWS) send(op byte, data []byte) {
	hdr := []byte{0x80 | op, 0x80 | byte(len(data))}
	mask := []byte{1, 2, 3, 4}
	p := append([]byte{}, data...)
	for i := range p {
		p[i] ^= mask[i%4]
	}
	_, _ = w.c.Write(append(append(hdr, mask...), p...))
}

func (w *testWS) recv() (byte, []byte, error) {
	_ = w.c.SetReadDeadline(time.Now().Add(10 * time.Second))
	var h [2]byte
	if _, err := io.ReadFull(w.br, h[:]); err != nil {
		return 0, nil, err
	}
	n := int(h[1] & 0x7F)
	if n == 126 {
		var b [2]byte
		_, _ = io.ReadFull(w.br, b[:])
		n = int(binary.BigEndian.Uint16(b[:]))
	}
	p := make([]byte, n)
	_, err := io.ReadFull(w.br, p)
	return h[0] & 0x0F, p, err
}

// readUntilExit — весь вывод до {"exit":…}
func (w *testWS) readUntilExit(t *testing.T) (string, int) {
	var out strings.Builder
	for {
		op, p, err := w.recv()
		if err != nil {
			t.Fatalf("поток оборвался: %v (вывод %q)", err, out.String())
		}
		if op == opBinary {
			out.Write(p)
		} else if op == opText {
			var m struct{ Exit *int }
			if json.Unmarshal(p, &m) == nil && m.Exit != nil {
				return out.String(), *m.Exit
			}
		}
	}
}

func TestStreamPipe(t *testing.T) {
	srv, _ := newTestAgent(t)
	ws := dialWS(t, srv, "cmd=read+x%3B+echo+got:$x%3B+exit+4")
	ws.send(opBinary, []byte("hello\n"))
	out, rc := ws.readUntilExit(t)
	if out != "got:hello\n" || rc != 4 {
		t.Fatalf("поток: %q %d", out, rc)
	}
}

func TestStreamPty(t *testing.T) {
	if _, err := os.Stat("/dev/ptmx"); err != nil {
		t.Skip("нет /dev/ptmx")
	}
	srv, _ := newTestAgent(t)
	ws := dialWS(t, srv, "pty=1&cols=120&rows=40&cmd=[+-t+0+]+%26%26+echo+tty%3B+stty+size")
	out, rc := ws.readUntilExit(t)
	if !strings.Contains(out, "tty") || !strings.Contains(out, "40 120") || rc != 0 {
		t.Fatalf("pty: %q %d", out, rc)
	}
}
