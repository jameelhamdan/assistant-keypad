package main

import (
	"context"
	"flag"
	"io"
	"log/slog"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/jameelhamdan/assistant-keypad/host/internal/claudecfg"
	"github.com/jameelhamdan/assistant-keypad/host/internal/config"
	"github.com/jameelhamdan/assistant-keypad/host/internal/core"
	"github.com/jameelhamdan/assistant-keypad/host/internal/device"
	"github.com/jameelhamdan/assistant-keypad/host/internal/firmware"
	"github.com/jameelhamdan/assistant-keypad/host/internal/ipc"
	"github.com/jameelhamdan/assistant-keypad/host/internal/osutil"
	"github.com/jameelhamdan/assistant-keypad/host/internal/server"
)

func init() { claudecfg.FindClaude = core.FindClaude }

func selfPath() string {
	p, err := os.Executable()
	if err != nil {
		return os.Args[0]
	}
	if r, err := filepath.EvalSymlinks(p); err == nil {
		p = r
	}
	// hooks must run the console build on Windows
	return strings.Replace(p, "keypadw.exe", "keypad.exe", 1)
}

func openLog(level string) (*slog.Logger, func()) {
	_ = os.MkdirAll(config.LogDir(), 0o700)
	path := filepath.Join(config.LogDir(), "agent.log")
	if st, err := os.Stat(path); err == nil && st.Size() > 5<<20 {
		_ = os.Rename(path, path+".1")
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	var w io.Writer = os.Stderr
	if err == nil {
		w = io.MultiWriter(os.Stderr, f)
	}
	lv := slog.LevelInfo
	if level == "debug" {
		lv = slog.LevelDebug
	}
	return slog.New(slog.NewTextHandler(w, &slog.HandlerOptions{Level: lv})), func() {
		if f != nil {
			f.Close()
		}
	}
}

func runAgent(args []string) error {
	fs := flag.NewFlagSet("agent", flag.ExitOnError)
	fake := fs.String("fake-device", "", "attach a simulated keypad that answers: first|allow|deny|continue|done|pc|none (testing only)")
	_ = fs.Parse(args)

	cfg, cfgErr := config.Load()
	log, closeLog := openLog(cfg.LogLevel)
	defer closeLog()
	if cfgErr != nil {
		log.Warn("config problem, using defaults where needed", "err", cfgErr)
	}
	store, err := config.OpenStore()
	if err != nil {
		return err
	}
	l, err := ipc.Listen()
	if err != nil {
		return err
	}
	defer l.Close()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	host, _ := os.Hostname()
	host, _, _ = strings.Cut(host, ".")
	theme := func() string {
		if osutil.DarkMode() {
			return "dark"
		}
		return "light"
	}
	agent := core.NewAgent(cfg, store, osutil.Idle, log)
	hub := device.NewHub(store, agent, device.Options{HostName: host, Theme: theme, Log: log})
	agent.SetHub(hub)

	srv := &server.Server{A: agent, Bin: selfPath(), Firmware: firmware.Image, Log: log, Quit: stop}
	go func() {
		if err := srv.ServeIPC(l); err != nil && ctx.Err() == nil {
			log.Error("ipc server stopped", "err", err)
			stop()
		}
	}()
	go agent.Run(ctx)
	go hub.Run(ctx)
	go watch(ctx, agent, hub, store, theme)

	if *fake != "" {
		f := device.NewFake("kp-fake01")
		switch *fake {
		case "first":
			f.Policy = device.FirstKey
		case "none":
		default:
			f.Policy = device.PressAct(*fake)
		}
		f.Delay = 300 * time.Millisecond
		go hub.Serve(f)
		log.Warn("SIMULATED KEYPAD attached - requests are answered automatically", "policy", *fake)
	}

	log.Info("agent started", "version", version, "host_id", store.HostID(), "config", config.Path())
	<-ctx.Done()
	log.Info("agent stopping")
	return nil
}

// watch reloads config.yaml when edited by hand and follows the OS theme.
func watch(ctx context.Context, a *core.Agent, hub *device.Hub, store *config.Store, theme func() string) {
	t := time.NewTicker(3 * time.Second)
	defer t.Stop()
	mtime := func() time.Time {
		st, err := os.Stat(config.Path())
		if err != nil {
			return time.Time{}
		}
		return st.ModTime()
	}
	lastMod, lastTheme := mtime(), theme()
	n := 0
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
		if m := mtime(); !m.Equal(lastMod) {
			lastMod = m
			if c, err := config.Load(); err == nil {
				a.SetConfig(c)
				a.Log.Info("config reloaded")
			} else {
				a.Log.Warn("config.yaml has an error; keeping previous settings", "err", err)
			}
		}
		if n++; n%5 == 0 { // every 15 s
			if th := theme(); th != lastTheme {
				lastTheme = th
				for _, d := range store.Devices() {
					if d.Theme != "dark" && d.Theme != "light" {
						hub.SendSettings(d.ID)
					}
				}
			}
		}
	}
}
