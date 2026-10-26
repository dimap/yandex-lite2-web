package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const profile = "client\ndev tun\nproto udp\nremote vpn.example.test 1194\nauth-user-pass\n<ca>\nTEST-CA\n</ca>\nredirect-gateway def1\ndhcp-option DNS 9.9.9.9\n"

func TestRedShieldQuotedDNS(t *testing.T) {
	src := profile + "pull-filter ignore \"dns\"\ndhcp-option DNS \"1.1.1.1\"\n"
	got, auth, err := sanitizeOVPN(src)
	if err != nil || !auth {
		t.Fatal("quoted Red Shield profile rejected", err)
	}
	if !strings.Contains(got, "pull-filter ignore dns") || strings.Contains(got, "dhcp-option DNS") {
		t.Fatal("DNS policy changed")
	}
	for _, line := range []string{`remote "vpn.example.test" 443 tcp # fallback`, `verify-x509-name "VPN server" name`} {
		fields, err := ovpnFields(line)
		if err != nil {
			t.Fatal(err)
		}
		again, err := ovpnFields(ovpnLine(fields))
		if err != nil || strings.Join(fields, "\x00") != strings.Join(again, "\x00") {
			t.Fatal("quote roundtrip")
		}
	}
	for _, line := range []string{`"up" "/bin/reboot"`, `"plugin" "/tmp/x.so"`, `pull-filter accept "route"`, `pull-filter ignore "dns`, `remote host\`} {
		if _, _, err := sanitizeOVPN(profile + line + "\n"); err == nil {
			t.Fatalf("accepted unsafe/malformed line %q", line)
		}
	}
}

func TestOptionalOVPNProfile(t *testing.T) {
	path := os.Getenv("STATION_TEST_OVPN")
	if path == "" {
		t.Skip("no local profile supplied")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal("could not read supplied profile")
	}
	if _, _, err = sanitizeOVPN(string(data)); err != nil {
		t.Fatal(err)
	}
}

func TestOVPN(t *testing.T) {
	got, auth, err := sanitizeOVPN(profile)
	if err != nil || !auth {
		t.Fatal(got, auth, err)
	}
	for _, required := range []string{"route-nopull", "dev webgpt0", "remote-cert-tls server", "script-security 1"} {
		if !strings.Contains(got, required) {
			t.Fatal(required)
		}
	}
	if strings.Contains(got, "redirect-gateway def1") || strings.Contains(got, "dhcp-option DNS") || strings.Contains(got, "auth-user-pass\n") {
		t.Fatal("profile controls the host network or credentials")
	}
	for _, evil := range []string{"up /bin/reboot", "plugin /tmp/test.so", "config /etc/shadow", "ca /etc/shadow", "auth-user-pass /etc/shadow", "script-security 3", "management 0.0.0.0 1199", "log /etc/passwd", "<connection>\nremote a\n</connection>", "tls-verify /bin/sh", "setenv OPENSSL_CONF /tmp/x", "pkcs11-providers /tmp/x"} {
		if _, _, err := sanitizeOVPN(profile + evil + "\n"); err == nil {
			t.Fatalf("accepted %s", evil)
		}
	}
	for _, bad := range []string{"client\nremote example\n", strings.Replace(profile, "</ca>", "", 1), strings.Replace(profile, "dev tun", "dev tap", 1), profile + "<key>\nx\n</key>\n"} {
		if _, _, err := sanitizeOVPN(bad); err == nil {
			t.Fatal("accepted incomplete profile")
		}
	}
}

func TestFinalVoiceOnlyAndPreservedQuestion(t *testing.T) {
	for _, phrase := range []string{"спроси GPT, Сколько будет 3.14 * 2?", "Алиса, спроси джи пи ти: Сколько будет 3.14 * 2?", "Спроси джипити Сколько будет 3.14 * 2?"} {
		q, ok := voiceQuery("onAliceStateChanged: BUSY, recognizedPhrase='"+phrase+"'", "спроси gpt")
		if !ok || q != "Сколько будет 3.14 * 2?" {
			t.Fatal(q, ok)
		}
	}
	for _, line := range []string{"onRequestStateChanged: LISTENING, recognizedPhrase='спроси GPT почему'", "onAliceStateChanged: BUSY, recognizedPhrase='включи музыку'", "onAliceStateChanged: BUSY, recognizedPhrase='спроси gptfoo почему'"} {
		if _, ok := voiceQuery(line, "спроси gpt"); ok {
			t.Fatal(line)
		}
	}
}

func testDaemon(t *testing.T, endpoint string) *Daemon {
	t.Helper()
	c := defaults()
	c.APIKey = "test-secret-DO-NOT-PRINT"
	c.Enabled = true
	return &Daemon{dir: t.TempDir(), cfg: c, vpn: &VPN{}, endpoint: endpoint, quiet: func(context.Context) {}, play: func(context.Context, []byte) error { return nil }}
}

func waitDone(t *testing.T, d *Daemon) {
	t.Helper()
	d.mu.Lock()
	done := d.done
	d.mu.Unlock()
	select {
	case <-done:
	case <-time.After(4 * time.Second):
		t.Fatal("request stuck")
	}
}

func TestResponsesSpeechAndMemory(t *testing.T) {
	var requests atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer test-secret-DO-NOT-PRINT" {
			t.Error("missing auth")
		}
		var data map[string]any
		if json.NewDecoder(r.Body).Decode(&data) != nil {
			t.Error("bad json")
		}
		switch r.URL.Path {
		case "/responses":
			n := requests.Add(1)
			if data["store"] != false || data["max_output_tokens"].(float64) != 1024 {
				t.Error("unbounded persistence or tokens")
			}
			if len(data["input"].([]any)) != int(n*2-1) {
				t.Error("missing conversation context")
			}
			io.WriteString(w, `{"status":"completed","output":[{"type":"reasoning"},{"type":"message","content":[{"type":"output_text","text":"Ответ."}]}]}`)
		case "/audio/speech":
			if data["response_format"] != "pcm" || data["input"] != "Ответ." {
				t.Error("bad speech request")
			}
			w.Write([]byte{0, 0, 1, 0})
		default:
			t.Error("unexpected endpoint")
		}
	}))
	defer srv.Close()
	d := testDaemon(t, srv.URL)
	var plays atomic.Int32
	d.play = func(_ context.Context, b []byte) error {
		if len(b) != 4 {
			t.Error("bad PCM")
		}
		plays.Add(1)
		return nil
	}
	for i := 0; i < 2; i++ {
		if err := d.ask("Вопрос"); err != nil {
			t.Fatal(err)
		}
		waitDone(t, d)
	}
	if plays.Load() != 2 || d.err != "" {
		t.Fatal("pipeline failed", d.err)
	}
	b, _ := json.Marshal(d.status())
	if strings.Contains(string(b), "test-secret") || strings.Contains(string(b), "Ответ.") {
		t.Fatal("status leaked secrets or transcripts")
	}
}

func TestErrorRedactionAndVPNGate(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(401)
		io.WriteString(w, "test-secret-DO-NOT-PRINT user transcript")
	}))
	defer srv.Close()
	d := testDaemon(t, srv.URL)
	if err := d.ask("Вопрос"); err != nil {
		t.Fatal(err)
	}
	waitDone(t, d)
	if !strings.Contains(d.err, "API-ключ") || strings.Contains(d.err, "test-secret") {
		t.Fatal(d.err)
	}
	d.cfg.VPNEnabled = true
	if d.ask("Вопрос") == nil || calls.Load() != 1 {
		t.Fatal("VPN gate failed")
	}
}

func TestCancellation(t *testing.T) {
	entered := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		close(entered)
		<-r.Context().Done()
	}))
	defer srv.Close()
	d := testDaemon(t, srv.URL)
	if err := d.ask("Вопрос"); err != nil {
		t.Fatal(err)
	}
	<-entered
	if d.ask("Другой вопрос") == nil {
		t.Fatal("concurrent request accepted")
	}
	d.stop()
	if d.busy || d.err != "" {
		t.Fatal("not cancelled cleanly")
	}
}

func TestConfigSecretsAndPermissions(t *testing.T) {
	d := testDaemon(t, "")
	if err := d.update([]byte(`{"enabled":true,"model":"custom-model"}`), false); err != nil {
		t.Fatal(err)
	}
	c, err := readConfig(d.dir)
	if err != nil || c.APIKey != d.cfg.APIKey || c.Model != "custom-model" {
		t.Fatal("key not preserved")
	}
	st, _ := os.Stat(filepath.Join(d.dir, "config.json"))
	if st.Mode().Perm() != 0600 {
		t.Fatal(st.Mode())
	}
	if d.update([]byte(`{"api_key":"x\ny"}`), false) == nil {
		t.Fatal("header injection accepted")
	}
	if err = d.update([]byte(`{"clear_key":true}`), false); err != nil {
		t.Fatal(err)
	}
	c, _ = readConfig(d.dir)
	if c.Enabled || c.APIKey != "" {
		t.Fatal("key not removed")
	}
}

func TestWatchSkipsHistoryAndHandlesRotation(t *testing.T) {
	d := testDaemon(t, "")
	d.cfg.APIKey = "" // Track ASR without making any network requests.
	path := filepath.Join(t.TempDir(), "maind.log")
	line := "onAliceStateChanged: BUSY, recognizedPhrase='спроси GPT вопрос'\n"
	os.WriteFile(path, []byte(line), 0600)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go d.watch(ctx, path)
	time.Sleep(400 * time.Millisecond)
	d.mu.Lock()
	old := d.lastVoice
	d.mu.Unlock()
	if !old.IsZero() {
		t.Fatal("replayed history")
	}
	f, _ := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0600)
	f.WriteString(line[:20])
	f.Close()
	time.Sleep(350 * time.Millisecond)
	d.mu.Lock()
	old = d.lastVoice
	d.mu.Unlock()
	if !old.IsZero() {
		t.Fatal("processed partial line")
	}
	f, _ = os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0600)
	f.WriteString(line[20:])
	f.Close()
	time.Sleep(350 * time.Millisecond)
	d.mu.Lock()
	old = d.lastVoice
	d.mu.Unlock()
	if old.IsZero() {
		t.Fatal("missed final line")
	}
	os.Rename(path, path+".1")
	os.WriteFile(path, []byte(strings.Replace(line, "вопрос", "ещё вопрос", 1)), 0600)
	time.Sleep(350 * time.Millisecond)
	d.mu.Lock()
	fresh := d.lastVoice
	d.mu.Unlock()
	if !fresh.After(old) {
		t.Fatal("rotation lost")
	}
}

func TestIncompleteResponseAndRefusal(t *testing.T) {
	if _, err := responseText([]byte(`{"status":"incomplete","output":[]}`)); err == nil {
		t.Fatal("accepted incomplete")
	}
	s, err := responseText([]byte(`{"status":"completed","output":[{"type":"message","content":[{"type":"refusal","refusal":"Не могу помочь."}]}]}`))
	if err != nil || s != "Не могу помочь." {
		t.Fatal(s, err)
	}
}
