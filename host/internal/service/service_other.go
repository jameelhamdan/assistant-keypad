//go:build !darwin && !windows

package service

import (
	"errors"
	"os/exec"
	"syscall"
)

func install(bin string) error { return errors.New("start at login is supported on macOS and Windows") }
func uninstall() error         { return nil }
func installed() bool          { return false }
func startRegistered() error   { return errors.New("not supported") }
func detach(cmd *exec.Cmd)     { cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true} }
