//go:build !windows

package main

import (
	"os"
	"path/filepath"
	"syscall"

	"github.com/jameelhamdan/assistant-keypad/host/internal/config"
)

// trayLock returns false if another tray already runs for this user.
func trayLock() bool {
	_ = os.MkdirAll(config.Dir(), 0o700)
	f, err := os.OpenFile(filepath.Join(config.Dir(), "tray.lock"), os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return true
	}
	if syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		f.Close()
		return false
	}
	return true // f stays open (and locked) for the life of the process
}
