package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

const version = "0.1.0"
const defaultDir = "/data/quasar/webpanel/assistant"
const maindLog = "/data/quasar/daemons_logs/maind.log"

type Config struct {
	Enabled      bool   `json:"enabled"`
	APIKey       string `json:"api_key"`
	Model        string `json:"model"`
	Voice        string `json:"voice"`
	Prefix       string `json:"prefix"`
	Instructions string `json:"instructions"`
	VPNEnabled   bool   `json:"vpn_enabled"`
	VPNConfig    string `json:"vpn_config"`
	VPNUser      string `json:"vpn_user"`
	VPNPassword  string `json:"vpn_password"`
	VPNDNS       string `json:"vpn_dns"`
}

func defaults() Config {
	return Config{Model: "gpt-5.6-terra", Voice: "coral", Prefix: "спроси gpt", VPNDNS: "1.1.1.1",
		Instructions: "Отвечай по-русски кратко, обычно двумя-тремя предложениями. Ответ будет озвучен: без Markdown. Ты голосовой помощник на базе OpenAI."}
}

func atomicWrite(path string, data []byte) error {
	f, err := os.CreateTemp(filepath.Dir(path), ".write-")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if _, err = f.Write(data); err == nil {
		err = f.Sync()
	}
	if closeErr := f.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	return os.Rename(f.Name(), path)
}

func readConfig(dir string) (Config, error) {
	c := defaults()
	b, err := os.ReadFile(filepath.Join(dir, "config.json"))
	if os.IsNotExist(err) {
		return c, nil
	}
	if err != nil {
		return c, errors.New("Не удалось прочитать настройки")
	}
	if json.Unmarshal(b, &c) != nil {
		return c, errors.New("Повреждены настройки сервиса")
	}
	return c, nil
}

func saveConfig(dir string, c Config) error {
	b, err := json.Marshal(c)
	if err != nil {
		return err
	}
	return atomicWrite(filepath.Join(dir, "config.json"), b)
}

var modelID = regexp.MustCompile(`^[a-zA-Z0-9._:-]{1,100}$`)
var phrases = regexp.MustCompile(`onAliceStateChanged: BUSY, recognizedPhrase='(.*)'`)

// Only final ASR events are eligible. LISTENING contains partial hypotheses.
func voiceQuery(line, prefix string) (string, bool) {
	m := phrases.FindStringSubmatch(line)
	if len(m) != 2 {
		return "", false
	}
	phrase := strings.TrimSpace(m[1])
	prefix = normalize(prefix)
	variants := []string{prefix}
	if prefix == "спроси gpt" {
		variants = append(variants, "спроси джипити", "спроси джи пи ти", "спроси джипи ти", "спроси чат gpt", "спроси чат джипити")
	}
	for _, p := range variants {
		words := strings.Fields(p)
		for i, w := range words {
			words[i] = regexp.QuoteMeta(w)
		}
		re := regexp.MustCompile(`(?i)^\s*(?:алиса[\s,.:!?—-]+)?` + strings.Join(words, `[\s,.:!?—-]+`) + `(?:[\s,.:!?—-]+(.*)|$)`)
		if found := re.FindStringSubmatch(phrase); found != nil {
			return strings.TrimSpace(found[1]), true
		}
	}
	return "", false
}

func normalize(s string) string {
	s = strings.ToLower(strings.ReplaceAll(s, "ё", "е"))
	return strings.Join(strings.Fields(strings.Map(func(r rune) rune {
		if strings.ContainsRune(",.!?:;—-«»\"", r) {
			return ' '
		}
		return r
	}, s)), " ")
}
