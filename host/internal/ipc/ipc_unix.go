//go:build !windows

package ipc

import (
	"context"
	"errors"
	"net"
	"os"
	"path/filepath"

	"github.com/jameelhamdan/assistant-keypad/host/internal/config"
)

func sockPath() string { return filepath.Join(config.Dir(), "agent.sock") }

func dial(ctx context.Context) (net.Conn, error) {
	var d net.Dialer
	return d.DialContext(ctx, "unix", sockPath())
}

func listen() (net.Listener, error) {
	p := sockPath()
	if err := os.MkdirAll(filepath.Dir(p), 0o700); err != nil {
		return nil, err
	}
	if Alive() {
		return nil, errors.New("another agent is already running")
	}
	_ = os.Remove(p) // stale socket from a crashed agent
	l, err := net.Listen("unix", p)
	if err != nil {
		return nil, err
	}
	if err := os.Chmod(p, 0o600); err != nil {
		l.Close()
		return nil, err
	}
	return l, nil
}
