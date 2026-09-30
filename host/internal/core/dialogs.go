package core

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"slices"
	"sync"
	"time"

	"github.com/jameelhamdan/assistant-keypad/host/internal/config"
	"github.com/jameelhamdan/assistant-keypad/host/internal/proto"
)

const handbackGrace = 2 * time.Second

var (
	ErrNoKeypad = errors.New("no keypad connected")
	ErrTimeout  = errors.New("no answer on the keypad")
	ErrToPC     = errors.New("handed back to the PC")
)

// Display is what dialogs need from the device layer.
type Display interface {
	Targets(project string) []string // connected keypads that show this project
	SendTo(id string, msg any) error
}

// Dialogs serialises interactive requests: one dialog owns the keypads at a
// time, FIFO across every Claude Code session.
type Dialogs struct {
	disp     Display
	idle     func() (time.Duration, bool) // time since last keyboard/mouse input
	handback func() config.Handback
	log      *slog.Logger
	onChange func()

	mu     sync.Mutex
	queue  []*Dialog
	active *Dialog
	seq    int
}

func NewDialogs(disp Display, idle func() (time.Duration, bool), hb func() config.Handback, log *slog.Logger) *Dialogs {
	return &Dialogs{disp: disp, idle: idle, handback: hb, log: log, onChange: func() {}}
}

// routed is a press together with the keypad it came from.
type routed struct {
	in  proto.In
	dev string
}

// Dialog is one interactive request; it may show several screens.
type Dialog struct {
	m        *Dialogs
	id       string
	Project  string
	handback bool
	turn     chan struct{}
	press    chan routed

	mu        sync.Mutex
	screen    *proto.Screen
	targets   []string
	acked     map[string]bool
	activated time.Time
	idle0     time.Duration
	idleOK    bool
	n         int
}

// Queued returns how many dialogs are waiting behind the active one.
func (m *Dialogs) Queued() int {
	m.mu.Lock()
	defer m.mu.Unlock()
	return len(m.queue)
}

// Busy reports whether a dialog currently owns the keypads.
func (m *Dialogs) Busy() bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.active != nil
}

// Run waits for the keypads, then runs fn. handback lets PC activity take
// the request back. Any error means "let Claude Code use its own UI".
func (m *Dialogs) Run(ctx context.Context, project, kind string, handback bool, fn func(d *Dialog) error) error {
	if len(m.disp.Targets(project)) == 0 {
		return ErrNoKeypad
	}
	m.mu.Lock()
	m.seq++
	d := &Dialog{m: m, id: fmt.Sprintf("%c%d", kind[0], m.seq), Project: project, handback: handback,
		turn: make(chan struct{}), press: make(chan routed, 4)}
	m.queue = append(m.queue, d)
	m.promoteLocked()
	m.mu.Unlock()
	m.onChange()

	select {
	case <-d.turn:
	case <-ctx.Done():
		m.mu.Lock()
		m.queue = slices.DeleteFunc(m.queue, func(x *Dialog) bool { return x == d })
		if m.active == d { // won the race
			m.finishLocked(d)
		}
		m.mu.Unlock()
		m.onChange()
		return ErrTimeout
	}
	defer func() {
		m.mu.Lock()
		m.finishLocked(d)
		m.mu.Unlock()
		m.onChange()
	}()
	return fn(d)
}

func (m *Dialogs) promoteLocked() {
	if m.active != nil || len(m.queue) == 0 {
		return
	}
	d := m.queue[0]
	m.queue = m.queue[1:]
	m.active = d
	d.activated = time.Now()
	if m.idle != nil {
		d.idle0, d.idleOK = m.idle()
	}
	close(d.turn)
}

func (m *Dialogs) finishLocked(d *Dialog) {
	if m.active != d {
		return
	}
	d.closeScreen("done", "")
	m.active = nil
	m.promoteLocked()
}

// Press routes a key press from a keypad; false if it answers nothing.
func (m *Dialogs) Press(dev string, in proto.In) bool {
	m.mu.Lock()
	d := m.active
	m.mu.Unlock()
	if d == nil {
		return false
	}
	d.mu.Lock()
	ok := d.screen != nil && d.screen.ID == in.ID && slices.Contains(d.targets, dev)
	d.mu.Unlock()
	if !ok {
		return false
	}
	select {
	case d.press <- routed{in, dev}:
	default:
	}
	return true
}

// Ack records a keypad's acknowledgement of a screen.
func (m *Dialogs) Ack(dev, id string) {
	m.mu.Lock()
	d := m.active
	m.mu.Unlock()
	if d == nil {
		return
	}
	d.mu.Lock()
	if d.screen != nil && d.screen.ID == id {
		d.acked[dev] = true
	}
	d.mu.Unlock()
}

// Reshow sends the active screen to a keypad that just (re)connected.
func (m *Dialogs) Reshow(dev string) bool {
	m.mu.Lock()
	d := m.active
	m.mu.Unlock()
	if d == nil {
		return false
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.screen == nil || !slices.Contains(m.disp.Targets(d.Project), dev) {
		return false
	}
	if !slices.Contains(d.targets, dev) {
		d.targets = append(d.targets, dev)
	}
	_ = m.disp.SendTo(dev, d.screen)
	return true
}

// Show displays a screen and waits for a press. The press's act is
// returned; "pc" (hand to PC), timeouts and disconnects become errors.
func (d *Dialog) Show(ctx context.Context, s proto.Screen) (proto.In, error) {
	m := d.m
	targets := m.disp.Targets(d.Project)
	if len(targets) == 0 {
		return proto.In{}, ErrNoKeypad
	}
	d.mu.Lock()
	d.n++
	s.T, s.ID = "screen", fmt.Sprintf("%s-%d", d.id, d.n)
	if dl, ok := ctx.Deadline(); ok {
		s.Timeout = max(1, int(time.Until(dl).Seconds()))
	}
	d.screen, d.targets, d.acked = &s, targets, map[string]bool{}
	d.mu.Unlock()
	for len(d.press) > 0 {
		<-d.press
	}
	for _, id := range targets {
		_ = m.disp.SendTo(id, s)
	}

	resend := time.NewTimer(1500 * time.Millisecond)
	defer resend.Stop()
	tick := time.NewTicker(250 * time.Millisecond)
	defer tick.Stop()
	for {
		select {
		case r := <-d.press:
			if r.in.Act == "pc" {
				d.closeScreen("pc", r.dev)
				return r.in, ErrToPC
			}
			d.closeScreen("answered", r.dev)
			return r.in, nil
		case <-resend.C:
			d.mu.Lock()
			for _, id := range d.targets {
				if !d.acked[id] {
					_ = m.disp.SendTo(id, s)
				}
			}
			d.mu.Unlock()
		case <-tick.C:
			if len(m.disp.Targets(d.Project)) == 0 {
				d.closeScreen("disconnected", "")
				return proto.In{}, ErrNoKeypad
			}
			if d.shouldHandBack() {
				m.log.Info("PC activity - handing the request back to the computer", "screen", s.ID)
				d.closeScreen("pc", "")
				return proto.In{}, ErrToPC
			}
		case <-ctx.Done():
			d.closeScreen("timeout", "")
			return proto.In{}, ErrTimeout
		}
	}
}

// closeScreen tells keypads (except the one that answered, which already
// locked itself) that the current screen is gone.
func (d *Dialog) closeScreen(why, except string) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.screen == nil {
		return
	}
	for _, id := range d.targets {
		if id != except || why == "done" {
			_ = d.m.disp.SendTo(id, proto.Close{T: "close", ID: d.screen.ID, Why: why})
		}
	}
	if why == "done" {
		d.screen = nil
	}
}

func (d *Dialog) shouldHandBack() bool {
	if !d.handback || d.m.idle == nil || !d.idleOK {
		return false
	}
	hb := d.m.handback()
	if !hb.Enabled {
		return false
	}
	idle, ok := d.m.idle()
	if !ok {
		return false
	}
	elapsed := time.Since(d.activated)
	// Input that began after the request appeared (with a grace period, so
	// typing that was already under way doesn't count) means "I'm at the PC".
	if idle+handbackGrace < elapsed {
		return true
	}
	// Optional: when the user was at the PC moments before, give the keypad
	// only a short head start (off when present_window is 0).
	present := hb.PresentWindow > 0 && d.idle0 < time.Duration(hb.PresentWindow)*time.Second
	return present && elapsed > time.Duration(hb.PresentWait)*time.Second
}
