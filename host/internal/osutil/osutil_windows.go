package osutil

import (
	"os/exec"
	"strings"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

var (
	user32           = windows.NewLazySystemDLL("user32.dll")
	kernel32         = windows.NewLazySystemDLL("kernel32.dll")
	getLastInputInfo = user32.NewProc("GetLastInputInfo")
	getTickCount     = kernel32.NewProc("GetTickCount")
)

type lastInputInfo struct {
	cbSize uint32
	dwTime uint32
}

func idle() (time.Duration, bool) {
	lii := lastInputInfo{cbSize: uint32(unsafe.Sizeof(lastInputInfo{}))}
	if r, _, _ := getLastInputInfo.Call(uintptr(unsafe.Pointer(&lii))); r == 0 {
		return 0, false
	}
	now, _, _ := getTickCount.Call()
	return time.Duration(uint32(now)-lii.dwTime) * time.Millisecond, true
}

func darkMode() bool {
	k, err := registry.OpenKey(registry.CURRENT_USER, `Software\Microsoft\Windows\CurrentVersion\Themes\Personalize`, registry.QUERY_VALUE)
	if err != nil {
		return false
	}
	defer k.Close()
	v, _, err := k.GetIntegerValue("AppsUseLightTheme")
	return err == nil && v == 0
}

func ssid() string {
	cmd := exec.Command("netsh", "wlan", "show", "interfaces")
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	out, err := cmd.Output()
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(out), "\n") {
		k, v, ok := strings.Cut(line, ":")
		if ok && strings.TrimSpace(k) == "SSID" {
			return strings.TrimSpace(v)
		}
	}
	return ""
}

func open(target string) error {
	return exec.Command("rundll32", "url.dll,FileProtocolHandler", target).Start()
}
