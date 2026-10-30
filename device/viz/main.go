// vizd — живой спектр звука колонки для визуализатора панели.
//
// Захватывает не микрофон, а то, что реально играет усилитель: на Лайт 2 это кодек hw:0,0 (TDM-B),
// его захват повторяет выходной сигнал (проверено: тихий тон виден в захвате, в тишине — цифровой ноль;
// микрофон PDM остаётся Алисе). Считает FFT и раз в 50 мс пишет 4 полосы в /tmp/webpanel_viz.json.
// Панель читает их тем же потоком, что и светодиоды, и кормит визуализатор.
package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"math"
	"os"
	"os/exec"
	"os/signal"
	"runtime"
	"runtime/debug"
	"syscall"
	"time"
)

var version = "dev"

const (
	statusPath  = "/tmp/webpanel_viz.json"
	sampleRate  = 48000
	fftSize     = 2048
	bandsCount  = 4
	writeEvery  = 50 * time.Millisecond
	statusLimit = 8 << 20
	// Автоматическая громкость: опорный уровень (дБ) по самой громкой полосе, насколько он опускается
	// за кадр, какой диапазон дБ считаем динамикой и ниже какого уровня — цифровая тишина.
	aFloor    = -85.0
	refDecay  = 0.45
	refRange  = 30.0
	silenceDb = -80.0
)

// Полосы — поровну по логарифмической шкале 40 Гц … 16 кГц: визуализатору нужны четыре направления.
var bandEdges = func() [bandsCount + 1]float64 {
	var e [bandsCount + 1]float64
	lo, hi := 40.0, 16000.0
	for i := range e {
		e[i] = lo * math.Pow(hi/lo, float64(i)/float64(bandsCount))
	}
	return e
}()

type status struct {
	Pid     int       `json:"pid"`
	Version string    `json:"version"`
	At      int64     `json:"t"`
	Bands   []float64 `json:"bands,omitempty"`
	RMS     float64   `json:"rms,omitempty"`
	Peak    float64   `json:"peak,omitempty"`
	Error   string    `json:"error,omitempty"`
}

func writeStatus(st status) {
	b, err := json.Marshal(st)
	if err != nil {
		return
	}
	tmp := statusPath + ".tmp"
	if os.WriteFile(tmp, b, 0600) == nil {
		os.Rename(tmp, statusPath)
	}
}

// analyzer — окно Ханна и FFT на 2048 точек: таблицы готовим один раз, чтобы в цикле не мусорить.
type analyzer struct {
	n            int
	window       []float64
	re, im       []float64
	cos, sin     []float64
	rev          []int
	env          [bandsCount]float64
	ref          float64 // медленный опорный уровень (дБ) по самой громкой полосе: по нему считаем динамику
	binLo, binHi [bandsCount]int
	binHz        float64
}

func newAnalyzer(n int, rate float64) *analyzer {
	a := &analyzer{n: n, window: make([]float64, n), re: make([]float64, n), im: make([]float64, n),
		cos: make([]float64, n/2), sin: make([]float64, n/2), rev: make([]int, n), binHz: rate / float64(n)}
	bits := 0
	for 1<<bits < n {
		bits++
	}
	for i := 0; i < n; i++ {
		a.window[i] = 0.5 - 0.5*math.Cos(2*math.Pi*float64(i)/float64(n-1))
		a.rev[i] = reverseBits(i, bits)
	}
	a.ref = aFloor
	for i := 0; i < n/2; i++ {
		a.cos[i] = math.Cos(-2 * math.Pi * float64(i) / float64(n))
		a.sin[i] = math.Sin(-2 * math.Pi * float64(i) / float64(n))
	}
	half := n / 2
	for b := 0; b < bandsCount; b++ {
		lo := int(bandEdges[b] / a.binHz)
		hi := int(bandEdges[b+1] / a.binHz)
		if lo < 1 {
			lo = 1
		}
		if hi <= lo {
			hi = lo + 1
		}
		if hi > half {
			hi = half
		}
		a.binLo[b], a.binHi[b] = lo, hi
	}
	return a
}

func reverseBits(v, bits int) int {
	var r int
	for i := 0; i < bits; i++ {
		r = r<<1 | v&1
		v >>= 1
	}
	return r
}

// fft — итеративная радикс-2 по месту.
func (a *analyzer) fft() {
	n := a.n
	for i := 0; i < n; i++ {
		if j := a.rev[i]; j > i {
			a.re[i], a.re[j] = a.re[j], a.re[i]
			a.im[i], a.im[j] = a.im[j], a.im[i]
		}
	}
	for size := 2; size <= n; size <<= 1 {
		half, step := size>>1, n/size
		for start := 0; start < n; start += size {
			for k := 0; k < half; k++ {
				idx := k * step
				wr, wi := a.cos[idx], a.sin[idx]
				i, j := start+k, start+k+half
				tr := wr*a.re[j] - wi*a.im[j]
				ti := wr*a.im[j] + wi*a.re[j]
				a.re[j], a.im[j] = a.re[i]-tr, a.im[i]-ti
				a.re[i], a.im[i] = a.re[i]+tr, a.im[i]+ti
			}
		}
	}
}

// bands считает четыре полосы 0…1 (сглаженные) и RMS/пик окна.
func (a *analyzer) bands(samples []int16) (out [bandsCount]float64, rms, peak float64) {
	// Постоянную составляющую убираем взвешенно по окну: если вычесть обычное среднее, окно Ханна само
	// «подсветит» остаток DC (в захвате кодека в тишине он ровно −1 LSB, но и нецелое число периодов тона
	// даёт смещение). Взвешенное среднее делает нулевой бин ровно нулём.
	var sw, sxw float64
	for i, s := range samples {
		sw += a.window[i]
		sxw += float64(s) * a.window[i]
	}
	dc := sxw / sw
	var sum float64
	for i, s := range samples {
		v := float64(s) - dc
		sum += v * v
		if av := math.Abs(v); av > peak {
			peak = av
		}
		a.re[i] = v * a.window[i]
		a.im[i] = 0
	}
	rms = math.Sqrt(sum/float64(len(samples))) / 32768
	peak /= 32768
	a.fft()
	// Сначала все полосы в дБ: общий опорный уровень считаем по самой громкой из них, чтобы тихая
	// громкость колонки не влияла — иначе при тихом прослушивании сигнал пропадал.
	var dbs [bandsCount]float64
	maxDb := -200.0
	for b := 0; b < bandsCount; b++ {
		// Максимум по бинам, а не среднее: среднее по сотням бинов верхней полосы размывало узкий тон
		// так, что он не проходил порог. Для визуализатора важна самая громкая составляющая полосы.
		var amp float64
		for k := a.binLo[b]; k < a.binHi[b]; k++ {
			if m := 2 * math.Hypot(a.re[k], a.im[k]) / float64(a.n); m > amp {
				amp = m
			}
		}
		// дБ относительно полной шкалы 32768: без этого полосы всегда были «громче нуля» и упирались в 1.
		dbs[b] = 20 * math.Log10(amp/32768+1e-9)
		if dbs[b] > maxDb {
			maxDb = dbs[b]
		}
	}
	// Опорный уровень: поднимается сразу, опускается медленно (~9 дБ/с). Ниже него — тишина, выше —
	// динамика refRange дБ. Абсолютный порог только для настоящей цифровой тишины (−1 LSB ≈ −90 дБ).
	if maxDb > a.ref {
		a.ref = maxDb
	} else if a.ref -= refDecay; a.ref < aFloor {
		a.ref = aFloor
	}
	silent := maxDb < silenceDb
	for b := 0; b < bandsCount; b++ {
		v := 0.0
		if !silent {
			v = math.Max(0, math.Min(1, (dbs[b]-(a.ref-refRange))/refRange))
		}
		// быстрая атака и достаточно быстрое затухание — иначе визуализатор «отстаёт» от музыки
		k := 0.7
		if v < a.env[b] {
			k = 0.3
		}
		a.env[b] += (v - a.env[b]) * k
		out[b] = math.Round(a.env[b]*1000) / 1000
	}
	return out, rms, peak
}

// run открывает захват и крутит анализ, пока не отменят контекст или не отвалится arecord.
func run(ctx context.Context, device string) error {
	cmd := exec.CommandContext(ctx, "arecord", "-q", "-D", device, "-f", "S16_LE", "-c", "2",
		"-r", "48000", "-t", "raw", "--buffer-time=100000")
	cmd.Stderr = os.Stderr
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	if err := cmd.Start(); err != nil {
		return err
	}
	defer func() {
		cmd.Process.Kill()
		cmd.Wait()
	}()

	a := newAnalyzer(fftSize, sampleRate)
	history := make([]int16, fftSize)
	hop := fftSize   // без перекрытия: панель всё равно читает 10 раз в секунду, а FFT — вдвое дешевле
	hopBuf := make([]int16, 0, hop)
	buf := make([]byte, 16384)
	r := bufio.NewReaderSize(stdout, 1<<16)
	lastWrite := time.Time{}
	var st status
	for ctx.Err() == nil {
		n, readErr := r.Read(buf)
		for i := 0; i+4 <= n; i += 4 {
			l := int32(int16(binary.LittleEndian.Uint16(buf[i:])))
			rr := int32(int16(binary.LittleEndian.Uint16(buf[i+2:])))
			hopBuf = append(hopBuf, int16((l+rr)/2))
			if len(hopBuf) < hop {
				continue
			}
			copy(history, history[hop:])
			copy(history[fftSize-hop:], hopBuf)
			hopBuf = hopBuf[:0]
			bands, rms, peak := a.bands(history)
			if time.Since(lastWrite) < writeEvery {
				continue
			}
			lastWrite = time.Now()
			st = status{Pid: os.Getpid(), Version: version, At: lastWrite.UnixMilli(),
				Bands: append([]float64(nil), bands[:]...), RMS: math.Round(rms*1000) / 1000,
				Peak: math.Round(peak*1000) / 1000}
			writeStatus(st)
		}
		if readErr != nil {
			if readErr == io.EOF || readErr == io.ErrUnexpectedEOF {
				return fmt.Errorf("arecord завершился")
			}
			return readErr
		}
	}
	return ctx.Err()
}

// runFile — отладочный режим: прогнать WAV (16 бит, стерео, 48 кГц) без колонки и напечатать полосы.
func runFile(path string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	off := bytes.Index(data, []byte("data"))
	if off < 0 || off+8 > len(data) {
		return fmt.Errorf("в %s нет data-чанка", path)
	}
	a := newAnalyzer(fftSize, sampleRate)
	history := make([]int16, fftSize)
	hopBuf := make([]int16, 0, fftSize/2)
	for i, hop := off+8, 0; i+4 <= len(data); i += 4 {
		l := int32(int16(binary.LittleEndian.Uint16(data[i:])))
		rr := int32(int16(binary.LittleEndian.Uint16(data[i+2:])))
		hopBuf = append(hopBuf, int16((l+rr)/2))
		if len(hopBuf) < fftSize/2 {
			continue
		}
		copy(history, history[fftSize/2:])
		copy(history[fftSize/2:], hopBuf)
		hopBuf = hopBuf[:0]
		bands, rms, peak := a.bands(history)
		hop++
		fmt.Printf("%4d  %5.2f %5.2f %5.2f %5.2f  rms %.3f peak %.3f\n",
			hop, bands[0], bands[1], bands[2], bands[3], rms, peak)
	}
	return nil
}

func main() {
	device := flag.String("device", "hw:0,0", "ALSA-устройство захвата (то, что играет усилитель)")
	file := flag.String("file", "", "отладка: прогнать WAV (16 бит, стерео, 48 кГц) и напечатать полосы")
	show := flag.Bool("version", false, "показать версию")
	flag.Parse()
	if *show {
		fmt.Println(version)
		return
	}
	if *file != "" {
		if err := runFile(*file); err != nil {
			log.Fatal(err)
		}
		return
	}
	runtime.GOMAXPROCS(1)
	debug.SetGCPercent(50)
	debug.SetMemoryLimit(statusLimit)
	log.SetFlags(log.Ldate | log.Ltime)
	log.Printf("vizd %s запущен, захват %s", version, *device)
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	defer os.Remove(statusPath)
	backoff := 2 * time.Second
	for {
		start := time.Now()
		err := run(ctx, *device)
		if ctx.Err() != nil {
			return
		}
		log.Printf("захват остановился: %v", err)
		writeStatus(status{Pid: os.Getpid(), Version: version, At: time.Now().UnixMilli(), Error: err.Error()})
		// Открытие захвата перенастраивает общий TDM-интерфейс кодека — это слышно коротким щелчком.
		// Поэтому неудачную попытку (занято, звук ещё не поднялся) не долбим каждые 2 с: пауза растёт.
		if time.Since(start) > 10*time.Second {
			backoff = 2 * time.Second
		} else if backoff < 30*time.Second {
			backoff *= 2
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
	}
}
