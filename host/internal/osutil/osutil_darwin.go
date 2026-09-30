package osutil

/*
#cgo LDFLAGS: -framework CoreGraphics
#include <CoreGraphics/CoreGraphics.h>
// Keyboard only: moving the mouse near the keypad should not count as "at the PC".
static double secondsSinceInput(void) {
	return CGEventSourceSecondsSinceLastEventType(kCGEventSourceStateCombinedSessionState, kCGEventKeyDown);
}
*/
import "C"

import (
	"os/exec"
	"strings"
	"time"
)

func idle() (time.Duration, bool) {
	s := float64(C.secondsSinceInput())
	if s < 0 {
		return 0, false
	}
	return time.Duration(s * float64(time.Second)), true
}

func darkMode() bool {
	out, err := exec.Command("defaults", "read", "-g", "AppleInterfaceStyle").Output()
	return err == nil && strings.Contains(string(out), "Dark")
}

func ssid() string {
	for _, dev := range []string{"en0", "en1"} {
		out, err := exec.Command("ipconfig", "getsummary", dev).Output()
		if err != nil {
			continue
		}
		for _, line := range strings.Split(string(out), "\n") {
			k, v, ok := strings.Cut(strings.TrimSpace(line), " : ")
			if ok && k == "SSID" && v != "<redacted>" {
				return v
			}
		}
	}
	return ""
}

func open(target string) error { return exec.Command("open", target).Start() }
