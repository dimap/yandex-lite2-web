package main

import (
	"encoding/binary"
	"math"
	"os"
	"testing"
	"time"
)

// Пик +6 дБ на 1 кГц: амплитуда синуса на этой частоте должна вырасти примерно вдвое.
func TestPeakingBoost(t *testing.T) {
	const fs = 48000.0
	const f0, gain, q = 1000.0, 6.0, 1.0
	a := math.Pow(10, gain/40)
	w := 2 * math.Pi * f0 / fs
	alpha := math.Sin(w) / (2 * q)
	b0 := 1 + alpha*a
	b1 := -2 * math.Cos(w)
	b2 := 1 - alpha*a
	a0 := 1 + alpha/a
	a1 := -2 * math.Cos(w)
	a2 := 1 - alpha/a
	eq := &equalizer{filters: []biquad{{b0: b0 / a0, b1: b1 / a0, b2: b2 / a0, a1: a1 / a0, a2: a2 / a0}}}

	const n = 48000
	src := make([]byte, n*4)
	for i := 0; i < n; i++ {
		v := int16(1000 * math.Sin(2*math.Pi*f0*float64(i)/fs))
		binary.LittleEndian.PutUint16(src[i*4:], uint16(v))
		binary.LittleEndian.PutUint16(src[i*4+2:], uint16(v))
	}
	out, ok := eq.Process(src)
	if !ok {
		t.Fatal("фильтр не применился")
	}
	peak := 0.0
	for i := n - 9600; i < n; i++ {
		v := math.Abs(float64(int16(binary.LittleEndian.Uint16(out[i*4:]))))
		if v > peak {
			peak = v
		}
	}
	if math.Abs(peak-2000) > 100 {
		t.Fatalf("амплитуда %.0f, ожидали ~2000", peak)
	}
}

func TestNoFiltersIsPassThrough(t *testing.T) {
	eq := &equalizer{}
	src := []byte{1, 0, 2, 0, 3, 0, 4, 0}
	out, ok := eq.Process(src)
	if ok || &out[0] != &src[0] {
		t.Fatal("без фильтров буфер должен уходить как есть")
	}
}

func TestReload(t *testing.T) {
	p := t.TempDir() + "/eq.json"
	if err := os.WriteFile(p, []byte(`{"enabled":true,"filters":[{"b0":1,"b1":0,"b2":0,"a1":0,"a2":0}]}`), 0644); err != nil {
		t.Fatal(err)
	}
	eq := &equalizer{path: p}
	eq.Reload()
	if len(eq.filters) != 1 {
		t.Fatalf("фильтров %d, ожидали 1", len(eq.filters))
	}
	if err := os.WriteFile(p, []byte(`{"enabled":false,"filters":[]}`), 0644); err != nil {
		t.Fatal(err)
	}
	eq.mtime = time.Time{} // на всякий случай: время изменения может совпасть
	eq.Reload()
	if len(eq.filters) != 0 {
		t.Fatal("выключенный эквалайзер должен снимать фильтры")
	}
}
