package main

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestVoiceError(t *testing.T) {
	// Английский текст провайдера не читаем: по частым причинам — понятная фраза, для остального — про панель.
	cases := []struct {
		err  error
		want string
	}{
		{errors.New("модель вернула пустой ответ"), "Модель вернула пустой ответ. Попробуйте спросить ещё раз."},
		{errors.New("не задан адрес API"), "Не задан адрес АПИ ассистента. Укажите его в веб-панели."},
		{errors.New("не задана модель"), "Не выбрана модель ассистента. Укажите её в веб-панели."},
		{errors.New("неверный адрес прокси"), "Неверный адрес прокси. Проверьте его в веб-панели."},
		{errors.New("API ответил 401: Incorrect API key provided: sk-AbC123456789"), "Ключ АПИ не подошёл. Проверьте ключ в веб-панели."},
		{errors.New("API ответил 403: Forbidden"), "Ключ АПИ не подошёл. Проверьте ключ в веб-панели."},
		{errors.New("API ответил 404: model not found"), "Адрес АПИ или модель не найдены. Проверьте настройки в веб-панели."},
		{errors.New("API ответил 429: rate limit"), "Сервер ассистента просит подождать: слишком много запросов. Попробуйте позже."},
		{errors.New("API ответил 503: upstream error"), "Сервер ассистента ответил ошибкой. Попробуйте позже."},
		{errors.New("API ответил 418: teapot"), "Ассистент отклонил запрос. Подробности — в веб-панели."},
		{errors.New("непонятный ответ API"), "Ассистент вернул непонятный ответ. Подробности — в веб-панели."},
		{&url.Error{Op: "Post", URL: "https://api.example.com", Err: errors.New("dial tcp: no route to host")},
			"Нет связи с сервером ассистента. Проверьте интернет и адрес АПИ в веб-панели."},
		{&url.Error{Op: "Post", URL: "https://api.example.com", Err: timeoutErr{}},
			"Ассистент не ответил вовремя. Попробуйте ещё раз."},
		{errors.New("что-то совсем странное"), genericError},
	}
	for _, c := range cases {
		if got := voiceError(c.err); got != c.want {
			t.Errorf("%v → %q, want %q", c.err, got, c.want)
		}
	}
}

type timeoutErr struct{}

func (timeoutErr) Error() string   { return "i/o timeout" }
func (timeoutErr) Timeout() bool   { return true }
func (timeoutErr) Temporary() bool { return true }

func TestChatRejectsEmptyAnswer(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"choices":[{"message":{"content":"   \n"}}]}`)
	}))
	defer srv.Close()
	_, err := chat(context.Background(), LLM{BaseURL: srv.URL, Model: "test"}, []message{{"user", "привет"}})
	if err == nil || !strings.Contains(err.Error(), "пустой") {
		t.Fatalf("пустой ответ модели должен быть ошибкой: %v", err)
	}
}

func TestSpeechRejectsNonWAV(t *testing.T) {
	// Так выглядит недоступный голос ассистента в speak(): текст вернулся не WAV — читаем голосом Алисы.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, "это не wav")
	}))
	defer srv.Close()
	if _, err := speech(context.Background(), TTS{BaseURL: srv.URL, Model: "tts", Voice: "alloy"}, "", "привет"); err == nil {
		t.Fatal("не-WAV должен давать ошибку — по ней speak переходит на Алису")
	}
}

func TestMatchTrigger(t *testing.T) {
	triggers := defaults().Triggers
	for phrase, want := range map[string]string{
		"Спроси gpt сколько будет 2 + 2":      "сколько будет 2 + 2",
		"Спроси GPT, сколько будет 2 + 2?":    "сколько будет 2 + 2?",
		"спроси чат gpt: почему небо голубое": "почему небо голубое",
		"Спроси нейросеть":                    "",
		"Спроси джипити, что такое ёж":        "что такое ёж",
	} {
		got, ok := matchTrigger(phrase, triggers)
		if !ok || got != want {
			t.Errorf("%q → %q, %v; want %q", phrase, got, ok, want)
		}
	}
	for _, phrase := range []string{"включи музыку", "спроси gptfoo почему", "расскажи спроси gpt"} {
		if _, ok := matchTrigger(phrase, triggers); ok {
			t.Errorf("%q не должна срабатывать", phrase)
		}
	}
}

func TestParseLine(t *testing.T) {
	ev, ok := parseLine("[1978:2016] [2026-10-26 17:26:48.127] [sdk] [info] [alice_state_machine.cpp:416] onRequestStateChanged: LISTENING, recognizedPhrase='Спроси gpt сколько будет 2 + 2'")
	if !ok || ev.kind != "phrase" || ev.phrase != "Спроси gpt сколько будет 2 + 2" {
		t.Fatalf("%+v %v", ev, ok)
	}
	// BUSY с той же фразой — не второй вопрос
	if ev, _ := parseLine("onAliceStateChanged: BUSY, recognizedPhrase='Спроси gpt'"); ev.kind == "phrase" {
		t.Fatal("BUSY не должен давать фразу")
	}
	for line, kind := range map[string]string{
		"onPhraseSpotted: алиса {…}":                         "spotted",
		"onAliceStateChanged: SPEAKING, outputSpeech='Окей'": "speaking",
		"onAliceStateChanged: LISTENING":                     "listening",
		"onAliceStateChanged: IDLE":                          "idle",
	} {
		if ev, ok := parseLine(line); !ok || ev.kind != kind {
			t.Errorf("%q → %+v", line, ev)
		}
	}
}

func TestForSpeech(t *testing.T) {
	if got := forSpeech("**Ответ:** «да»\n\n# Итог", 100); got != "Ответ: да Итог" {
		t.Errorf("%q", got)
	}
	long := strings.Repeat("Первое предложение. ", 30)
	got := forSpeech(long, 100)
	if len([]rune(got)) > 100 || !strings.HasSuffix(got, ".") {
		t.Errorf("обрезка по предложению: %q", got)
	}
}

func TestStripThinking(t *testing.T) {
	if got := stripThinking("<think>долго</think>Четыре."); got != "Четыре." {
		t.Errorf("%q", got)
	}
}

func TestKeysPerProvider(t *testing.T) {
	path := t.TempDir() + "/config.json"
	os.WriteFile(path, []byte(`{"llm":{"base_url":"https://api.example.com/v1/","api_key":"old"},
		"tts":{"base_url":"https://tts.example.com/v1"},
		"keys":{"https://api.example.com/v1":"llm-key","https://other.example.com/v1":"other"}}`), 0600)
	c, err := loadConfig(path)
	if err != nil || c.LLM.APIKey != "llm-key" || c.TTS.APIKey != "" {
		t.Fatalf("%v %q %q", err, c.LLM.APIKey, c.TTS.APIKey)
	}
	if c.keyFor("HTTPS://other.example.com/v1/", "") != "other" {
		t.Fatal("адрес с другим регистром и «/» — тот же провайдер")
	}
}

func TestRingFrame(t *testing.T) {
	r, g, b, ok := parseColor("#ff8a00")
	if !ok || r != 0xff || g != 0x8a || b != 0 {
		t.Fatal(r, g, b, ok)
	}
	if _, _, _, ok := parseColor("red"); ok {
		t.Fatal("не цвет")
	}
	f := ringFrame(r, g, b, 1)
	if len(f) != 9 || f[0] != 0 || f[1] != 0x8a || f[2] != 0xff { // B, G, R на каждый из 3 пикселей
		t.Fatalf("% x", f)
	}
	if ringFrame(r, g, b, 0)[2] != 0 {
		t.Fatal("нулевая яркость гасит кольцо")
	}
}

func TestModePhrases(t *testing.T) {
	path := t.TempDir() + "/config.json"
	os.WriteFile(path, []byte(`{"mode":{"follow_up":false}}`), 0600)
	c, _ := loadConfig(path)
	if c.Mode.TimeoutMin != 10 || len(c.Mode.Enter) == 0 || len(c.Mode.Exit) == 0 || c.Mode.FollowUp {
		t.Fatalf("%+v", c.Mode)
	}
	if _, ok := matchTrigger("Переключись на GPT, пожалуйста", c.Mode.Enter); !ok {
		t.Fatal("фраза включения")
	}
	if _, ok := matchTrigger("Верни Алису", c.Mode.Exit); !ok {
		t.Fatal("фраза выключения")
	}
	if _, ok := matchTrigger("включи музыку", c.Mode.Enter); ok {
		t.Fatal("обычная просьба не включает режим")
	}
}

func TestRadioHelpers(t *testing.T) {
	stations := []Station{{Name: "Groove Salad", URL: "http://ice1.somafm.com/groovesalad-128-mp3"}, {Name: "Радио Рекорд", URL: "https://radiorecord.hostingradio.ru/rr_main96.aacp"}}
	for said, want := range map[string]string{"groove salad": "Groove Salad", "грув салад": "Groove Salad", "рекорд": "Радио Рекорд", "радио рекорд": "Радио Рекорд", "погода": ""} {
		st, ok := findStation(said, stations)
		if (want == "") == ok || st.Name != want {
			t.Errorf("%q → %q %v", said, st.Name, ok)
		}
	}
	for u, ok := range map[string]bool{"http://a.b/c.mp3": true, "https://a.b:8000/x?y=1": true, "ftp://a.b": false,
		`http://a.b/"x`: false, "http://a.b/x y": false, "http://a.b/!x": false, "file:///etc/passwd": false} {
		if validStreamURL(u) != ok {
			t.Errorf("%q", u)
		}
	}
	if parseStreamTitle("StreamTitle='Artist - Song';StreamUrl='';\x00\x00") != "Artist - Song" {
		t.Fatal("ICY")
	}
	if !alicePlayback("Включи музыку") || alicePlayback("какая погода") {
		t.Fatal("просьба что-то включить")
	}
}

func TestFatalStreamErrors(t *testing.T) {
	if !fatalStreamError("souphttpsrc0: Bad Request") || fatalStreamError("souphttpsrc0: Internal data stream error.") {
		t.Fatal("400 — не повторяем, обрыв связи — повторяем")
	}
}

func TestAliceMusicStartEvent(t *testing.T) {
	for _, line := range []string{
		"[sdk] [info] [directive_sequencer.cpp:758] routeDirective (xx25101022)alice_request.music_thin_client_on_started via AliceCapability",
		"[sdk] [info] [directive_sequencer.cpp:758] routeDirective (xx922f83e2)audio_play.Glagol via AudioPlayerCapability",
		"[sdk] [info] [directive_sequencer.cpp:758] routeDirective (xx1)audio_player_continue_directive.Glagol via AudioPlayerCapability",
	} {
		if ev, ok := parseLine(line); !ok || ev.kind != "music" {
			t.Errorf("%q → %+v", line, ev)
		}
	}
	if ev, _ := parseLine("routeDirective (xx2)alice_request.music_thin_client_on_stopped via AliceCapability"); ev.kind == "music" {
		t.Fatal("остановка — не начало музыки")
	}
}

func TestSpokenStationNames(t *testing.T) {
	stations := []Station{{Name: "Relax FM Chillout", URL: "http://a/1"}, {Name: "FLUX", URL: "http://a/2"}, {Name: "Lofi", URL: "http://a/3"},
		{Name: "Несуществующая Радиостанция", URL: "http://a/4"}}
	for said, want := range map[string]string{"lo fi": "Lofi", "флакс": "FLUX", "лофи": "Lofi",
		"несуществующей радиостанции": "Несуществующая Радиостанция", "релакс": "Relax FM Chillout", "джаз": ""} {
		st, ok := findStation(said, stations)
		if (want == "") == ok || st.Name != want {
			t.Errorf("%q → %q %v, want %q", said, st.Name, ok, want)
		}
	}
}

func TestStationAliasesAndCatalogNames(t *testing.T) {
	stations := []Station{{Name: "SomaFM Groove Salad (128k MP3)", URL: "http://a/1"},
		{Name: "Deep House Radio", URL: "http://a/2", Aliases: []string{"дип хаус"}}}
	for said, want := range map[string]string{"soma fm groove salad": "SomaFM Groove Salad (128k MP3)",
		"дип хаус": "Deep House Radio", "дипхаус": "Deep House Radio"} {
		if st, ok := findStation(said, stations); !ok || st.Name != want {
			t.Errorf("%q → %q %v", said, st.Name, ok)
		}
	}
}

func TestRadioProxyValidation(t *testing.T) {
	for raw, ok := range map[string]bool{"http://10.0.0.1:3128": true, "http://u:p@proxy.lan:8080": true,
		"socks5://a:1080": false, `http://a"b:1`: false, "http://a b:1": false, "http://u:%22x@a:1": false} {
		if validProxy(raw) != ok {
			t.Errorf("%q", raw)
		}
	}
}

func TestFindScene(t *testing.T) {
	conf := "S|a1|1|voice|спать, пора спать||1|Спать\nS|a2|0|voice|ночь||1|Ночь\nS|a3|1|time|23:00||1|Время\n" +
		"S|a4|1|voice|в будни|12345|1|Будни\n"
	mon := time.Date(2026, 10, 26, 12, 0, 0, 0, time.UTC) // понедельник
	sat := mon.AddDate(0, 0, 5)
	for q, want := range map[string]string{"Спать": "a1", "пора спать!": "a1", "ночь": "", "23:00": "", "спать дальше": ""} {
		sc, ok := findScene(conf, q, mon)
		if (want != "") != ok || sc.id != want {
			t.Errorf("%q → %+v %v, want %q", q, sc, ok, want)
		}
	}
	if _, ok := findScene(conf, "в будни", sat); ok {
		t.Error("сценарий «по будням» не должен срабатывать в субботу")
	}
	if sc, ok := findScene(conf, "в будни", mon); !ok || sc.name != "Будни" {
		t.Errorf("%+v %v", sc, ok)
	}
}

func TestLoadConfigEmptyFile(t *testing.T) {
	p := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(p, []byte(" \n"), 0600); err != nil {
		t.Fatal(err)
	}
	c, err := loadConfig(p)
	if err != nil {
		t.Fatalf("пустой конфиг не должен быть ошибкой: %v", err)
	}
	if len(c.Triggers) == 0 || c.Mode.TimeoutMin != 10 {
		t.Fatal("для пустого конфига не подставились defaults")
	}
}
