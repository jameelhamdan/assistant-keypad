package service

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"

	"github.com/jameelhamdan/assistant-keypad/host/internal/config"
)

const (
	taskName = `Keypad Agent`
	runKey   = `Software\Microsoft\Windows\CurrentVersion\Run`
	runName  = "Keypad"
)

func hidden(cmd *exec.Cmd) *exec.Cmd {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: windows.CREATE_NO_WINDOW}
	return cmd
}

func taskXML(bin, user string) string {
	esc := func(s string) string {
		return strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;").Replace(s)
	}
	return fmt.Sprintf(`<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Keypad background agent (hardware keypad for Claude Code)</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>%[2]s</UserId></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>%[2]s</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <Hidden>true</Hidden>
  </Settings>
  <Actions Context="Author"><Exec><Command>%[1]s</Command><Arguments>agent</Arguments></Exec></Actions>
</Task>
`, esc(bin), esc(user))
}

func currentUser() string {
	d, u := os.Getenv("USERDOMAIN"), os.Getenv("USERNAME")
	if d != "" {
		return d + `\` + u
	}
	return u
}

func install(bin string) error {
	_ = os.MkdirAll(config.Dir(), 0o700)
	xml := filepath.Join(config.Dir(), "agent-task.xml")
	// Task Scheduler expects UTF-16; the XML declaration says so.
	u16, err := windows.UTF16FromString(taskXML(bin, currentUser()))
	if err != nil {
		return err
	}
	buf := []byte{0xFF, 0xFE}
	for _, c := range u16[:len(u16)-1] {
		buf = append(buf, byte(c), byte(c>>8))
	}
	if err := os.WriteFile(xml, buf, 0o600); err != nil {
		return err
	}
	defer os.Remove(xml)
	if out, err := hidden(exec.Command("schtasks", "/Create", "/TN", taskName, "/XML", xml, "/F")).CombinedOutput(); err != nil {
		return fmt.Errorf("schtasks: %v %s", err, out)
	}
	k, _, err := registry.CreateKey(registry.CURRENT_USER, runKey, registry.SET_VALUE)
	if err != nil {
		return err
	}
	defer k.Close()
	if err := k.SetStringValue(runName, `"`+bin+`" tray`); err != nil {
		return err
	}
	_ = startRegistered()
	return Spawn(bin, "tray")
}

func uninstall() error {
	_ = hidden(exec.Command("schtasks", "/Delete", "/TN", taskName, "/F")).Run()
	if k, err := registry.OpenKey(registry.CURRENT_USER, runKey, registry.SET_VALUE); err == nil {
		_ = k.DeleteValue(runName)
		k.Close()
	}
	return nil
}

func installed() bool {
	return hidden(exec.Command("schtasks", "/Query", "/TN", taskName)).Run() == nil
}

func startRegistered() error {
	return hidden(exec.Command("schtasks", "/Run", "/TN", taskName)).Run()
}

func detach(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true,
		CreationFlags: windows.CREATE_NEW_PROCESS_GROUP | windows.DETACHED_PROCESS}
}
