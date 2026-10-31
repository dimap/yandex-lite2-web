package main

import (
	"math"
	"testing"
)

func sine(freq float64, n int) []int16 {
	return sineAmp(freq, 20000, n)
}

func sineAmp(freq, amp float64, n int) []int16 {
	out := make([]int16, n)
	for i := range out {
		out[i] = int16(amp * math.Sin(2*math.Pi*freq*float64(i)/sampleRate))
	}
	return out
}

// settle прогоняет окно несколько раз: у полос сглаживание, с первого кадра они не успевают вырасти.
func settle(a *analyzer, samples []int16) (out [bandsCount]float64, rms, peak float64) {
	for i := 0; i < 12; i++ {
		out, rms, peak = a.bands(samples)
	}
	return
}

func TestBandsPutToneInTheRightBand(t *testing.T) {
	cases := []struct {
		freq float64
		want int
	}{{100, 0}, {500, 1}, {1500, 2}, {8000, 3}}
	for _, c := range cases {
		a := newAnalyzer(fftSize, sampleRate)
		out, rms, _ := settle(a, sine(c.freq, fftSize))
		best := 0
		for i := range out {
			if out[i] > out[best] {
				best = i
			}
		}
		if best != c.want || out[best] < 0.7 {
			t.Errorf("%.0f Гц → полосы %v (лучшая %d), ждали %d; rms=%.3f", c.freq, out, best, c.want, rms)
		}
	}
}

func TestSilenceAndLevelsAreInRange(t *testing.T) {
	a := newAnalyzer(fftSize, sampleRate)
	out, rms, peak := settle(a, make([]int16, fftSize))
	if rms != 0 || peak != 0 {
		t.Fatalf("тишина: rms=%v peak=%v", rms, peak)
	}
	for i, v := range out {
		if v != 0 {
			t.Fatalf("тишина: полоса %d = %v", i, v)
		}
	}
	// Захват кодека в тишине — не нули, а −1 LSB: постоянная составляющая не должна «зажигать» низкую полосу.
	dc := make([]int16, fftSize)
	for i := range dc {
		dc[i] = -1
	}
	out, rms, peak = settle(newAnalyzer(fftSize, sampleRate), dc)
	if rms != 0 || peak != 0 {
		t.Fatalf("постоянная составляющая: rms=%v peak=%v", rms, peak)
	}
	for i, v := range out {
		if v != 0 {
			t.Fatalf("постоянная составляющая: полоса %d = %v", i, v)
		}
	}
	// Полная шкала не должна вылезать за 0…1 ни в одной полосе.
	out, _, peak = settle(newAnalyzer(fftSize, sampleRate), func() []int16 {
		s := make([]int16, fftSize)
		for i := range s {
			v := int16(32000 * math.Sin(2*math.Pi*440*float64(i)/sampleRate))
			s[i] = v
		}
		return s
	}())
	for i, v := range out {
		if v < 0 || v > 1 {
			t.Fatalf("полоса %d = %v", i, v)
		}
	}
	if peak < 0.9 || peak > 1 {
		t.Fatalf("пик полной шкалы = %v", peak)
	}
}

func TestAgcLiftsQuietSignalAndShowsDrop(t *testing.T) {
	// Тихая музыка не должна пропадать: опорный уровень общий, поэтому громкость прослушивания не важна.
	a := newAnalyzer(fftSize, sampleRate)
	out, _, _ := settle(a, sineAmp(500, 300, fftSize))
	best := bestBand(out)
	if out[best] < 0.8 {
		t.Fatalf("тихий тон не поднялся: %v", out)
	}
	// Совсем тихо (−70 дБ от полной шкалы) — тоже видно.
	out, _, _ = settle(newAnalyzer(fftSize, sampleRate), sineAmp(500, 10, fftSize))
	if out[bestBand(out)] < 0.8 {
		t.Fatalf("очень тихий тон не поднялся: %v", out)
	}
	// А после громкого тот же тон на 20 дБ тише — уже заметно ниже: в этом и смысл динамики.
	a = newAnalyzer(fftSize, sampleRate)
	settle(a, sineAmp(500, 20000, fftSize))
	out, _, _ = settle(a, sineAmp(500, 2000, fftSize))
	best = bestBand(out)
	if out[best] > 0.6 || out[best] < 0.1 {
		t.Fatalf("после громкого тихий тон: %v (полоса %d)", out, best)
	}
}

func TestLoudAndQuietPlaybackLookTheSame(t *testing.T) {
	// Один и тот же сигнал на разной громкости колонки должен давать одинаковые полосы.
	loud, _, _ := settle(newAnalyzer(fftSize, sampleRate), sineAmp(500, 20000, fftSize))
	quiet, _, _ := settle(newAnalyzer(fftSize, sampleRate), sineAmp(500, 200, fftSize))
	for i := range loud {
		if math.Abs(loud[i]-quiet[i]) > 0.1 {
			t.Fatalf("громкость влияет на полосы: громко %v, тихо %v", loud, quiet)
		}
	}
}

func bestBand(out [bandsCount]float64) int {
	best := 0
	for i := range out {
		if out[i] > out[best] {
			best = i
		}
	}
	return best
}

func TestFFTMatchesDirectSum(t *testing.T) {
	// Одна ненулевая точка: спектр должен быть плоским, а нулевая гармоника — равна ей.
	a := newAnalyzer(8, 48000)
	a.re[3], a.im[3] = 1, 0
	a.fft()
	for k := range a.re {
		if mag := math.Hypot(a.re[k], a.im[k]); math.Abs(mag-1) > 1e-9 {
			t.Fatalf("бин %d: %v", k, mag)
		}
	}
}
