package device

import (
	"context"
	"crypto/md5"
	"encoding/hex"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"os"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/jameelhamdan/assistant-keypad/host/internal/config"
	"github.com/jameelhamdan/assistant-keypad/host/internal/proto"
	"github.com/jameelhamdan/assistant-keypad/host/internal/secure"
)

const (
	pingEvery   = 2 * time.Second
	deadAfter   = 8 * time.Second
	helloWithin = 5 * time.Second
)

// Events receives everything the rest of the agent cares about.
type Events interface {
	Connected(c *Conn)
	Disconnected(c *Conn)
	Message(c *Conn, m proto.In)
}

// Conn is one live, handshaken keypad.
type Conn struct {
	ID    string
	Link  Link
	Since time.Time

	hub     *Hub
	mu      sync.Mutex
	hello   proto.In
	wifi    proto.WiFi
	waiters []*waiter
	done    chan struct{}
	once    sync.Once
}

type waiter struct {
	t  string
	ch chan proto.In
}

// Send marshals and sends a host message.
func (c *Conn) Send(msg any) error {
	b, err := proto.Encode(msg)
	if err != nil {
		return err
	}
	return c.Link.Send(b)
}

func (c *Conn) Close() { c.once.Do(func() { close(c.done); c.Link.Close() }) }

// Info is a snapshot for the tray and settings UI.
type Info struct {
	ID      string     `json:"id"`
	Name    string     `json:"name"`
	Link    string     `json:"link"`
	Addr    string     `json:"addr"`
	FW      string     `json:"fw"`
	Paired  bool       `json:"paired"`
	Battery int        `json:"battery"`
	WiFi    proto.WiFi `json:"wifi"`
	Since   time.Time  `json:"since"`
}

func (c *Conn) Info() Info {
	c.mu.Lock()
	defer c.mu.Unlock()
	return Info{ID: c.ID, Name: c.hello.Name, Link: c.Link.Kind(), Addr: c.Link.Addr(), FW: c.hello.FW,
		Paired: c.hello.Paired, Battery: c.hello.Bat, WiFi: c.wifi, Since: c.Since}
}

func (c *Conn) wait(t string, timeout time.Duration) (proto.In, error) {
	w := &waiter{t: t, ch: make(chan proto.In, 1)}
	c.mu.Lock()
	c.waiters = append(c.waiters, w)
	c.mu.Unlock()
	defer func() {
		c.mu.Lock()
		c.waiters = slices.DeleteFunc(c.waiters, func(x *waiter) bool { return x == w })
		c.mu.Unlock()
	}()
	select {
	case m := <-w.ch:
		return m, nil
	case <-c.done:
		return proto.In{}, errors.New("keypad disconnected")
	case <-time.After(timeout):
		return proto.In{}, fmt.Errorf("keypad did not answer %q", t)
	}
}

// Options configures a Hub.
type Options struct {
	HostName string
	Theme    func() string // resolves the "system" theme to dark|light
	Log      *slog.Logger
}

// Hub finds, connects and supervises keypads.
type Hub struct {
	store *config.Store
	ev    Events
	opt   Options
	disc  *discovery

	mu    sync.Mutex
	conns map[string]*Conn
	busy  map[string]bool // USB ports or device ids currently being handled
	held  map[string]bool // USB ports released for flashing
}

func NewHub(store *config.Store, ev Events, opt Options) *Hub {
	if opt.Log == nil {
		opt.Log = slog.Default()
	}
	if opt.Theme == nil {
		opt.Theme = func() string { return "dark" }
	}
	return &Hub{store: store, ev: ev, opt: opt, disc: newDiscovery(),
		conns: map[string]*Conn{}, busy: map[string]bool{}, held: map[string]bool{}}
}

// Run scans USB, browses mDNS and dials paired keypads until ctx ends.
func (h *Hub) Run(ctx context.Context) {
	go h.disc.run(ctx, func(f string, a ...any) { h.opt.Log.Debug(fmt.Sprintf(f, a...)) })
	t := time.NewTicker(2 * time.Second)
	defer t.Stop()
	lastDial := map[string]time.Time{}
	noUSB := os.Getenv("KEYPAD_NO_USB") == "1" // development: force the Wi-Fi path
	for {
		for _, port := range usbPorts() {
			if noUSB {
				break
			}
			if h.claim("usb:" + port) {
				go h.serveUSB(port)
			}
		}
		for _, d := range h.store.Devices() {
			if d.Key == "" || h.Get(d.ID) != nil || time.Since(lastDial[d.ID]) < 5*time.Second {
				continue
			}
			if h.claim("dial:" + d.ID) {
				lastDial[d.ID] = time.Now()
				go h.dial(d)
			}
		}
		select {
		case <-ctx.Done():
			for _, c := range h.Conns() {
				c.Close()
			}
			return
		case <-t.C:
		}
	}
}

func (h *Hub) claim(key string) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.busy[key] || h.held[strings.TrimPrefix(key, "usb:")] {
		return false
	}
	h.busy[key] = true
	return true
}

func (h *Hub) release(key string) {
	h.mu.Lock()
	delete(h.busy, key)
	h.mu.Unlock()
}

func (h *Hub) serveUSB(port string) {
	defer h.release("usb:" + port)
	l, err := openUSB(port)
	if err != nil {
		h.opt.Log.Debug("usb open failed", "port", port, "err", err)
		time.Sleep(3 * time.Second) // busy (flasher, serial monitor): back off
		return
	}
	h.Serve(l)
}

func (h *Hub) dial(d config.Device) {
	defer h.release("dial:" + d.ID)
	addrs := []string{}
	if s, ok := h.disc.get(d.ID); ok {
		addrs = append(addrs, s.Addr)
	}
	if d.LastIP != "" {
		addrs = append(addrs, net.JoinHostPort(d.LastIP, strconv.Itoa(proto.TCPPort)))
	}
	for _, a := range slices.Compact(addrs) {
		l, err := dialWiFi(a, h.store.HostID(), d.ID, d.Key)
		if err != nil {
			h.opt.Log.Debug("wifi dial failed", "id", d.ID, "addr", a, "err", err)
			continue
		}
		h.Serve(l)
		return
	}
}

// Serve runs the protocol on an open link until it dies. Exported for the
// fake device and tests.
func (h *Hub) Serve(l Link) {
	defer l.Close()
	in := make(chan []byte, 16)
	go func() {
		defer close(in)
		for {
			b, err := l.Recv()
			if err != nil {
				return
			}
			in <- b
		}
	}()

	// Ask the keypad to introduce itself (it may still think an earlier host
	// process is connected).
	_ = l.Send([]byte(`{"t":"who"}`))
	var hello proto.In
	timer := time.NewTimer(helloWithin)
	for hello.T != "hello" {
		select {
		case b, ok := <-in:
			if !ok {
				return
			}
			if m, err := proto.Decode(b); err == nil && m.T == "hello" {
				hello = m
			}
		case <-timer.C:
			h.opt.Log.Debug("no hello", "addr", l.Addr())
			return
		}
	}
	timer.Stop()
	if !strings.HasPrefix(hello.ID, "kp-") || hello.V != proto.Version {
		h.opt.Log.Warn("incompatible keypad", "id", hello.ID, "protocol", hello.V, "fw", hello.FW)
		return
	}

	c := &Conn{ID: hello.ID, Link: l, Since: time.Now(), hub: h, hello: hello, done: make(chan struct{})}
	if hello.WiFi != nil {
		c.wifi = *hello.WiFi
	}
	if !h.register(c) {
		return
	}
	defer h.unregister(c)

	h.greet(c)
	h.ev.Connected(c)

	ping := time.NewTicker(pingEvery)
	defer ping.Stop()
	lastRx := time.Now()
	for {
		select {
		case b, ok := <-in:
			if !ok {
				return
			}
			lastRx = time.Now()
			m, err := proto.Decode(b)
			if err != nil {
				h.opt.Log.Debug("dropped device message", "id", c.ID, "len", len(b))
				continue
			}
			if m.T != "pong" {
				h.opt.Log.Debug("from keypad", "id", c.ID, "msg", string(b))
			}
			h.handle(c, m)
		case <-ping.C:
			if time.Since(lastRx) > deadAfter {
				h.opt.Log.Info("keypad timed out", "id", c.ID)
				return
			}
			_ = c.Send(proto.Simple{T: "ping"})
		case <-c.done:
			return
		}
	}
}

func (h *Hub) register(c *Conn) bool {
	h.mu.Lock()
	old := h.conns[c.ID]
	if old != nil && old.Link.Kind() == "usb" && c.Link.Kind() == "wifi" {
		h.mu.Unlock()
		return false // USB wins while plugged in
	}
	h.conns[c.ID] = c
	h.mu.Unlock()
	if old != nil {
		old.Close()
	}
	d, _ := h.store.Update(c.ID, func(d *config.Device) {
		if d.Name == d.ID && c.hello.Name != "" {
			d.Name = c.hello.Name
		}
		if c.hello.WiFi != nil && c.hello.WiFi.IP != "" {
			d.LastIP = c.hello.WiFi.IP
		}
	})
	h.opt.Log.Info("keypad connected", "id", c.ID, "name", d.Name, "link", c.Link.Kind(), "addr", c.Link.Addr(), "fw", c.hello.FW)
	return true
}

func (h *Hub) unregister(c *Conn) {
	c.Close()
	h.mu.Lock()
	mine := h.conns[c.ID] == c
	if mine {
		delete(h.conns, c.ID)
	}
	h.mu.Unlock()
	if mine {
		h.opt.Log.Info("keypad disconnected", "id", c.ID, "link", c.Link.Kind())
		h.ev.Disconnected(c)
	}
}

func (h *Hub) greet(c *Conn) {
	_ = c.Send(proto.HelloAck{T: "hello_ack", V: proto.Version, Host: h.opt.HostName, Time: time.Now().Unix()})
	h.SendSettings(c.ID)
}

// SendSettings pushes theme, brightness and name to one keypad.
func (h *Hub) SendSettings(id string) {
	c := h.Get(id)
	d, ok := h.store.Device(id)
	if c == nil || !ok {
		return
	}
	theme := d.Theme
	if theme != "dark" && theme != "light" {
		theme = h.opt.Theme()
	}
	_ = c.Send(proto.Settings{T: "settings", Theme: theme, Brightness: d.Brightness, Name: d.Name})
}

func (h *Hub) handle(c *Conn, m proto.In) {
	c.mu.Lock()
	for _, w := range c.waiters {
		if w.t == m.T {
			select {
			case w.ch <- m:
			default:
			}
		}
	}
	c.mu.Unlock()
	switch m.T {
	case "pong":
	case "hello": // device rebooted or re-announced on the same link
		c.mu.Lock()
		c.hello = m
		c.mu.Unlock()
		h.greet(c)
		h.ev.Connected(c)
	case "wifi":
		c.mu.Lock()
		c.wifi = proto.WiFi{State: m.State, SSID: m.SSID, IP: m.IP, RSSI: m.RSSI}
		c.mu.Unlock()
		if m.State == "up" && m.IP != "" {
			_, _ = h.store.Update(c.ID, func(d *config.Device) { d.LastIP = m.IP; d.SSID = m.SSID })
		}
		h.ev.Message(c, m)
	case "log":
		h.opt.Log.Info("keypad log", "id", c.ID, "level", m.Level, "msg", m.Msg)
	default:
		h.ev.Message(c, m)
	}
}

// Get returns the live connection for a keypad id, or nil.
func (h *Hub) Get(id string) *Conn {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.conns[id]
}

// Conns returns all live connections, sorted by id.
func (h *Hub) Conns() []*Conn {
	h.mu.Lock()
	defer h.mu.Unlock()
	out := make([]*Conn, 0, len(h.conns))
	for _, c := range h.conns {
		out = append(out, c)
	}
	slices.SortFunc(out, func(a, b *Conn) int { return strings.Compare(a.ID, b.ID) })
	return out
}

// Discovered lists keypads seen on the network (paired or not).
func (h *Hub) Discovered() []Seen { return h.disc.list() }

// Provision pairs a USB-connected keypad and gives it Wi-Fi credentials.
func (h *Hub) Provision(id, ssid, pass, name string) error {
	c := h.Get(id)
	if c == nil || c.Link.Kind() != "usb" {
		return errors.New("connect the keypad with a USB cable to pair it")
	}
	if name == "" {
		name = id
	}
	key := secure.NewKey()
	if err := c.Send(proto.Provision{T: "provision", SSID: ssid, Pass: pass, Host: h.store.HostID(), Key: key, Name: name}); err != nil {
		return err
	}
	m, err := c.wait("provisioned", 10*time.Second)
	if err != nil {
		return err
	}
	if m.OK == nil || !*m.OK {
		return fmt.Errorf("keypad rejected the settings: %s", m.Err)
	}
	_, err = h.store.Update(id, func(d *config.Device) { d.Key = key; d.Name = name; d.SSID = ssid })
	h.SendSettings(id)
	return err
}

// Unpair tells a connected keypad to wipe its Wi-Fi pairing, then forgets it.
// An offline keypad keeps its settings but no longer accepts this computer.
func (h *Hub) Unpair(id string) error {
	if c := h.Get(id); c != nil {
		_ = c.Send(proto.Simple{T: "unpair"})
		if c.Link.Kind() == "wifi" {
			time.AfterFunc(500*time.Millisecond, c.Close)
		}
	}
	return h.store.Remove(id)
}

// UpdateFirmware streams a firmware image over the live link (USB or Wi-Fi).
func (h *Hub) UpdateFirmware(id string, bin []byte, progress func(pct int)) error {
	c := h.Get(id)
	if c == nil {
		return errors.New("keypad not connected")
	}
	sum := md5.Sum(bin)
	if err := c.Send(proto.OTABegin{T: "ota_begin", Size: len(bin), MD5: hex.EncodeToString(sum[:])}); err != nil {
		return err
	}
	if _, err := c.wait("ota", 10*time.Second); err != nil {
		return err
	}
	const chunk = 2048
	for off := 0; off < len(bin); off += chunk {
		end := min(off+chunk, len(bin))
		if err := c.Send(proto.OTAData{T: "ota_data", Off: off, D: bin[off:end]}); err != nil {
			return err
		}
		m, err := c.wait("ota", 10*time.Second)
		if err != nil {
			return err
		}
		if m.OK != nil && !*m.OK {
			return fmt.Errorf("keypad rejected the update: %s", m.Err)
		}
		if progress != nil {
			progress(end * 100 / len(bin))
		}
	}
	if err := c.Send(proto.Simple{T: "ota_end"}); err != nil {
		return err
	}
	m, err := c.wait("ota", 20*time.Second)
	if err != nil {
		return err
	}
	if m.OK == nil || !*m.OK {
		return fmt.Errorf("update failed: %s", m.Err)
	}
	return nil // the keypad reboots into the new firmware
}

// HoldUSB releases a USB port (for an external flasher) until the returned func is called.
func (h *Hub) HoldUSB(id string) (string, func(), error) {
	c := h.Get(id)
	if c == nil || c.Link.Kind() != "usb" {
		return "", nil, errors.New("keypad is not on USB")
	}
	port := c.Link.Addr()
	h.mu.Lock()
	h.held[port] = true
	h.mu.Unlock()
	c.Close()
	return port, func() {
		h.mu.Lock()
		delete(h.held, port)
		h.mu.Unlock()
	}, nil
}
