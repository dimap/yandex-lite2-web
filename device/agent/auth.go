package main

import (
	"bufio"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"os"
	"strings"
	"sync"
	"time"
)

// tokens — хэши токенов панелей. Файл перечитывается, когда меняется: панель добавляет себя, не перезапуская агента.
type tokens struct {
	path   string
	mu     sync.Mutex
	mod    time.Time
	size   int64
	hashes [][]byte
}

func newTokens(path string) *tokens { return &tokens{path: path} }

func (t *tokens) reload() {
	st, err := os.Stat(t.path)
	if err != nil {
		t.hashes, t.mod, t.size = nil, time.Time{}, 0
		return
	}
	if st.ModTime().Equal(t.mod) && st.Size() == t.size {
		return
	}
	f, err := os.Open(t.path)
	if err != nil {
		return
	}
	defer f.Close()
	var hs [][]byte
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		fields := strings.Fields(sc.Text())
		if len(fields) == 0 {
			continue
		}
		if h, err := hex.DecodeString(fields[0]); err == nil && len(h) == sha256.Size {
			hs = append(hs, h)
		}
	}
	t.hashes, t.mod, t.size = hs, st.ModTime(), st.Size()
}

// valid сверяет sha256 токена со всеми известными за постоянное время.
func (t *tokens) valid(token string) bool {
	if len(token) < 32 {
		return false
	}
	sum := sha256.Sum256([]byte(token))
	t.mu.Lock()
	defer t.mu.Unlock()
	t.reload()
	ok := 0
	for _, h := range t.hashes {
		ok |= subtle.ConstantTimeCompare(sum[:], h)
	}
	return ok == 1
}
