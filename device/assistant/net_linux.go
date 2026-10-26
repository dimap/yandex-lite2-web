//go:build linux

package main

import (
	"os/exec"
	"syscall"
)

// The supervisor may restart us after a crash. Do not leave an orphan VPN or
// audio player holding the tunnel/device in that case.
func prepareChild(cmd *exec.Cmd) { cmd.SysProcAttr = &syscall.SysProcAttr{Pdeathsig: syscall.SIGKILL} }

func markSocket(_, _ string, raw syscall.RawConn) error {
	var socketErr error
	err := raw.Control(func(fd uintptr) {
		socketErr = syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, syscall.SO_MARK, 51887)
	})
	if err != nil {
		return err
	}
	return socketErr
}
