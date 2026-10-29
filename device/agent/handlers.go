package main

import (
	"encoding/json"
	"errors"
	"io"
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const outLimit = 64 << 20 // вывод команды больше 64 МБ обрезаем

type agent struct {
	tokens  *tokens
	started time.Time
}

func (a *agent) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/v1/ping", a.ping)
	mux.HandleFunc("/v1/exec", a.exec)
	mux.HandleFunc("/v1/file", a.file)
	mux.HandleFunc("/v1/ls", a.ls)
	mux.HandleFunc("/v1/stream", a.stream)
	return a.auth(mux)
}

// auth: без верного токена — 401 и пауза, чтобы токен нельзя было быстро перебирать.
func (a *agent) auth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		token, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
		if !ok || !a.tokens.valid(token) {
			time.Sleep(time.Second)
			writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "нужен токен панели"})
			return
		}
		next.ServeHTTP(w, r)
	})
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func fail(w http.ResponseWriter, code int, err error) {
	writeJSON(w, code, map[string]any{"error": err.Error()})
}

func (a *agent) ping(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"version": version, "uptime": int(time.Since(a.started).Seconds()), "pid": os.Getpid()})
}

type limitWriter struct {
	buf []byte
	cut bool
}

func (l *limitWriter) Write(p []byte) (int, error) {
	if room := outLimit - len(l.buf); room < len(p) {
		l.buf, l.cut = append(l.buf, p[:max(room, 0)]...), true
		return len(p), nil
	}
	l.buf = append(l.buf, p...)
	return len(p), nil
}

// exec: команда через sh -c, как adb shell — stdout и stderr вместе, stdin пустой. По тайм-ауту или обрыву
// соединения гасим всю группу процессов: команда не должна пережить вызов.
func (a *agent) exec(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		fail(w, http.StatusMethodNotAllowed, errors.New("нужен POST"))
		return
	}
	var req struct {
		Cmd     string  `json:"cmd"`
		Timeout float64 `json:"timeout"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&req); err != nil {
		fail(w, http.StatusBadRequest, err)
		return
	}
	timeout := time.Duration(req.Timeout * float64(time.Second))
	if timeout <= 0 || timeout > time.Hour {
		timeout = time.Hour
	}
	out := &limitWriter{}
	p := newProc(req.Cmd)
	p.cmd.Stdout, p.cmd.Stderr = out, out
	if err := p.cmd.Start(); err != nil {
		fail(w, http.StatusInternalServerError, err)
		return
	}
	done := make(chan error, 1)
	go func() { done <- p.cmd.Wait() }()
	timedOut := false
	select {
	case <-done:
	case <-time.After(timeout):
		timedOut = true
		p.kill()
		<-done
	case <-r.Context().Done():
		p.kill()
		<-done
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"out": string(out.buf), "rc": exitCode(p.cmd.ProcessState), "timeout": timedOut, "cut": out.cut})
}

// file: GET — содержимое, PUT — записать на месте (как adb push: подмены через mount bind видят новый текст).
func (a *agent) file(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Query().Get("path")
	if !filepath.IsAbs(path) {
		fail(w, http.StatusBadRequest, errors.New("нужен абсолютный путь"))
		return
	}
	switch r.Method {
	case http.MethodGet:
		f, err := os.Open(path)
		if err != nil {
			fail(w, statusFor(err), err)
			return
		}
		defer f.Close()
		st, err := f.Stat()
		if err != nil {
			fail(w, http.StatusInternalServerError, err)
			return
		}
		if st.IsDir() {
			fail(w, http.StatusBadRequest, errors.New(path+": это папка"))
			return
		}
		w.Header().Set("Content-Type", "application/octet-stream")
		if st.Mode().IsRegular() {
			w.Header().Set("Content-Length", strconv.FormatInt(st.Size(), 10))
		}
		_, _ = io.Copy(w, f)
	case http.MethodPut:
		mode := os.FileMode(0o644)
		if m := r.URL.Query().Get("mode"); m != "" {
			v, err := strconv.ParseUint(m, 8, 32)
			if err != nil {
				fail(w, http.StatusBadRequest, err)
				return
			}
			mode = os.FileMode(v) & os.ModePerm
		}
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			fail(w, statusFor(err), err)
			return
		}
		f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, mode)
		if err != nil {
			fail(w, statusFor(err), err)
			return
		}
		n, err := io.Copy(f, r.Body)
		if cerr := f.Close(); err == nil {
			err = cerr
		}
		if err == nil {
			err = os.Chmod(path, mode)
		}
		if err != nil {
			fail(w, http.StatusInternalServerError, err)
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"size": n})
	default:
		fail(w, http.StatusMethodNotAllowed, errors.New("нужен GET или PUT"))
	}
}

func statusFor(err error) int {
	switch {
	case errors.Is(err, fs.ErrNotExist):
		return http.StatusNotFound
	case errors.Is(err, fs.ErrPermission):
		return http.StatusForbidden
	}
	return http.StatusInternalServerError
}

// ls — как adb ls (sync LIST): [режим, размер, mtime, имя] по lstat, вместе с «.» и «..»; нет папки — пусто.
func (a *agent) ls(w http.ResponseWriter, r *http.Request) {
	dir := r.URL.Query().Get("path")
	if !filepath.IsAbs(dir) {
		fail(w, http.StatusBadRequest, errors.New("нужен абсолютный путь"))
		return
	}
	res := [][]any{}
	if names, err := readNames(dir); err == nil {
		for _, name := range append([]string{".", ".."}, names...) {
			var st syscall.Stat_t
			if syscall.Lstat(filepath.Join(dir, name), &st) != nil {
				continue
			}
			res = append(res, []any{st.Mode, st.Size, st.Mtim.Sec, name})
		}
	}
	writeJSON(w, http.StatusOK, res)
}

func readNames(dir string) ([]string, error) {
	f, err := os.Open(dir)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	return f.Readdirnames(-1)
}
