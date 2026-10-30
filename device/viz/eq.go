package main

import (
	"encoding/binary"
	"encoding/json"
	"math"
	"os"
	"time"
)

// Эквалайзер для Bluetooth-переливания: панель кладёт готовые коэффициенты биквадов в eq.json
// (пресеты и формулы — в server.py), tap применяет их к потоку перед отправкой в bluealsa.
type biquad struct {
	b0, b1, b2, a1, a2 float64
	z1, z2             [2]float64
}

type eqConfig struct {
	Enabled bool `json:"enabled"`
	Filters []struct {
		B0 float64 `json:"b0"`
		B1 float64 `json:"b1"`
		B2 float64 `json:"b2"`
		A1 float64 `json:"a1"`
		A2 float64 `json:"a2"`
	} `json:"filters"`
}

type equalizer struct {
	path    string
	mtime   time.Time
	filters []biquad
}

func (e *equalizer) Reload() {
	st, err := os.Stat(e.path)
	if err != nil {
		e.filters = nil
		return
	}
	if st.ModTime().Equal(e.mtime) {
		return
	}
	e.mtime = st.ModTime()
	data, err := os.ReadFile(e.path)
	if err != nil {
		return
	}
	var cfg eqConfig
	if json.Unmarshal(data, &cfg) != nil || !cfg.Enabled {
		e.filters = nil
		return
	}
	fs := make([]biquad, 0, len(cfg.Filters))
	for _, f := range cfg.Filters {
		fs = append(fs, biquad{b0: f.B0, b1: f.B1, b2: f.B2, a1: f.A1, a2: f.A2})
	}
	e.filters = fs
}

func (b *biquad) step(x float64, ch int) float64 {
	y := b.b0*x + b.z1[ch]
	b.z1[ch] = b.b1*x - b.a1*y + b.z2[ch]
	b.z2[ch] = b.b2*x - b.a2*y
	return y
}

// Process — PCM 16 бит стерео: фильтры к обоим каналам. false — фильтров нет, буфер не трогаем.
func (e *equalizer) Process(buf []byte) ([]byte, bool) {
	if len(e.filters) == 0 {
		return buf, false
	}
	out := make([]byte, len(buf))
	for i := 0; i+4 <= len(buf); i += 4 {
		l := float64(int16(binary.LittleEndian.Uint16(buf[i:])))
		r := float64(int16(binary.LittleEndian.Uint16(buf[i+2:])))
		for f := range e.filters {
			l = e.filters[f].step(l, 0)
			r = e.filters[f].step(r, 1)
		}
		binary.LittleEndian.PutUint16(out[i:], uint16(clip16(l)))
		binary.LittleEndian.PutUint16(out[i+2:], uint16(clip16(r)))
	}
	return out, true
}

func clip16(v float64) int16 {
	switch {
	case v >= 32767:
		return 32767
	case v <= -32768:
		return -32768
	default:
		return int16(math.Round(v))
	}
}
