package main

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"time"

	"fyne.io/systray"

	"github.com/jameelhamdan/assistant-keypad/host/internal/claudecfg"
	"github.com/jameelhamdan/assistant-keypad/host/internal/config"
	"github.com/jameelhamdan/assistant-keypad/host/internal/core"
	"github.com/jameelhamdan/assistant-keypad/host/internal/ipc"
	"github.com/jameelhamdan/assistant-keypad/host/internal/osutil"
	"github.com/jameelhamdan/assistant-keypad/host/internal/service"
)

var (
	colIdle    = color.RGBA{0x3f, 0xb9, 0x50, 0xff}
	colWorking = color.RGBA{0x3b, 0x82, 0xf6, 0xff}
	colWaiting = color.RGBA{0xe0, 0xa1, 0x1b, 0xff}
	colOff     = color.RGBA{0x8a, 0x93, 0xa0, 0xff}
)

// trayIcon draws the keypad: 2 rows x 4 keys; the top row carries the state color.
func trayIcon(c color.RGBA) []byte {
	const s, key, gap, pad = 44, 8, 3, 3
	img := image.NewRGBA(image.Rect(0, 0, s, s))
	top := (s - (2*key + gap)) / 2
	for row := 0; row < 2; row++ {
		for col := 0; col < 4; col++ {
			x0, y0 := pad+col*(key+gap)+2, top+row*(key+gap)
			fill := c
			if row == 1 {
				fill.A = 0x90
			}
			for y := 0; y < key; y++ {
				for x := 0; x < key; x++ {
					corner := (x == 0 || x == key-1) && (y == 0 || y == key-1)
					if !corner {
						img.Set(x0+x, y0+y, fill)
					}
				}
			}
		}
	}
	var buf bytes.Buffer
	_ = png.Encode(&buf, img)
	if runtime.GOOS != "windows" {
		return buf.Bytes()
	}
	// Windows wants an .ico: a one-image directory wrapping the PNG.
	var ico bytes.Buffer
	_ = binary.Write(&ico, binary.LittleEndian, []uint16{0, 1, 1})
	ico.Write([]byte{s, s, 0, 0})
	_ = binary.Write(&ico, binary.LittleEndian, []uint16{1, 32})
	_ = binary.Write(&ico, binary.LittleEndian, []uint32{uint32(buf.Len()), 22})
	ico.Write(buf.Bytes())
	return ico.Bytes()
}

func runTray() error {
	if !trayLock() {
		// Already running (e.g. the app was opened again): show the settings instead.
		return cmdSettings()
	}
	// First launch of the macOS app: register the login items. launchd then
	// starts the agent and a tray of its own, so this instance steps aside.
	if runtime.GOOS == "darwin" && strings.Contains(selfPath(), ".app/Contents/MacOS/") && !service.Installed() {
		if err := service.Install(selfPath()); err == nil {
			return nil
		}
	}
	systray.Run(trayReady, func() {})
	return nil
}

// welcome opens the settings page once, the first time an agent is reachable.
func welcome(openUI func(string)) {
	marker := filepath.Join(config.Dir(), ".welcomed")
	if _, err := os.Stat(marker); err == nil {
		return
	}
	if os.WriteFile(marker, []byte("1"), 0o600) == nil {
		openUI("")
	}
}

func trayReady() {
	icons := map[color.RGBA][]byte{}
	setIcon := func(c color.RGBA) {
		if icons[c] == nil {
			icons[c] = trayIcon(c)
		}
		systray.SetIcon(icons[c])
	}
	setIcon(colOff)
	systray.SetTooltip("Keypad")

	head := systray.AddMenuItem("Starting…", "")
	head.Disable()
	var kp, ss []*systray.MenuItem
	for range 4 {
		m := systray.AddMenuItem("", "")
		m.Disable()
		m.Hide()
		kp = append(kp, m)
	}
	systray.AddSeparator()
	sessHead := systray.AddMenuItem("Claude Code sessions", "")
	sessHead.Disable()
	for range 6 {
		m := systray.AddMenuItem("", "")
		m.Disable()
		m.Hide()
		ss = append(ss, m)
	}
	systray.AddSeparator()
	mPause := systray.AddMenuItemCheckbox("Pause keypad", "Leave every decision to the PC", false)
	mSettings := systray.AddMenuItem("Settings…", "")
	mAdd := systray.AddMenuItem("Add keypad…", "Pair a keypad plugged in with USB")
	mClaude := systray.AddMenuItemCheckbox("Claude Code integration", "Hooks and MCP server in ~/.claude", false)
	mLogin := systray.AddMenuItemCheckbox("Start at login", "", service.Installed())
	mLogs := systray.AddMenuItem("Open logs folder", "")
	systray.AddSeparator()
	mQuit := systray.AddMenuItem("Quit Keypad", "Stop the agent and the tray")

	bin := selfPath()
	openUI := func(anchor string) {
		var r struct{ URL string }
		if call("GET", "/ui", nil, &r) == nil {
			_ = osutil.Open(r.URL + anchor)
		}
	}
	refreshClaude := func() {
		if claudecfg.Check(bin).Installed() {
			mClaude.Check()
		} else {
			mClaude.Uncheck()
		}
	}
	refreshClaude()

	lastStart := time.Time{}
	update := func() {
		var s core.Snapshot
		if err := call("GET", "/status", nil, &s); err != nil {
			head.SetTitle("Agent not running, starting…")
			setIcon(colOff)
			if time.Since(lastStart) > 10*time.Second {
				lastStart = time.Now()
				_ = service.StartAgent(bin)
			}
			return
		}
		welcome(openUI)
		if s.Paused {
			mPause.Check()
		} else {
			mPause.Uncheck()
		}
		names := map[string]string{}
		for _, d := range s.Devices {
			names[d.ID] = d.Name
		}
		headText := fmt.Sprintf("%d keypads connected", len(s.Keypads))
		switch len(s.Keypads) {
		case 0:
			headText = "No keypad connected"
		case 1:
			headText = "1 keypad connected"
		}
		if s.Paused {
			headText = "Paused, decisions stay on the PC"
		}
		head.SetTitle(headText)
		for i, m := range kp {
			if i < len(s.Keypads) {
				k := s.Keypads[i]
				link := "USB"
				if k.Link == "wifi" {
					link = "Wi-Fi"
				}
				t := fmt.Sprintf("   %s · %s", firstNonEmpty(names[k.ID], k.ID), link)
				if k.Battery > 0 {
					t += fmt.Sprintf(" · %d%%", k.Battery)
				}
				m.SetTitle(t)
				m.Show()
			} else {
				m.Hide()
			}
		}
		if len(s.Sessions) == 0 {
			sessHead.SetTitle("No Claude Code sessions")
		} else {
			sessHead.SetTitle("Claude Code sessions")
		}
		waiting, working := s.Busy, false
		for i, m := range ss {
			if i < len(s.Sessions) {
				x := s.Sessions[i]
				m.SetTitle(fmt.Sprintf("   %s · %s", firstNonEmpty(x.Project, x.ID[:min(8, len(x.ID))]), x.Title))
				m.Show()
			} else {
				m.Hide()
			}
		}
		for _, x := range s.Sessions {
			waiting = waiting || slices.Contains([]string{core.StPermission, core.StQuestion, core.StStopped, core.StInput}, x.State)
			working = working || slices.Contains([]string{core.StThinking, core.StWorking, core.StTool, core.StContinuing}, x.State)
		}
		switch {
		case s.Paused || len(s.Keypads) == 0:
			setIcon(colOff)
		case waiting:
			setIcon(colWaiting)
		case working:
			setIcon(colWorking)
		default:
			setIcon(colIdle)
		}
		systray.SetTooltip("Keypad: " + headText)
	}
	update()

	tick := time.NewTicker(1500 * time.Millisecond)
	for {
		select {
		case <-tick.C:
			update()
		case <-mPause.ClickedCh:
			_ = call("POST", "/pause", map[string]bool{"paused": !mPause.Checked()}, nil)
			update()
		case <-mSettings.ClickedCh:
			openUI("")
		case <-mAdd.ClickedCh:
			openUI("#keypads")
		case <-mClaude.ClickedCh:
			if mClaude.Checked() {
				_ = claudecfg.Uninstall()
			} else {
				cfg, _ := config.Load()
				_ = claudecfg.Install(bin, cfg.Behavior.MaxContinues)
			}
			refreshClaude()
		case <-mLogin.ClickedCh:
			if mLogin.Checked() {
				_ = service.Uninstall()
				mLogin.Uncheck()
			} else if service.Install(bin) == nil {
				mLogin.Check()
			}
		case <-mLogs.ClickedCh:
			_ = osutil.Open(config.LogDir())
		case <-mQuit.ClickedCh:
			if ipc.Alive() {
				_ = call("POST", "/quit", nil, nil)
			}
			systray.Quit()
			return
		}
	}
}

func firstNonEmpty(s ...string) string {
	for _, x := range s {
		if x != "" {
			return x
		}
	}
	return ""
}
