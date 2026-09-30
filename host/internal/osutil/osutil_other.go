//go:build !darwin && !windows

package osutil

import (
	"os/exec"
	"time"
)

func idle() (time.Duration, bool) { return 0, false }
func darkMode() bool              { return true }
func ssid() string                { return "" }
func open(target string) error    { return exec.Command("xdg-open", target).Start() }
