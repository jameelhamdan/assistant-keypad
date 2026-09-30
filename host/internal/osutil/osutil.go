// Package osutil wraps the few OS facilities the agent needs: last-input
// time (PC hand-back), dark mode (keypad "system" theme), the current Wi-Fi
// SSID (pairing wizard) and opening URLs/folders.
package osutil

import "time"

// Idle returns the time since the last input of this user session (keyboard
// only on macOS; keyboard and mouse on Windows); ok is false when unknown.
func Idle() (time.Duration, bool) { return idle() }

// DarkMode reports whether the OS appearance is dark.
func DarkMode() bool { return darkMode() }

// SSID returns the current Wi-Fi network name, or "" if unknown.
func SSID() string { return ssid() }

// Open opens a URL or path with the default handler.
func Open(target string) error { return open(target) }
