package main

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// Дочерний процесс имитирует только stdin-протокол CLI. Ни maind, ни adb не запускаются.
func TestAliceCLIProcessHelper(t *testing.T) {
	if os.Getenv("PANEL_TEST_ALICE_HELPER") != "1" {
		return
	}
	f, err := os.OpenFile(os.Getenv("PANEL_TEST_ALICE_LOG"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		os.Exit(9)
	}
	defer f.Close()
	id := os.Getenv("PANEL_TEST_ALICE_ID")
	fmt.Fprintf(f, "%s:start\n", id)
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		line := scanner.Text()
		fmt.Fprintf(f, "%s:%s\n", id, line)
		if line == "x" {
			if os.Getenv("PANEL_TEST_ALICE_FAIL") == "1" {
				os.Exit(7)
			}
			return
		}
	}
}

func fakeAliceCLI(t *testing.T) (*aliceCLI, string, *atomic.Int32) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "commands")
	count := &atomic.Int32{}
	a := &aliceCLI{timing: &cliTiming{start: 10 * time.Millisecond, line: time.Millisecond, speech: 10 * time.Millisecond, exit: 2 * time.Second}}
	a.command = func(ctx context.Context) *exec.Cmd {
		cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestAliceCLIProcessHelper$")
		cmd.Env = append(os.Environ(), "PANEL_TEST_ALICE_HELPER=1", "PANEL_TEST_ALICE_LOG="+path,
			fmt.Sprintf("PANEL_TEST_ALICE_ID=%d", count.Add(1)), "GORACE=atexit_sleep_ms=0")
		return cmd
	}
	t.Cleanup(a.Close)
	return a, path, count
}

func waitCLIEvent(t *testing.T, path, event string) string {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		raw, _ := os.ReadFile(path)
		if strings.Contains(string(raw), event) {
			return string(raw)
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("CLI event %q missing", event)
	return ""
}

func TestAliceSpeechUsesFreshSessionsAndKeepsControlWarm(t *testing.T) {
	a, path, count := fakeAliceCLI(t)
	if err := a.Warm(); err != nil {
		t.Fatal(err)
	}
	for _, text := range []string{"первый ответ", "второй ответ"} {
		if err := a.Say(context.Background(), text); err != nil {
			t.Fatal(err)
		}
	}
	if err := a.Listen(); err != nil {
		t.Fatal(err)
	}
	got := waitCLIEvent(t, path, "1:s\n")
	for _, want := range []string{"2:q\n2:Повтори за мной: первый ответ\n2:x\n", "3:q\n3:Повтори за мной: второй ответ\n3:x\n"} {
		if !strings.Contains(got, want) {
			t.Fatalf("protocol mismatch: %s", got)
		}
	}
	if count.Load() != 3 {
		t.Fatalf("control CLI restarted: %d processes", count.Load())
	}
}

func TestAliceCancelDoesNotWaitForSpeechDelay(t *testing.T) {
	a, path, _ := fakeAliceCLI(t)
	a.timing.speech = 10 * time.Second
	if err := a.Warm(); err != nil {
		t.Fatal(err)
	}
	speechDone := make(chan error, 1)
	go func() { speechDone <- a.Say(context.Background(), "длинный ответ") }()
	waitCLIEvent(t, path, "2:Повтори за мной: длинный ответ\n")
	cancelDone := make(chan error, 1)
	go func() { cancelDone <- a.Cancel() }()
	select {
	case err := <-cancelDone:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("Cancel blocked behind speech")
	}
	waitCLIEvent(t, path, "1:c\n")
	select {
	case err := <-speechDone:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("speech cancellation: %v", err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("speech process not reaped after cancellation")
	}
}

func TestAliceCanceledQuestionNeverStartsSpeech(t *testing.T) {
	a, _, count := fakeAliceCLI(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := a.Say(ctx, "устаревший ответ"); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	if count.Load() != 0 {
		t.Fatal("canceled speech started a process")
	}
}

func TestAliceSpeechReportsProcessFailureAndCanRetry(t *testing.T) {
	a, _, _ := fakeAliceCLI(t)
	factory := a.command
	a.command = func(ctx context.Context) *exec.Cmd {
		cmd := factory(ctx)
		cmd.Env = append(cmd.Env, "PANEL_TEST_ALICE_FAIL=1")
		return cmd
	}
	if err := a.Say(context.Background(), "ошибка"); err == nil {
		t.Fatal("failed CLI reported success")
	}
	a.command = factory
	if err := a.Say(context.Background(), "повтор"); err != nil {
		t.Fatal(err)
	}
}

func TestAliceControlRestartsAfterExit(t *testing.T) {
	a, path, count := fakeAliceCLI(t)
	if err := a.send("x"); err != nil {
		t.Fatal(err)
	}
	waitCLIEvent(t, path, "1:x\n")
	select {
	case <-a.done:
	case <-time.After(3 * time.Second):
		t.Fatal("control CLI not reaped")
	}
	if err := a.Cancel(); err != nil {
		t.Fatal(err)
	}
	waitCLIEvent(t, path, "2:c\n")
	if count.Load() != 2 {
		t.Fatalf("process count: %d", count.Load())
	}
}
