// Package service starts the agent and the tray at login, per user:
// launchd LaunchAgents on macOS, a Task Scheduler task (agent, restarted on
// failure) plus a Run key (tray) on Windows. No administrator rights needed.
package service

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
)

// GUIBinary returns the executable used for background processes. On
// Windows a sibling keypadw.exe (built without a console) is preferred.
func GUIBinary(bin string) string {
	if runtime.GOOS == "windows" {
		w := filepath.Join(filepath.Dir(bin), "keypadw.exe")
		if _, err := os.Stat(w); err == nil {
			return w
		}
	}
	return bin
}

// Spawn starts `bin args...` detached from the current process.
func Spawn(bin string, args ...string) error {
	cmd := exec.Command(GUIBinary(bin), args...)
	detach(cmd)
	return cmd.Start()
}

// Install registers the agent and tray to start at login, and starts them now.
func Install(bin string) error { return install(GUIBinary(bin)) }

// Uninstall removes the login registrations (running processes are left alone).
func Uninstall() error { return uninstall() }

// Installed reports whether the agent is registered to start at login.
func Installed() bool { return installed() }

// StartAgent starts the agent through the service manager, or directly.
func StartAgent(bin string) error {
	if installed() && startRegistered() == nil {
		return nil
	}
	return Spawn(bin, "agent")
}
