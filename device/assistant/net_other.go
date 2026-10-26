//go:build !linux

package main

import (
	"errors"
	"os/exec"
	"syscall"
)

func prepareChild(_ *exec.Cmd) {}

func markSocket(_, _ string, _ syscall.RawConn) error {
	return errors.New("VPN работает на Linux-колонке")
}
