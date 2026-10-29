// yapanel-agent — защищённый канал между панелью и колонкой: команды, файлы и потоки по TLS с токеном.
// adb остаётся как был: через него панель ставит и чинит агента, а если агент не отвечает — работает напрямую.
//
// В папке агента: cert.pem и key.pem — свой самоподписанный сертификат (панель знает его по отпечатку,
// прочитанному по adb при установке), tokens — sha256 токенов панелей по строке «хэш имя».
package main

import (
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"runtime/debug"
	"time"
)

var version = "dev"

// build — начало sha256 файла агента: панель сравнивает его со своей сборкой и обновляет агента,
// даже если номер версии тот же (сборка из изменённых исходников).
var build string

func exeHash() string {
	exe, err := os.Executable()
	if err != nil {
		return ""
	}
	data, err := os.ReadFile(exe)
	if err != nil {
		return ""
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])[:16]
}

func main() {
	dir := flag.String("dir", "/data/quasar/webpanel/agent", "папка агента: сертификат и токены")
	addr := flag.String("addr", ":7861", "адрес и порт")
	showVersion := flag.Bool("version", false, "показать версию")
	flag.Parse()
	if *showVersion {
		fmt.Println(version)
		return
	}
	log.SetFlags(log.LstdFlags)
	// Памяти на колонке мало (свободно ~30 МБ): один поток для Go-кода, сборка мусора чаще, потолок кучи —
	// и раз в полминуты отдаём системе то, что освободилось после всплеска (большой файл, длинный вывод).
	runtime.GOMAXPROCS(1)
	debug.SetGCPercent(25)
	debug.SetMemoryLimit(6 << 20)
	go func() {
		for range time.Tick(30 * time.Second) {
			debug.FreeOSMemory()
		}
	}()
	cert, err := loadOrCreateCert(filepath.Join(*dir, "cert.pem"), filepath.Join(*dir, "key.pem"))
	if err != nil {
		log.Fatalf("сертификат: %v", err)
	}
	build = exeHash()
	a := &agent{tokens: newTokens(filepath.Join(*dir, "tokens")), started: time.Now()}
	srv := &http.Server{
		Addr:              *addr,
		Handler:           a.routes(),
		ReadHeaderTimeout: 15 * time.Second,
		TLSConfig:         &tls.Config{MinVersion: tls.VersionTLS12, Certificates: []tls.Certificate{cert}},
		ErrorLog:          log.New(os.Stderr, "http: ", log.LstdFlags),
		// только HTTP/1.1: WebSocket перехватывает соединение, а панели HTTP/2 не нужен
		TLSNextProto: map[string]func(*http.Server, *tls.Conn, http.Handler){},
	}
	log.Printf("yapanel-agent %s слушает %s", version, *addr)
	log.Fatal(srv.ListenAndServeTLS("", ""))
}
