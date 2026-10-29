package main

import (
	"os"
	"os/exec"
	"syscall"
	"time"
)

type proc struct{ cmd *exec.Cmd }

// newProc — sh -c в своей группе процессов: потом гасим её целиком, вместе с детьми команды.
func newProc(command string) *proc {
	c := exec.Command("/bin/sh", "-c", command)
	c.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	c.Dir = "/"
	return &proc{cmd: c}
}

func (p *proc) kill() {
	if p.cmd.Process == nil {
		return
	}
	pg := -p.cmd.Process.Pid
	_ = syscall.Kill(pg, syscall.SIGHUP)
	_ = syscall.Kill(pg, syscall.SIGTERM)
	go func() {
		time.Sleep(2 * time.Second)
		_ = syscall.Kill(pg, syscall.SIGKILL)
	}()
}

// exitCode — как в sh: убит сигналом — 128 + номер.
func exitCode(st *os.ProcessState) int {
	if st == nil {
		return -1
	}
	if ws, ok := st.Sys().(syscall.WaitStatus); ok && ws.Signaled() {
		return 128 + int(ws.Signal())
	}
	return st.ExitCode()
}
