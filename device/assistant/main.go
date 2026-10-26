package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"
)

type turn struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}
type Daemon struct {
	dir        string
	mu         sync.Mutex
	cfg        Config
	vpn        *VPN
	busy       bool
	changing   bool
	cancel     context.CancelFunc
	done       chan struct{}
	err        string
	stage      string
	requests   int
	lastVoice  time.Time
	lastAnswer time.Time
	history    []turn
	logOK      bool
	// Fixed in production; injectable in unit tests, never configurable over the UI.
	endpoint string
	play     func(context.Context, []byte) error
	quiet    func(context.Context)
}

func (d *Daemon) status() map[string]any {
	d.mu.Lock()
	defer d.mu.Unlock()
	connected, state, msg := d.vpn.status()
	_, vpnErr := os.Stat("/opt/sbin/openvpn")
	_, routingErr := os.Stat(filepath.Join(d.dir, ".vpn-ready"))
	_, needsAuth, _ := sanitizeOVPN(d.cfg.VPNConfig)
	return map[string]any{"installed": true, "running": true, "version": version,
		"enabled": d.cfg.Enabled, "has_key": d.cfg.APIKey != "", "model": d.cfg.Model, "voice": d.cfg.Voice,
		"prefix": d.cfg.Prefix, "instructions": d.cfg.Instructions, "busy": d.busy, "stage": d.stage,
		"error": d.err, "requests": d.requests, "last_voice": d.lastVoice.Unix(), "log_available": d.logOK,
		"vpn_enabled": d.cfg.VPNEnabled, "vpn_configured": d.cfg.VPNConfig != "", "vpn_has_auth": d.cfg.VPNUser != "" && d.cfg.VPNPassword != "",
		"vpn_needs_auth": needsAuth,
		"vpn_installed":  vpnErr == nil && routingErr == nil, "vpn_connected": connected, "vpn_state": state, "vpn_message": msg, "vpn_dns": d.cfg.VPNDNS}
}

func (d *Daemon) update(data []byte, vpn bool) error {
	d.mu.Lock()
	old := d.cfg
	d.mu.Unlock()
	c := old
	if vpn {
		var p struct {
			Enabled  bool    `json:"enabled"`
			Config   string  `json:"config"`
			User     *string `json:"user"`
			Password *string `json:"password"`
			DNS      string  `json:"dns"`
			Clear    bool    `json:"clear"`
		}
		if json.Unmarshal(data, &p) != nil {
			return errors.New("Некорректные настройки VPN")
		}
		c.VPNEnabled = p.Enabled
		if p.Config != "" {
			if _, _, err := sanitizeOVPN(p.Config); err != nil {
				return err
			}
			c.VPNConfig = p.Config
		}
		if p.User != nil {
			c.VPNUser = *p.User
		}
		if p.Password != nil {
			c.VPNPassword = *p.Password
		}
		if p.DNS != "" {
			c.VPNDNS = p.DNS
		}
		if p.Clear {
			c.VPNConfig = ""
			c.VPNUser = ""
			c.VPNPassword = ""
			c.VPNEnabled = false
		}
		if err := validCredentials(c); err != nil {
			return err
		}
		if c.VPNEnabled {
			_, auth, err := sanitizeOVPN(c.VPNConfig)
			if err != nil {
				return err
			}
			if auth && (c.VPNUser == "" || c.VPNPassword == "") {
				return errors.New("Профиль требует логин и пароль Red Shield")
			}
			if _, err = os.Stat("/opt/sbin/openvpn"); err != nil {
				return errors.New("Сначала установите OpenVPN")
			}
		}
	} else {
		var p struct {
			Enabled      bool    `json:"enabled"`
			Key          *string `json:"api_key"`
			Model        string  `json:"model"`
			Voice        string  `json:"voice"`
			Prefix       string  `json:"prefix"`
			Instructions string  `json:"instructions"`
			ClearKey     bool    `json:"clear_key"`
		}
		if json.Unmarshal(data, &p) != nil {
			return errors.New("Некорректные настройки OpenAI")
		}
		c.Enabled = p.Enabled
		if p.Key != nil && *p.Key != "" {
			c.APIKey = strings.TrimSpace(*p.Key)
		}
		if p.ClearKey {
			c.APIKey = ""
			c.Enabled = false
		}
		if p.Model != "" {
			c.Model = p.Model
		}
		if p.Voice != "" {
			c.Voice = p.Voice
		}
		if p.Prefix != "" {
			c.Prefix = normalize(p.Prefix)
		}
		if p.Instructions != "" {
			c.Instructions = p.Instructions
		}
		if !modelID.MatchString(c.Model) || !modelID.MatchString(c.Voice) {
			return errors.New("Неверное имя модели или голоса")
		}
		if len(c.APIKey) > 512 || strings.ContainsAny(c.APIKey, " \r\n\t\x00") {
			return errors.New("Неверный формат API-ключа")
		}
		if len([]rune(c.Prefix)) < 3 || len(c.Prefix) > 120 || len(c.Instructions) > 4000 {
			return errors.New("Проверьте длину голосовой фразы и инструкции")
		}
		if c.Enabled && c.APIKey == "" {
			return errors.New("Введите API-ключ OpenAI")
		}
	}
	d.mu.Lock()
	d.changing = true
	d.mu.Unlock()
	defer func() { d.mu.Lock(); d.changing = false; d.mu.Unlock() }()
	if err := saveConfig(d.dir, c); err != nil {
		return errors.New("Не удалось сохранить настройки на колонке")
	}
	d.stop()
	d.mu.Lock()
	d.cfg = c
	d.history = nil
	d.err = ""
	d.mu.Unlock()
	if vpn {
		d.vpn.stop()
		d.vpn.mu.Lock()
		d.vpn.lastStart = time.Time{}
		d.vpn.mu.Unlock()
	}
	return nil
}

func (d *Daemon) stop() {
	d.mu.Lock()
	cancel, done := d.cancel, d.done
	d.mu.Unlock()
	if cancel != nil {
		cancel()
		<-done
	}
}
func (d *Daemon) setStage(s string) { d.mu.Lock(); d.stage = s; d.mu.Unlock() }

func clientFor(c Config) *http.Client {
	dialer := &net.Dialer{Timeout: 15 * time.Second}
	if c.VPNEnabled {
		dialer.Control = markSocket
		dialer.Resolver = &net.Resolver{PreferGo: true, Dial: func(ctx context.Context, network, address string) (net.Conn, error) {
			dns := net.Dialer{Timeout: 8 * time.Second, Control: markSocket}
			transport := "udp4"
			if strings.HasPrefix(network, "tcp") {
				transport = "tcp4"
			}
			return dns.DialContext(ctx, transport, net.JoinHostPort(c.VPNDNS, "53"))
		}}
	}
	tr := &http.Transport{TLSHandshakeTimeout: 15 * time.Second, ResponseHeaderTimeout: 50 * time.Second, DisableKeepAlives: true,
		DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
			return dialer.DialContext(ctx, "tcp4", address)
		}}
	return &http.Client{Transport: tr, Timeout: 65 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return errors.New("redirect rejected") }}
}

func (d *Daemon) post(ctx context.Context, c Config, path string, payload any, limit int64) ([]byte, error) {
	if c.VPNEnabled {
		connected, _, _ := d.vpn.status()
		if !connected {
			return nil, errors.New("VPN не подключён; запрос OpenAI не отправлен")
		}
	}
	data, _ := json.Marshal(payload)
	req, err := http.NewRequestWithContext(ctx, "POST", d.endpoint+path, bytes.NewReader(data))
	if err != nil {
		return nil, errors.New("Ошибка запроса OpenAI")
	}
	req.Header.Set("Authorization", "Bearer "+c.APIKey)
	req.Header.Set("Content-Type", "application/json")
	client := clientFor(c)
	defer client.CloseIdleConnections()
	resp, err := client.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, errors.New("Нет ответа от OpenAI: проверьте интернет и VPN")
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		// Provider responses can echo credentials or user text. Never return their body.
		switch resp.StatusCode {
		case 401:
			return nil, errors.New("OpenAI отклонил API-ключ")
		case 403:
			return nil, errors.New("OpenAI отказал в доступе: проверьте проект, модель и VPN")
		case 429:
			return nil, errors.New("Лимит OpenAI: проверьте баланс API и частоту запросов")
		default:
			return nil, fmt.Errorf("OpenAI: HTTP %d; проверьте модель и доступ проекта", resp.StatusCode)
		}
	}
	b, err := io.ReadAll(io.LimitReader(resp.Body, limit+1))
	if err != nil {
		return nil, errors.New("Ответ OpenAI прерван")
	}
	if int64(len(b)) > limit {
		return nil, errors.New("Ответ OpenAI слишком большой")
	}
	return b, nil
}

func responseText(b []byte) (string, error) {
	var r struct {
		Status string `json:"status"`
		Output []struct {
			Type    string `json:"type"`
			Content []struct {
				Type    string `json:"type"`
				Text    string `json:"text"`
				Refusal string `json:"refusal"`
			} `json:"content"`
		} `json:"output"`
	}
	if json.Unmarshal(b, &r) != nil {
		return "", errors.New("Некорректный ответ OpenAI")
	}
	var parts []string
	for _, o := range r.Output {
		if o.Type == "message" {
			for _, c := range o.Content {
				if c.Type == "output_text" {
					parts = append(parts, c.Text)
				} else if c.Type == "refusal" {
					parts = append(parts, c.Refusal)
				}
			}
		}
	}
	s := strings.TrimSpace(strings.Join(parts, "\n"))
	if s == "" {
		return "", errors.New("OpenAI не вернул текст: увеличьте лимит или выберите другую модель")
	}
	if r.Status != "" && r.Status != "completed" {
		return "", errors.New("OpenAI не завершил ответ в пределах лимита")
	}
	if len([]rune(s)) > 3500 {
		s = string([]rune(s)[:3500])
	}
	return s, nil
}

func (d *Daemon) ask(text string) error { return d.askWithMode(text, false) }
func (d *Daemon) askWithMode(text string, voice bool) error {
	text = strings.TrimSpace(text)
	if text == "" {
		return errors.New("После голосовой фразы произнесите вопрос")
	}
	if len([]rune(text)) > 2000 {
		return errors.New("Вопрос слишком длинный")
	}
	d.mu.Lock()
	if d.changing || (voice && !d.cfg.Enabled) {
		d.mu.Unlock()
		return errors.New("Голосовой запуск выключен или настройки обновляются")
	}
	if d.busy {
		d.mu.Unlock()
		return errors.New("Помощник уже отвечает")
	}
	c := d.cfg
	if c.APIKey == "" {
		d.mu.Unlock()
		return errors.New("API-ключ OpenAI не задан")
	}
	if c.VPNEnabled {
		connected, _, _ := d.vpn.status()
		if !connected {
			d.mu.Unlock()
			return errors.New("Сначала дождитесь подключения VPN")
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Second)
	d.busy = true
	d.cancel = cancel
	d.done = make(chan struct{})
	d.err = ""
	d.stage = "thinking"
	d.requests++
	if time.Since(d.lastAnswer) > 10*time.Minute {
		d.history = nil
	}
	input := append(append([]turn{}, d.history...), turn{"user", text})
	d.mu.Unlock()
	go func() {
		var failure error
		defer func() {
			cancel()
			d.mu.Lock()
			d.busy = false
			d.cancel = nil
			d.stage = "idle"
			if failure != nil && !errors.Is(failure, context.Canceled) {
				d.err = failure.Error()
			}
			close(d.done)
			d.mu.Unlock()
		}()
		// Stop Alice's own answer/music with its local CLI; microphone owner stays alive.
		d.quiet(ctx)
		payload := map[string]any{"model": c.Model, "instructions": c.Instructions, "input": input, "store": false, "max_output_tokens": 1024}
		if strings.HasPrefix(c.Model, "gpt-5") || strings.HasPrefix(c.Model, "gpt-6") {
			payload["reasoning"] = map[string]string{"effort": "low"}
		}
		b, err := d.post(ctx, c, "/responses", payload, 512*1024)
		if err != nil {
			failure = err
			return
		}
		answer, err := responseText(b)
		if err != nil {
			failure = err
			return
		}
		d.setStage("speech")
		pcm, err := d.post(ctx, c, "/audio/speech", map[string]any{"model": "gpt-4o-mini-tts", "voice": c.Voice, "input": answer, "response_format": "pcm"}, 6*1024*1024)
		if err != nil {
			failure = err
			return
		}
		if len(pcm) == 0 || len(pcm)%2 != 0 {
			failure = errors.New("OpenAI вернул некорректный звук")
			return
		}
		d.setStage("playing")
		if err = d.play(ctx, pcm); err != nil {
			if ctx.Err() != nil {
				failure = ctx.Err()
			} else {
				failure = errors.New("Не удалось воспроизвести ответ через ALSA")
			}
			return
		}
		d.mu.Lock()
		d.lastAnswer = time.Now()
		d.history = append(input, turn{"assistant", answer})
		if len(d.history) > 8 {
			d.history = d.history[len(d.history)-8:]
		}
		d.mu.Unlock()
	}()
	return nil
}

func playPCM(ctx context.Context, pcm []byte) error {
	cmd := exec.CommandContext(ctx, "/usr/bin/aplay", "-q", "-D", "default", "-t", "raw", "-f", "S16_LE", "-r", "24000", "-c", "1")
	prepareChild(cmd)
	cmd.Stdin = bytes.NewReader(pcm)
	cmd.WaitDelay = 2 * time.Second
	return cmd.Run()
}

func quietAlice(ctx context.Context) {
	ctx, cancel := context.WithTimeout(ctx, 6*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "/system/vendor/quasar/maind", "--cli")
	prepareChild(cmd)
	cmd.Env = append(os.Environ(), "LD_LIBRARY_PATH=/system/vendor/quasar/libs")
	cmd.WaitDelay = time.Second
	pipe, err := cmd.StdinPipe()
	if err != nil {
		return
	}
	if cmd.Start() != nil {
		return
	}
	go func() {
		defer pipe.Close()
		for _, part := range []string{"q\n", "стоп\n", "x\n"} {
			select {
			case <-ctx.Done():
				return
			case <-time.After(600 * time.Millisecond):
			}
			_, _ = io.WriteString(pipe, part)
		}
	}()
	_ = cmd.Wait()
}

// Start at EOF so a restart never resends old conversations. Follow both rename
// rotation and copy-truncate, and preserve partial lines between reads.
func (d *Daemon) watch(ctx context.Context, path string) {
	var f *os.File
	var info os.FileInfo
	var offset int64
	var pending string
	defer func() {
		if f != nil {
			f.Close()
		}
	}()
	first := true
	last := ""
	var lastAt time.Time
	ticker := time.NewTicker(300 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
		st, err := os.Stat(path)
		d.mu.Lock()
		d.logOK = err == nil
		d.mu.Unlock()
		if err != nil {
			continue
		}
		if f == nil || !os.SameFile(info, st) || st.Size() < offset {
			if f != nil {
				f.Close()
			}
			f, err = os.Open(path)
			if err != nil {
				f = nil
				continue
			}
			info = st
			offset = 0
			pending = ""
			if first {
				offset = st.Size()
				first = false
			}
			_, _ = f.Seek(offset, io.SeekStart)
		}
		buf := make([]byte, 64*1024)
		n, _ := f.Read(buf)
		offset += int64(n)
		if n == 0 {
			continue
		}
		pending += string(buf[:n])
		lines := strings.Split(pending, "\n")
		pending = lines[len(lines)-1]
		if len(pending) > 65536 {
			pending = ""
		}
		for _, line := range lines[:len(lines)-1] {
			d.mu.Lock()
			c := d.cfg
			d.mu.Unlock()
			if !c.Enabled {
				continue
			}
			q, ok := voiceQuery(line, c.Prefix)
			if !ok {
				continue
			}
			if q == last && time.Since(lastAt) < 8*time.Second {
				continue
			}
			last = q
			lastAt = time.Now()
			d.mu.Lock()
			d.lastVoice = time.Now()
			d.mu.Unlock()
			if normalize(q) == "стоп" || normalize(q) == "остановись" {
				d.stop()
				continue
			}
			if normalize(q) == "новый разговор" {
				d.stop()
				d.mu.Lock()
				d.history = nil
				d.mu.Unlock()
				continue
			}
			if err := d.askWithMode(q, true); err != nil {
				d.mu.Lock()
				d.err = err.Error()
				d.mu.Unlock()
			}
		}
	}
}

func (d *Daemon) handler() http.Handler {
	// Serialise mutations (configuration, tests, stops), not background processing.
	var mutations sync.Mutex
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store")
		if r.URL.Path == "/status" && r.Method == "GET" {
			json.NewEncoder(w).Encode(d.status())
			return
		}
		if r.Method != "POST" {
			http.Error(w, `{"error":"method"}`, 405)
			return
		}
		data, err := io.ReadAll(io.LimitReader(r.Body, 300*1024+1))
		if err != nil || len(data) > 300*1024 {
			http.Error(w, `{"error":"Слишком большой запрос"}`, 400)
			return
		}
		mutations.Lock()
		defer mutations.Unlock()
		switch r.URL.Path {
		case "/config":
			err = d.update(data, false)
		case "/vpn":
			err = d.update(data, true)
		case "/test":
			err = d.ask("Представься как голосовой помощник OpenAI одним коротким предложением.")
		case "/stop":
			d.stop()
		default:
			http.Error(w, `{"error":"not found"}`, 404)
			return
		}
		if err != nil {
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		json.NewEncoder(w).Encode(d.status())
	})
}

func serve(dir string) error {
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	if err := os.Chmod(dir, 0700); err != nil {
		return err
	}
	c, err := readConfig(dir)
	if err != nil {
		return err
	}
	sock := filepath.Join(dir, "control.sock")
	_ = os.Remove(sock)
	ln, err := net.Listen("unix", sock)
	if err != nil {
		return err
	}
	defer ln.Close()
	defer os.Remove(sock)
	if err = os.Chmod(sock, 0600); err != nil {
		return err
	}
	d := &Daemon{dir: dir, cfg: c, vpn: &VPN{dir: dir, state: "off"}, endpoint: "https://api.openai.com/v1", play: playPCM, quiet: quietAlice, stage: "idle"}
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer cancel()
	srv := &http.Server{Handler: d.handler(), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 30 * time.Second}
	go srv.Serve(ln)
	go d.watch(ctx, maindLog)
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			d.stop()
			d.vpn.stop()
			return srv.Close()
		case <-ticker.C:
			d.mu.Lock()
			if d.cfg.VPNEnabled {
				d.vpn.ensure(d.cfg)
			}
			d.mu.Unlock()
		}
	}
}

func ctl(dir, action string) error {
	method := "POST"
	var body io.Reader = os.Stdin
	if action == "status" {
		method = "GET"
		body = nil
	}
	tr := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", filepath.Join(dir, "control.sock"))
	}}
	defer tr.CloseIdleConnections()
	c := http.Client{Transport: tr, Timeout: 25 * time.Second}
	req, err := http.NewRequest(method, "http://unix/"+action, body)
	if err != nil {
		return err
	}
	r, err := c.Do(req)
	if err != nil {
		return errors.New("Сервис на колонке не запущен")
	}
	defer r.Body.Close()
	_, err = io.Copy(os.Stdout, io.LimitReader(r.Body, 64*1024))
	return err
}

func main() {
	args := os.Args[1:]
	if len(args) == 1 && args[0] == "version" {
		fmt.Println(version)
		return
	}
	var err error
	if len(args) == 1 && args[0] == "serve" {
		err = serve(defaultDir)
	} else if len(args) == 2 && args[0] == "ctl" {
		err = ctl(defaultDir, args[1])
	} else {
		err = errors.New("usage: station-assistant serve | ctl status/config/vpn/test/stop | version")
	}
	if err != nil {
		b, _ := json.Marshal(map[string]string{"error": err.Error()})
		fmt.Println(string(b))
		os.Exit(1)
	}
}
