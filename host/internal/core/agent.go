// Package core is the agent's brain: Claude Code sessions, hook decisions,
// keypad dialogs, shortcuts and workers.
package core

import (
	"context"
	"log/slog"
	"slices"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/jameelhamdan/assistant-keypad/host/internal/config"
	"github.com/jameelhamdan/assistant-keypad/host/internal/device"
	"github.com/jameelhamdan/assistant-keypad/host/internal/proto"
)

// Agent ties sessions, dialogs, keypads and configuration together. It is
// the device.Events sink and the Display for dialogs.
type Agent struct {
	Log      *slog.Logger
	Store    *config.Store
	Hub      *device.Hub
	Sessions *Sessions
	Dialogs  *Dialogs
	Workers  *Workers

	cfgMu sync.RWMutex
	cfg   config.Config

	paused atomic.Bool
	dirty  chan struct{}

	pendMu  sync.Mutex
	pending map[string]pendingShortcut
}

type pendingShortcut struct {
	config.Shortcut
	at time.Time
}

// NewAgent builds an agent; call SetHub before Run.
func NewAgent(cfg config.Config, store *config.Store, idle func() (time.Duration, bool), log *slog.Logger) *Agent {
	a := &Agent{Log: log, Store: store, Sessions: NewSessions(), cfg: cfg,
		dirty: make(chan struct{}, 1), pending: map[string]pendingShortcut{}}
	a.Dialogs = NewDialogs(a, idle, func() config.Handback { return a.Config().Behavior.Handback }, log)
	a.Dialogs.onChange = a.markDirty
	a.Workers = NewWorkers(a)
	return a
}

func (a *Agent) SetHub(h *device.Hub) { a.Hub = h }

func (a *Agent) Config() config.Config {
	a.cfgMu.RLock()
	defer a.cfgMu.RUnlock()
	return a.cfg
}

// SetConfig applies new settings live.
func (a *Agent) SetConfig(c config.Config) {
	a.cfgMu.Lock()
	a.cfg = c
	a.cfgMu.Unlock()
	a.markDirty()
}

func (a *Agent) Paused() bool { return a.paused.Load() }

func (a *Agent) SetPaused(p bool) {
	a.paused.Store(p)
	a.markDirty()
}

// Run pushes status updates to keypads until ctx ends.
func (a *Agent) Run(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case <-a.dirty:
			time.Sleep(80 * time.Millisecond) // coalesce bursts of hook events
			a.pushStatus()
		}
	}
}

func (a *Agent) markDirty() {
	select {
	case a.dirty <- struct{}{}:
	default:
	}
}

// ---- Display ------------------------------------------------------------------

// Targets lists connected keypads whose project filter accepts project.
func (a *Agent) Targets(project string) []string {
	if a.Hub == nil {
		return nil
	}
	var out []string
	for _, c := range a.Hub.Conns() {
		if a.accepts(c.ID, project) {
			out = append(out, c.ID)
		}
	}
	return out
}

func (a *Agent) SendTo(id string, msg any) error {
	if c := a.Hub.Get(id); c != nil {
		return c.Send(msg)
	}
	return nil
}

func (a *Agent) accepts(id, project string) bool {
	d, ok := a.Store.Device(id)
	if !ok || len(d.Projects) == 0 || project == "" {
		return true
	}
	return slices.ContainsFunc(d.Projects, func(p string) bool { return strings.EqualFold(p, project) })
}

// Toast shows a short message on every keypad.
func (a *Agent) Toast(text, level string, ms int) {
	for _, c := range a.Hub.Conns() {
		_ = c.Send(proto.Toast{T: "toast", Text: clip(text, 60), Level: level, MS: ms})
	}
}

func (a *Agent) pushStatus() {
	if a.Hub == nil {
		return
	}
	live := a.Sessions.Live()
	cur, _ := a.Sessions.Current()
	cfg := a.Config()
	keys := proto.Keys{}
	if len(cfg.Shortcuts) > 0 && cur.ID != "" {
		keys.Set(cfg.Keys.Menu, "Shortcuts", "menu", "accent")
	}
	for _, c := range a.Hub.Conns() {
		id := c.ID
		list := Wire(live, func(p string) bool { return a.accepts(id, p) })
		sel := short(cur.ID)
		if !slices.ContainsFunc(list, func(s proto.Session) bool { return s.ID == sel }) {
			sel = ""
			if len(list) > 0 {
				sel = list[0].ID
			}
		}
		_ = c.Send(proto.Status{T: "status", Sessions: list, Sel: sel, Pinned: a.Sessions.Pinned(),
			Queue: a.Dialogs.Queued(), Paused: a.Paused(), Keys: keys})
	}
}

// ---- device.Events --------------------------------------------------------------

func (a *Agent) Connected(c *device.Conn) {
	a.pushStatus()
	a.Dialogs.Reshow(c.ID)
}

func (a *Agent) Disconnected(c *device.Conn) { a.markDirty() }

func (a *Agent) Message(c *device.Conn, m proto.In) {
	switch m.T {
	case "ack":
		a.Dialogs.Ack(c.ID, m.ID)
	case "press":
		if m.ID == "status" {
			if m.Act == "menu" {
				go a.shortcutMenu()
			}
			return
		}
		if !a.Dialogs.Press(c.ID, m) {
			// stale screen on that keypad: send it back to the status screen
			_ = c.Send(proto.Close{T: "close", ID: m.ID, Why: "stale"})
		}
	case "session":
		if m.Act == "follow" {
			a.Sessions.Follow()
		} else {
			a.Sessions.Select(m.SID)
		}
		a.markDirty()
	case "wifi":
		a.markDirty()
	}
}

// ---- shortcuts -----------------------------------------------------------------------

func (a *Agent) shortcutLabels() []string {
	var out []string
	for _, s := range a.Config().Shortcuts {
		out = append(out, clip(s.Label, 28))
	}
	return out
}

// shortcutMenu runs when the menu key is pressed on the status screen.
func (a *Agent) shortcutMenu() {
	cur, ok := a.Sessions.Current()
	if !ok {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	_ = a.Dialogs.Run(ctx, cur.Project, "menu", false, func(d *Dialog) error {
		p, err := d.Show(ctx, proto.Screen{Tpl: "list", Title: "Send to Claude", Project: cur.Project,
			Items: a.shortcutLabels(), Click: "back"})
		if err != nil || p.Act != "item" || p.Idx == nil {
			return err
		}
		go a.QueueShortcut(*p.Idx, cur.ID)
		return nil
	})
}

// QueueShortcut delivers shortcut idx to a session: on its next hook when
// Claude is busy, or by offering a worker when the session is idle.
func (a *Agent) QueueShortcut(idx int, sid string) {
	cfg := a.Config()
	if idx < 0 || idx >= len(cfg.Shortcuts) {
		return
	}
	sc := cfg.Shortcuts[idx]
	s, ok := a.Sessions.Get(sid)
	if !ok {
		return
	}
	a.pendMu.Lock()
	a.pending[sid] = pendingShortcut{sc, time.Now()}
	a.pendMu.Unlock()
	a.Log.Info("shortcut queued", "session", short(sid), "project", s.Project, "label", sc.Label)

	if a.Sessions.IsBusy(sid) || !cfg.Workers.Enabled || s.Cwd == "" {
		a.Toast("Queued for "+s.Project+": "+sc.Label, "info", 2500)
		return
	}
	// Idle session: no hook will fire to carry it. Offer a worker.
	if cfg.Workers.AskFirst {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
		defer cancel()
		var yes bool
		_ = a.Dialogs.Run(ctx, s.Project, "worker", false, func(d *Dialog) error {
			p, err := d.Show(ctx, a.yesNo(s.Project, "Start a worker?",
				s.Project+" is idle. Start a new Claude worker there with \""+sc.Label+"\"?", "back"))
			yes = err == nil && p.Act == "yes"
			return err
		})
		if !yes {
			a.Toast("Queued for your next prompt: "+sc.Label, "info", 2500)
			return
		}
	}
	a.takePending(sid)
	if err := a.Workers.Start(s.Cwd, s.Project, sc); err != nil {
		a.Log.Warn("worker start failed", "err", err)
		a.Toast("Worker failed: "+err.Error(), "danger", 4000)
		return
	}
	a.Toast("Worker started in "+s.Project, "ok", 3000)
}

func (a *Agent) takePending(sid string) (config.Shortcut, bool) {
	a.pendMu.Lock()
	defer a.pendMu.Unlock()
	p, ok := a.pending[sid]
	delete(a.pending, sid)
	if !ok || time.Since(p.at) > time.Duration(a.Config().Behavior.ShortcutTTL)*time.Second {
		return config.Shortcut{}, false
	}
	return p.Shortcut, true
}

// ---- screen builders -----------------------------------------------------------------

func (a *Agent) yesNo(project, title, body, click string) proto.Screen {
	k := a.Config().Keys
	keys := proto.Keys{}.Set(k.Yes, "Yes", "yes", "ok").Set(k.No, "No", "no", "danger")
	if k.PC != k.Yes && k.PC != k.No {
		keys.Set(k.PC, "PC", "pc", "dim")
	}
	return proto.Screen{Tpl: "prompt", Title: title, Project: project, Body: body, Keys: keys, Click: click}
}

// ---- snapshot for the tray / settings UI ---------------------------------------------

type Snapshot struct {
	HostID     string          `json:"host_id"`
	Paused     bool            `json:"paused"`
	Busy       bool            `json:"busy"`
	Queue      int             `json:"queue"`
	Keypads    []device.Info   `json:"keypads"`
	Devices    []config.Device `json:"devices"`
	Discovered []device.Seen   `json:"discovered"`
	Sessions   []Session       `json:"sessions"`
	Workers    []WorkerInfo    `json:"workers"`
}

func (a *Agent) Snapshot() Snapshot {
	s := Snapshot{HostID: a.Store.HostID(), Paused: a.Paused(), Busy: a.Dialogs.Busy(), Queue: a.Dialogs.Queued(),
		Sessions: a.Sessions.Live(), Workers: a.Workers.List(), Keypads: []device.Info{}, Devices: []config.Device{}}
	if a.Hub != nil {
		for _, c := range a.Hub.Conns() {
			s.Keypads = append(s.Keypads, c.Info())
		}
		s.Discovered = a.Hub.Discovered()
	}
	for _, d := range a.Store.Devices() {
		d.Key = strconv.FormatBool(d.Key != "") // never expose keys; "true" = paired
		s.Devices = append(s.Devices, d)
	}
	return s
}
