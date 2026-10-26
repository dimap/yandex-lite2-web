package main

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"unicode"
)

// OpenVPN accepts quoted arguments and comments. Parse them as data, then
// serialize each token ourselves: quoting never bypasses the directive allowlist.
func ovpnFields(line string) ([]string, error) {
	var fields []string
	var word strings.Builder
	var quote rune
	escaped, started := false, false
	flush := func() {
		if started {
			fields = append(fields, word.String())
			word.Reset()
			started = false
		}
	}
	for _, r := range line {
		if escaped {
			word.WriteRune(r)
			escaped = false
			started = true
			continue
		}
		if r == '\\' {
			escaped = true
			started = true
			continue
		}
		if quote != 0 {
			if r == quote {
				quote = 0
			} else {
				word.WriteRune(r)
			}
			continue
		}
		if r == '"' || r == '\'' {
			quote = r
			started = true
			continue
		}
		if unicode.IsSpace(r) {
			flush()
			continue
		}
		if (r == '#' || r == ';') && !started {
			break
		}
		word.WriteRune(r)
		started = true
	}
	if quote != 0 || escaped {
		return nil, errors.New("Незакрытые кавычки или escape")
	}
	flush()
	return fields, nil
}

func ovpnLine(fields []string) string {
	quoted := make([]string, len(fields))
	for i, s := range fields {
		if s == "" || strings.ContainsAny(s, " \t\\\"'#;") {
			s = "\"" + strings.NewReplacer("\\", "\\\\", "\"", "\\\"").Replace(s) + "\""
		}
		quoted[i] = s
	}
	return strings.Join(quoted, " ")
}

// Configs are data, never executable hooks. Unknown directives fail closed.
func sanitizeOVPN(src string) (string, bool, error) {
	if len(src) > 256*1024 || strings.ContainsRune(src, 0) {
		return "", false, errors.New("Слишком большой или повреждённый .ovpn")
	}
	allowed := map[string]bool{}
	for _, k := range strings.Fields("client proto remote port nobind persist-key persist-tun resolv-retry remote-random cipher data-ciphers data-ciphers-fallback auth tls-version-min tls-version-max tls-cipher tls-ciphersuites key-direction verify-x509-name connect-retry connect-retry-max connect-timeout server-poll-timeout ping ping-restart reneg-sec explicit-exit-notify tun-mtu mssfix sndbuf rcvbuf mute-replay-warnings auth-nocache") {
		allowed[k] = true
	}
	blocks := map[string]bool{"ca": true, "cert": true, "key": true, "tls-auth": true, "tls-crypt": true, "tls-crypt-v2": true}
	seen := map[string]bool{}
	var out []string
	block, remote, needAuth := "", false, false
	for n, raw := range strings.Split(strings.ReplaceAll(src, "\r", ""), "\n") {
		line := strings.TrimSpace(raw)
		if block != "" {
			if line == "</"+block+">" {
				block = ""
			} else if strings.ContainsAny(line, "<>") {
				return "", false, fmt.Errorf("Некорректный inline-блок, строка %d", n+1)
			}
			out = append(out, line)
			continue
		}
		if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, ";") {
			continue
		}
		if strings.HasPrefix(line, "<") {
			name := strings.TrimSuffix(strings.TrimPrefix(line, "<"), ">")
			if !blocks[name] || line != "<"+name+">" || seen[name] {
				return "", false, fmt.Errorf("Неподдерживаемый inline-блок, строка %d", n+1)
			}
			seen[name] = true
			block = name
			out = append(out, line)
			continue
		}
		fields, err := ovpnFields(line)
		if err != nil {
			return "", false, fmt.Errorf("Незакрытые кавычки или escape в строке %d", n+1)
		}
		if len(fields) == 0 {
			continue
		}
		key := fields[0]
		switch key {
		case "dev":
			if len(fields) != 2 || fields[1] != "tun" {
				return "", false, errors.New("Поддерживаются только профили OpenVPN tun")
			}
			continue
		case "dev-type":
			if len(fields) != 2 || fields[1] != "tun" {
				return "", false, errors.New("Требуется dev-type tun")
			}
			continue
		case "auth-user-pass":
			if len(fields) != 1 {
				return "", false, errors.New("Логин и пароль VPN укажите в панели, без внешнего файла")
			}
			needAuth = true
			continue
		case "remote-cert-tls":
			if len(fields) != 2 || fields[1] != "server" {
				return "", false, errors.New("Требуется проверка сертификата сервера")
			}
			continue
		case "verb", "mute", "redirect-gateway", "route", "route-ipv6", "route-metric", "route-delay", "dhcp-option", "register-dns", "block-outside-dns":
			continue
		case "setenv":
			if strings.Join(fields, " ") == "setenv opt block-outside-dns" {
				continue
			}
		case "pull-filter":
			// DNS is handled by our marked resolver. Keep ignore filters only;
			// an accept filter could take precedence over our own route filters.
			if len(fields) == 3 && fields[1] == "ignore" &&
				(fields[2] == "dns" || fields[2] == "dhcp-option" || strings.HasPrefix(fields[2], "dhcp-option ")) {
				out = append(out, ovpnLine(fields))
				continue
			}
		case "comp-lzo":
			if len(fields) == 2 && fields[1] == "no" {
				continue
			}
		case "allow-compression":
			if len(fields) == 2 && fields[1] == "no" {
				continue
			}
		}
		if !allowed[key] {
			return "", false, fmt.Errorf("Неподдерживаемая директива .ovpn в строке %d; нужны inline-сертификаты без скриптов и плагинов", n+1)
		}
		if key == "remote" {
			if len(fields) < 2 || len(fields) > 4 {
				return "", false, errors.New("Неверный remote в .ovpn")
			}
			remote = true
		}
		out = append(out, ovpnLine(fields))
	}
	if block != "" || !remote || !seen["ca"] || seen["cert"] != seen["key"] {
		return "", false, errors.New("Нужен .ovpn с remote, встроенным <ca> и парой <cert>/<key>, если используется сертификат клиента")
	}
	// route-nopull prevents changes to the speaker's network, DNS and LAN routes.
	out = append(out, "client", "dev webgpt0", "dev-type tun", "remote-cert-tls server", "route-nopull", "script-security 1", "auth-nocache", "allow-compression no", "verb 3", "pull-filter ignore redirect-gateway", "pull-filter ignore route-ipv6", "pull-filter ignore ifconfig-ipv6",
		// Let dual-transport profiles try TCP before our 45-second watchdog fires.
		"server-poll-timeout 10", "connect-retry 2 5")
	return strings.Join(out, "\n") + "\n", needAuth, nil
}

type VPN struct {
	mu        sync.Mutex
	cancel    context.CancelFunc
	done      chan struct{}
	connected bool
	state     string
	message   string
	lastStart time.Time
	dir       string
}

func (v *VPN) status() (bool, string, string) {
	v.mu.Lock()
	defer v.mu.Unlock()
	return v.connected, v.state, v.message
}
func (v *VPN) set(state, msg string, connected bool) {
	v.mu.Lock()
	defer v.mu.Unlock()
	v.state = state
	v.message = msg
	v.connected = connected
}
func ipOutput(args ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	bin := "/opt/sbin/ip"
	if _, err := os.Stat(bin); err != nil {
		bin = "ip"
	}
	return exec.CommandContext(ctx, bin, args...).Output()
}
func ip(args ...string) error { _, err := ipOutput(args...); return err }
func clearRoutes() {
	_ = ip("rule", "del", "priority", "31887", "fwmark", "51887", "table", "51887")
	_ = ip("route", "flush", "table", "51887")
}
func vpnPolicy() error {
	// Keep the blackhole and rule across reconnects, including in-flight requests.
	if err := ip("route", "replace", "blackhole", "default", "metric", "32767", "table", "51887"); err != nil {
		return err
	}
	rules, err := ipOutput("rule", "show")
	if err != nil {
		return err
	}
	for _, line := range strings.Split(string(rules), "\n") {
		if strings.HasPrefix(line, "31887:") {
			if strings.Contains(line, "fwmark 0xcaaf") && strings.Contains(line, "lookup 51887") {
				return nil
			}
			return errors.New("routing priority already in use")
		}
	}
	return ip("rule", "add", "priority", "31887", "fwmark", "51887", "table", "51887")
}
func vpnRoutes() error {
	return ip("route", "replace", "default", "dev", "webgpt0", "metric", "10", "table", "51887")
}
func (v *VPN) stop() {
	v.mu.Lock()
	cancel, done := v.cancel, v.done
	v.mu.Unlock()
	if cancel != nil {
		cancel()
		<-done
	}
	clearRoutes()
	v.set("off", "", false)
}

func (v *VPN) ensure(c Config) {
	v.mu.Lock()
	if v.cancel != nil || time.Since(v.lastStart) < 30*time.Second {
		v.mu.Unlock()
		return
	}
	v.lastStart = time.Now()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	v.cancel = cancel
	v.done = done
	v.mu.Unlock()
	go func() {
		defer func() {
			_ = ip("route", "del", "default", "dev", "webgpt0", "metric", "10", "table", "51887")
			v.mu.Lock()
			v.connected = false
			v.cancel = nil
			v.lastStart = time.Now() // Keep failures visible during the retry delay.
			close(done)
			v.mu.Unlock()
		}()
		profile, auth, err := sanitizeOVPN(c.VPNConfig)
		if err != nil {
			v.set("error", err.Error(), false)
			return
		}
		if auth {
			if c.VPNUser == "" || c.VPNPassword == "" {
				v.set("error", "Заполните логин и пароль VPN", false)
				return
			}
			if err = atomicWrite(filepath.Join(v.dir, "vpn.auth"), []byte(c.VPNUser+"\n"+c.VPNPassword+"\n")); err != nil {
				v.set("error", "Не удалось записать пароль VPN", false)
				return
			}
			profile += "auth-user-pass " + filepath.Join(v.dir, "vpn.auth") + "\n"
		}
		if err = atomicWrite(filepath.Join(v.dir, "vpn.ovpn"), []byte(profile)); err != nil {
			v.set("error", "Не удалось записать профиль VPN", false)
			return
		}
		defer os.Remove(filepath.Join(v.dir, "vpn.auth"))
		defer os.Remove(filepath.Join(v.dir, "vpn.ovpn"))
		bin := "/opt/sbin/openvpn"
		if _, err = os.Stat(bin); err != nil {
			v.set("error", "Установите OpenVPN кнопкой в панели", false)
			return
		}
		cmd := exec.CommandContext(ctx, bin, "--config", filepath.Join(v.dir, "vpn.ovpn"))
		prepareChild(cmd)
		if err = vpnPolicy(); err != nil {
			v.set("error", "Прошивка не поддерживает маршрутизацию VPN для OpenAI", false)
			return
		}
		cmd.WaitDelay = 3 * time.Second
		stdout, err := cmd.StdoutPipe()
		if err != nil {
			return
		}
		cmd.Stderr = cmd.Stdout
		v.set("connecting", "Подключение к Red Shield VPN", false)
		if err = cmd.Start(); err != nil {
			v.set("error", "Не удалось запустить OpenVPN", false)
			return
		}
		timer := time.AfterFunc(45*time.Second, func() {
			v.set("error", "VPN не подключился за 45 секунд; проверьте профиль", false)
			cancel()
		})
		defer timer.Stop()
		scanner := bufio.NewScanner(stdout)
		for scanner.Scan() {
			line := scanner.Text()
			if strings.Contains(line, "Initialization Sequence Completed") {
				timer.Stop()
				if err = vpnRoutes(); err != nil {
					v.set("error", "Прошивка не поддерживает маршрутизацию VPN для OpenAI", false)
					cancel()
				} else {
					v.set("connected", "Только запросы OpenAI идут через VPN", true)
				}
			} else if strings.Contains(line, "AUTH_FAILED") {
				v.set("error", "Red Shield отклонил логин или пароль", false)
				cancel()
			} else if strings.Contains(line, "Attempting to establish TCP connection") {
				v.set("connecting", "Пробуем TCP-соединение с Red Shield VPN", false)
			} else if strings.Contains(line, "UDP link remote:") {
				v.set("connecting", "Пробуем UDP-соединение с Red Shield VPN", false)
			} else if strings.Contains(line, "VERIFY ERROR") {
				v.set("error", "Не удалось проверить сертификат VPN-сервера", false)
				cancel()
			} else if strings.Contains(line, "RESOLVE: Cannot resolve host address") {
				v.set("error", "Не удалось определить адрес VPN-сервера; проверьте интернет", false)
			} else if strings.Contains(line, "Restart pause") || strings.Contains(line, "SIGUSR1") {
				v.set("connecting", "Переподключение VPN", false)
			} else if strings.Contains(line, "Options error") || strings.Contains(line, "Exiting due to fatal error") {
				v.set("error", "OpenVPN отклонил настройки; проверьте совместимость .ovpn", false)
			}
		}
		_ = cmd.Wait()
		_, state, _ := v.status()
		if state != "error" {
			v.set("error", "Соединение VPN завершилось; повтор через 30 секунд", false)
		}
	}()
}

func validCredentials(c Config) error {
	if net.ParseIP(c.VPNDNS) == nil || strings.Contains(c.VPNDNS, ":") {
		return errors.New("DNS VPN должен быть IPv4-адресом")
	}
	if len(c.VPNUser) > 512 || len(c.VPNPassword) > 512 || strings.ContainsAny(c.VPNUser+c.VPNPassword, "\r\n\x00") {
		return errors.New("Некорректные учётные данные VPN")
	}
	return nil
}
