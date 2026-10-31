package main

// Связь с Алисой без её облака: отладочный CLI maind (отмена диалога, текстовый запрос) и журнал maind.log
// (распознанная фраза, состояние Алисы). Строки журнала — как в прошивке Лайт 2 0.369:
//   onPhraseSpotted: алиса …
//   onRequestStateChanged: LISTENING, recognizedPhrase='Спроси gpt сколько будет 2 + 2'
//   onAliceStateChanged: SPEAKING, outputSpeech='…'

import (
	"io"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"
	"unicode"
)

const quasar = "/system/vendor/quasar"

// aliceCLI держит один maind --cli открытым: так отмена срабатывает за доли секунды, а не за время запуска.
type aliceCLI struct {
	mu  sync.Mutex
	cmd *exec.Cmd
	in  io.WriteCloser
}

func (a *aliceCLI) ensure() error {
	if a.cmd != nil && a.cmd.ProcessState == nil {
		return nil
	}
	cmd := exec.Command(quasar+"/maind", "--cli")
	cmd.Env = append(os.Environ(), "LD_LIBRARY_PATH="+quasar+"/libs")
	in, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	if err = cmd.Start(); err != nil {
		return err
	}
	a.cmd, a.in = cmd, in
	go cmd.Wait() // заполняет ProcessState: следующий вызов запустит CLI заново
	// Пауза как в server.py say(): если начать писать раньше, CLI не успевает подняться
	// и первая же строка теряется — Алиса вместо текста отвечает «мда».
	time.Sleep(2 * time.Second)
	return nil
}

func (a *aliceCLI) send(lines ...string) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	if err := a.ensure(); err != nil {
		return err
	}
	for i, line := range lines {
		if i > 0 {
			time.Sleep(500 * time.Millisecond)
		}
		if _, err := io.WriteString(a.in, line+"\n"); err != nil {
			a.cmd.Process.Kill()
			return err
		}
	}
	return nil
}

// Warm запускает CLI заранее: на отмену у нас около секунды, а сам запуск CLI занимает полторы.
func (a *aliceCLI) Warm() error { return a.send() }

// Listen открывает микрофон Алисы, как будто её позвали: так продолжают беседу без «Алисы».
func (a *aliceCLI) Listen() error { return a.send("s") }

// PausePlayer ставит на паузу плеер Алисы («Моя волна», треки) — перед своим радио.
func (a *aliceCLI) PausePlayer() error { return a.send("o") }

// Cancel — то же, что «Алиса, хватит»: обрывает текущий диалог и ответ из облака.
func (a *aliceCLI) Cancel() error { return a.send("c") }

// Say — Алиса читает текст своим голосом («Окей, повторяю: …»).
// Say — текстовый запрос «Повтори за мной». После ответа выходим из CLI ('x'), как это делает
// панель в server.py say(): если держать сессию открытой, следующий 'q' Алиса уже не считает
// новым запросом и вместо чтения текста отвечает «мда»/«браво».
func (a *aliceCLI) Say(text string) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	if err := a.ensure(); err != nil {
		return err
	}
	if _, err := io.WriteString(a.in, "q\n"); err != nil {
		a.cmd.Process.Kill()
		return err
	}
	time.Sleep(500 * time.Millisecond)
	if _, err := io.WriteString(a.in, "Повтори за мной: "+text+"\n"); err != nil {
		a.cmd.Process.Kill()
		return err
	}
	time.Sleep(3 * time.Second)
	io.WriteString(a.in, "x\n")
	a.in.Close()
	return nil
}

func (a *aliceCLI) Close() {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.cmd != nil && a.cmd.ProcessState == nil {
		io.WriteString(a.in, "x\n")
		time.Sleep(300 * time.Millisecond)
		a.cmd.Process.Kill()
	}
}

// logEvent — то, что нам нужно из строки журнала.
type logEvent struct {
	kind   string // spotted, phrase, speaking, listening, idle, music
	phrase string
}

func parseLine(line string) (logEvent, bool) {
	switch {
	case strings.Contains(line, "onPhraseSpotted:"):
		return logEvent{kind: "spotted"}, true
	case strings.Contains(line, "onRequestStateChanged: LISTENING, recognizedPhrase='"):
		s := line[strings.Index(line, "recognizedPhrase='")+len("recognizedPhrase='"):]
		if i := strings.LastIndex(s, "'"); i >= 0 {
			s = s[:i]
		}
		return logEvent{kind: "phrase", phrase: strings.TrimSpace(s)}, true
	case strings.Contains(line, "onAliceStateChanged: SPEAKING"):
		return logEvent{kind: "speaking"}, true
	case strings.Contains(line, "onAliceStateChanged: LISTENING"):
		return logEvent{kind: "listening"}, true
	case strings.Contains(line, "onAliceStateChanged: IDLE"):
		return logEvent{kind: "idle"}, true
	// Алиса начинает свою музыку — из приложения, панели, голосом или продолжая после паузы
	case strings.Contains(line, "music_thin_client_on_started"),
		strings.Contains(line, "routeDirective (") && (strings.Contains(line, ")audio_play") || strings.Contains(line, ")audio_player_continue")):
		return logEvent{kind: "music"}, true
	}
	return logEvent{}, false
}

func normalize(s string) string {
	s = strings.ReplaceAll(strings.ToLower(s), "ё", "е")
	return strings.Join(strings.FieldsFunc(s, func(r rune) bool { return !unicode.IsLetter(r) && !unicode.IsDigit(r) }), " ")
}

// matchTrigger: «Спроси GPT, сколько будет 2 + 2» → «сколько будет 2 + 2». Вопрос берём из исходной фразы,
// чтобы не потерять «+» и прочие знаки.
func matchTrigger(phrase string, triggers []string) (string, bool) {
	norm := normalize(phrase)
	for _, t := range triggers {
		t = normalize(t)
		if t == "" || (norm != t && !strings.HasPrefix(norm, t+" ")) {
			continue
		}
		words := strings.Fields(phrase)
		n := len(strings.Fields(t))
		// слова триггера в исходной фразе могут прилипать к знакам («gpt,») — считаем по словам нормализованной
		kept, seen := []string{}, 0
		for _, w := range words {
			if seen < n {
				seen += len(strings.Fields(normalize(w)))
				continue
			}
			kept = append(kept, w)
		}
		return strings.TrimLeft(strings.Join(kept, " "), " ,.:;!?—-"), true
	}
	return "", false
}

// forSpeech готовит ответ модели для «Повтори за мной»: одна строка, без разметки и кавычек, не длиннее limit.
func forSpeech(s string, limit int) string {
	s = strings.NewReplacer("*", "", "_", " ", "#", "", "`", "", "«", "", "»", "", "\"", "", "'", "", ">", "").Replace(s)
	s = strings.Join(strings.Fields(s), " ")
	r := []rune(s)
	if len(r) <= limit {
		return s
	}
	cut := string(r[:limit])
	if i := strings.LastIndexAny(cut, ".!?"); i > limit/2 {
		return cut[:i+1]
	}
	if i := strings.LastIndex(cut, " "); i > 0 {
		return cut[:i] + "…"
	}
	return cut
}
