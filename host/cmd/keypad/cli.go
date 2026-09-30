package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"text/tabwriter"
	"time"

	"github.com/jameelhamdan/assistant-keypad/host/internal/claudecfg"
	"github.com/jameelhamdan/assistant-keypad/host/internal/config"
	"github.com/jameelhamdan/assistant-keypad/host/internal/core"
	"github.com/jameelhamdan/assistant-keypad/host/internal/ipc"
	"github.com/jameelhamdan/assistant-keypad/host/internal/osutil"
	"github.com/jameelhamdan/assistant-keypad/host/internal/service"
)

// defaultCommand is what a double-click (no arguments) does.
func defaultCommand() string {
	if runtime.GOOS == "darwin" || runtime.GOOS == "windows" {
		return "tray"
	}
	return "help"
}

func call(method, path string, body, out any) error {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := ipc.NewClient().Do(ctx, method, path, body, out); err != nil {
		if !ipc.Alive() {
			return errors.New("the keypad agent is not running (start it with `keypad agent` or from the tray)")
		}
		return err
	}
	return nil
}

// stopAgent asks a running agent (possibly an older version) to exit.
func stopAgent() {
	if !ipc.Alive() {
		return
	}
	_ = call("POST", "/quit", nil, nil)
	for i := 0; i < 30 && ipc.Alive(); i++ {
		time.Sleep(100 * time.Millisecond)
	}
}

func cmdInstall() error {
	stopAgent()
	cfg, _ := config.Load()
	if err := claudecfg.Install(selfPath(), cfg.Behavior.MaxContinues); err != nil {
		return fmt.Errorf("Claude Code integration: %w", err)
	}
	fmt.Println("Claude Code integration installed:", claudecfg.SettingsPath())
	if err := service.Install(selfPath()); err != nil {
		return fmt.Errorf("start at login: %w", err)
	}
	fmt.Println("Keypad starts at login and is running now.")
	fmt.Println("Restart any open Claude Code sessions to pick up the hooks.")
	return nil
}

func cmdUninstall() error {
	stopAgent()
	if err := service.Uninstall(); err != nil {
		return err
	}
	if err := claudecfg.Uninstall(); err != nil {
		return err
	}
	fmt.Println("Removed the Claude Code integration and the login items.")
	fmt.Println("Settings and pairing keys remain in", config.Dir())
	return nil
}

func cmdClaude(args []string) error {
	sub := "status"
	if len(args) > 0 {
		sub = args[0]
	}
	switch sub {
	case "install":
		cfg, _ := config.Load()
		if err := claudecfg.Install(selfPath(), cfg.Behavior.MaxContinues); err != nil {
			return err
		}
		fmt.Println("Installed. Restart open Claude Code sessions.")
	case "uninstall":
		if err := claudecfg.Uninstall(); err != nil {
			return err
		}
		fmt.Println("Removed.")
	case "status":
		st := claudecfg.Check(selfPath())
		fmt.Printf("settings:  %s\nhooks:     %d/%d%s\nmcp:       %v\nclaude:    %s\ninstalled: %v\n",
			st.Settings, st.Hooks, st.Expected, map[bool]string{true: " (pointing at another keypad binary)"}[st.Stale],
			st.MCP, core.FindClaude(), st.Installed())
	default:
		return errors.New("usage: keypad claude install|uninstall|status")
	}
	return nil
}

func cmdService(args []string) error {
	sub := "status"
	if len(args) > 0 {
		sub = args[0]
	}
	switch sub {
	case "install":
		return service.Install(selfPath())
	case "uninstall":
		return service.Uninstall()
	case "status":
		fmt.Printf("start at login: %v\nagent running:  %v\n", service.Installed(), ipc.Alive())
		return nil
	}
	return errors.New("usage: keypad service install|uninstall|status")
}

func cmdStatus() error {
	var s core.Snapshot
	if err := call("GET", "/status", nil, &s); err != nil {
		return err
	}
	w := tabwriter.NewWriter(os.Stdout, 0, 4, 2, ' ', 0)
	fmt.Fprintf(w, "host id\t%s\npaused\t%v\n\n", s.HostID, s.Paused)
	live := map[string]string{}
	for _, k := range s.Keypads {
		live[k.ID] = k.Link + " " + k.Addr
	}
	fmt.Fprintln(w, "KEYPAD\tNAME\tCONNECTION\tPAIRED")
	for _, d := range s.Devices {
		conn := live[d.ID]
		if conn == "" {
			conn = "offline"
		}
		fmt.Fprintf(w, "%s\t%s\t%s\t%s\n", d.ID, d.Name, conn, d.Key)
	}
	fmt.Fprintln(w, "\nSESSION\tPROJECT\tSTATE\tDETAIL")
	for _, x := range s.Sessions {
		fmt.Fprintf(w, "%.8s\t%s\t%s\t%s %s\n", x.ID, x.Project, x.State, x.Title, x.Detail)
	}
	return w.Flush()
}

func cmdSettings() error {
	var r struct{ URL string }
	if err := call("GET", "/ui", nil, &r); err != nil {
		return err
	}
	return osutil.Open(r.URL)
}

func cmdUpdate(args []string) error {
	if len(args) != 2 {
		return errors.New("usage: keypad update <keypad-id> <firmware.bin>")
	}
	path, err := filepath.Abs(args[1])
	if err != nil {
		return err
	}
	if err := call("POST", "/devices/"+args[0]+"/update", map[string]string{"path": path}, nil); err != nil {
		return err
	}
	for {
		time.Sleep(700 * time.Millisecond)
		var p struct{ Progress int }
		if err := call("GET", "/devices/"+args[0]+"/update", nil, &p); err != nil {
			return err
		}
		if p.Progress < 0 {
			return errors.New("update failed (see the agent log)")
		}
		fmt.Printf("\rupdating %s: %3d%%", args[0], p.Progress)
		if p.Progress >= 100 {
			fmt.Println("\ndone, the keypad restarts")
			return nil
		}
	}
}
