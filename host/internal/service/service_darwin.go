package service

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"

	"github.com/jameelhamdan/assistant-keypad/host/internal/config"
)

const (
	agentLabel = "com.jameelhamdan.keypad.agent"
	trayLabel  = "com.jameelhamdan.keypad.tray"
)

func plistPath(label string) string {
	h, _ := os.UserHomeDir()
	return filepath.Join(h, "Library", "LaunchAgents", label+".plist")
}

func domain() string { return "gui/" + strconv.Itoa(os.Getuid()) }

func plist(label, bin, arg string) string {
	esc := func(s string) string {
		return strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;").Replace(s)
	}
	logf := esc(filepath.Join(config.LogDir(), arg+".launchd.log"))
	return fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>%s</string>
	<key>ProgramArguments</key><array><string>%s</string><string>%s</string></array>
	<key>RunAtLoad</key><true/>
	<key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
	<key>ThrottleInterval</key><integer>5</integer>
	<key>ProcessType</key><string>Interactive</string>
	<key>LimitLoadToSessionType</key><string>Aqua</string>
	<key>StandardErrorPath</key><string>%s</string>
</dict>
</plist>
`, label, esc(bin), arg, logf)
}

func install(bin string) error {
	_ = os.MkdirAll(config.LogDir(), 0o700)
	for label, arg := range map[string]string{agentLabel: "agent", trayLabel: "tray"} {
		p := plistPath(label)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			return err
		}
		_ = exec.Command("launchctl", "bootout", domain()+"/"+label).Run()
		if err := os.WriteFile(p, []byte(plist(label, bin, arg)), 0o644); err != nil {
			return err
		}
		if out, err := exec.Command("launchctl", "bootstrap", domain(), p).CombinedOutput(); err != nil {
			return fmt.Errorf("launchctl bootstrap %s: %v %s", label, err, out)
		}
	}
	return nil
}

func uninstall() error {
	for _, label := range []string{trayLabel, agentLabel} {
		_ = exec.Command("launchctl", "bootout", domain()+"/"+label).Run()
		if err := os.Remove(plistPath(label)); err != nil && !os.IsNotExist(err) {
			return err
		}
	}
	return nil
}

func installed() bool {
	_, err := os.Stat(plistPath(agentLabel))
	return err == nil
}

func startRegistered() error {
	return exec.Command("launchctl", "kickstart", domain()+"/"+agentLabel).Run()
}

func detach(cmd *exec.Cmd) { cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true} }
