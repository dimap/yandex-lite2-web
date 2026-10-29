package main

import (
	"encoding/json"
	"io"
	"net/http"
	"os"
	"strconv"
	"syscall"
	"time"
)

// stream — долгий процесс по WebSocket: двоичные кадры от панели — его stdin, от агента — вывод
// (stdout и stderr вместе). С pty=1 — в псевдотерминале (консоль: Tab, Ctrl+C, top), текстовый кадр
// {"resize":[cols,rows]} меняет размер окна. Закрыли соединение — гасим всю группу процессов; процесс
// закончился — текстовый кадр {"exit":код} и закрытие.
func (a *agent) stream(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	usePty := q.Get("pty") == "1"
	cols, rows := atoiOr(q.Get("cols"), 80), atoiOr(q.Get("rows"), 24)
	ws, err := upgrade(w, r)
	if err != nil {
		fail(w, http.StatusBadRequest, err)
		return
	}
	defer ws.close()
	p := newProc(q.Get("cmd"))
	var out io.ReadCloser
	var in io.WriteCloser
	var master *os.File
	if usePty {
		m, s, err := openPty()
		if err != nil {
			_ = ws.write(opText, mustJSON(map[string]any{"error": err.Error()}))
			return
		}
		_ = setWinsize(m, cols, rows)
		p.cmd.Stdin, p.cmd.Stdout, p.cmd.Stderr = s, s, s
		p.cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true, Setctty: true, Ctty: 0}
		err = p.cmd.Start()
		s.Close()
		if err != nil {
			m.Close()
			_ = ws.write(opText, mustJSON(map[string]any{"error": err.Error()}))
			return
		}
		out, in, master = m, m, m
	} else {
		pr, pw, err := os.Pipe()
		if err != nil {
			_ = ws.write(opText, mustJSON(map[string]any{"error": err.Error()}))
			return
		}
		p.cmd.Stdout, p.cmd.Stderr = pw, pw
		stdin, err := p.cmd.StdinPipe()
		if err == nil {
			err = p.cmd.Start()
		}
		pw.Close()
		if err != nil {
			pr.Close()
			_ = ws.write(opText, mustJSON(map[string]any{"error": err.Error()}))
			return
		}
		out, in = pr, stdin
	}
	defer out.Close()
	defer in.Close()

	pumped := make(chan struct{})
	go func() {
		defer close(pumped)
		buf := make([]byte, 32<<10)
		for {
			n, err := out.Read(buf)
			if n > 0 && ws.write(opBinary, buf[:n]) != nil {
				p.kill()
				return
			}
			if err != nil {
				return
			}
		}
	}()
	go func() {
		for {
			op, data, err := ws.read()
			if err != nil {
				p.kill()
				return
			}
			switch {
			case op == opBinary:
				_, _ = in.Write(data)
			case op == opText && master != nil:
				var m struct{ Resize []int }
				if json.Unmarshal(data, &m) == nil && len(m.Resize) == 2 {
					_ = setWinsize(master, m.Resize[0], m.Resize[1])
				}
			}
		}
	}()
	_ = p.cmd.Wait()
	if master == nil {
		select { // фоновые дети могут держать канал открытым — не ждём их дольше пары секунд
		case <-pumped:
		case <-time.After(2 * time.Second):
		}
	} else {
		select { // в pty после выхода оболочки дочитываем остаток, дальше чтение вернёт EIO
		case <-pumped:
		case <-time.After(500 * time.Millisecond):
		}
	}
	_ = ws.write(opText, mustJSON(map[string]any{"exit": exitCode(p.cmd.ProcessState)}))
	_ = ws.write(opClose, nil)
}

func atoiOr(s string, def int) int {
	if v, err := strconv.Atoi(s); err == nil && v > 0 && v < 1000 {
		return v
	}
	return def
}

func mustJSON(v any) []byte {
	b, _ := json.Marshal(v)
	return b
}
