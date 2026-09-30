// Package config holds user settings (config.yaml, edited from the tray) and
// machine state (state.json: host id, paired keypads and their keys).
package config

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sync"

	"gopkg.in/yaml.v3"
)

const AppName = "Keypad"

// Dir is the per-user data directory (config, state, logs, socket).
func Dir() string {
	if d := os.Getenv("KEYPAD_HOME"); d != "" {
		return d
	}
	base, err := os.UserConfigDir()
	if err != nil {
		base = os.TempDir()
	}
	return filepath.Join(base, AppName)
}

func LogDir() string { return filepath.Join(Dir(), "logs") }

type Shortcut struct {
	Label  string `yaml:"label" json:"label"`
	Prompt string `yaml:"prompt" json:"prompt"`
}

type Timeouts struct {
	Question   int `yaml:"question" json:"question"`
	Permission int `yaml:"permission" json:"permission"`
	Stop       int `yaml:"stop" json:"stop"`
}

type Handback struct {
	Enabled       bool `yaml:"enabled" json:"enabled"`
	PresentWindow int  `yaml:"present_window" json:"present_window"` // s: input this recent = you are at the PC (0 = off)
	PresentWait   int  `yaml:"present_wait" json:"present_wait"`     // s: then the keypad only gets this long
}

type Behavior struct {
	AskOnStop         bool     `yaml:"ask_on_stop" json:"ask_on_stop"`
	MaxContinues      int      `yaml:"max_continues" json:"max_continues"`
	InterceptAskUser  bool     `yaml:"intercept_ask_user_question" json:"intercept_ask_user_question"`
	AnnounceInContext bool     `yaml:"announce_in_context" json:"announce_in_context"`
	ShortcutTTL       int      `yaml:"shortcut_ttl" json:"shortcut_ttl"`
	Timeouts          Timeouts `yaml:"timeouts" json:"timeouts"`
	Handback          Handback `yaml:"pc_handback" json:"pc_handback"`
}

// KeyMap places actions on keys 1..8 (top row 1-4, bottom row 5-8).
type KeyMap struct {
	Allow     int `yaml:"allow" json:"allow"`
	Deny      int `yaml:"deny" json:"deny"`
	Yes       int `yaml:"yes" json:"yes"`
	No        int `yaml:"no" json:"no"`
	Continue  int `yaml:"continue" json:"continue"`
	Shortcuts int `yaml:"shortcuts" json:"shortcuts"`
	Done      int `yaml:"done" json:"done"`
	PC        int `yaml:"pc" json:"pc"`
	Menu      int `yaml:"menu" json:"menu"`
}

type Workers struct {
	Enabled     bool   `yaml:"enabled" json:"enabled"`
	AskFirst    bool   `yaml:"ask_first" json:"ask_first"`
	MaxTurns    int    `yaml:"max_turns" json:"max_turns"`
	MaxParallel int    `yaml:"max_parallel" json:"max_parallel"`
	Model       string `yaml:"model" json:"model"`
}

type Config struct {
	Behavior  Behavior   `yaml:"behavior" json:"behavior"`
	Keys      KeyMap     `yaml:"keys" json:"keys"`
	Shortcuts []Shortcut `yaml:"shortcuts" json:"shortcuts"`
	Workers   Workers    `yaml:"workers" json:"workers"`
	LogLevel  string     `yaml:"log_level" json:"log_level"`
}

func Default() Config {
	return Config{
		Behavior: Behavior{
			AskOnStop: true, MaxContinues: 20, InterceptAskUser: true, AnnounceInContext: true,
			ShortcutTTL: 900,
			Timeouts:    Timeouts{Question: 300, Permission: 300, Stop: 300},
			Handback:    Handback{Enabled: true, PresentWindow: 0, PresentWait: 8},
		},
		Keys: KeyMap{Allow: 1, Deny: 4, Yes: 1, No: 4, Continue: 1, Shortcuts: 2, Done: 4, PC: 8, Menu: 1},
		Shortcuts: []Shortcut{
			{"Continue", "Continue with the current task and make further useful progress."},
			{"Write tests", "Add or extend automated tests for the code you just changed, covering the main paths and edge cases, then run them and fix failures."},
			{"Fix bugs", "Look for bugs, error-handling gaps and edge cases in the current change, fix them, and explain each fix briefly."},
			{"Refactor", "Refactor the code you are working on for readability and maintainability without changing behaviour, then run the existing tests."},
			{"Review", "Review the current changes as a strict senior code reviewer, list the issues you find, and fix the important ones."},
			{"Improve design", "Review the UI/design of what you are working on and improve visual hierarchy, spacing, consistency and accessibility. Keep behaviour unchanged."},
			{"Explain", "Explain briefly what you have done so far, what remains, and any decisions I should know about."},
			{"Commit", "Commit the current work with a clear, conventional commit message. Do not push."},
		},
		Workers:  Workers{Enabled: true, AskFirst: true, MaxTurns: 60, MaxParallel: 2},
		LogLevel: "info",
	}
}

// Validate clamps values into safe ranges.
func (c *Config) Validate() error {
	b := &c.Behavior
	clamp := func(v *int, lo, hi int) {
		if *v < lo {
			*v = lo
		} else if *v > hi {
			*v = hi
		}
	}
	clamp(&b.MaxContinues, 1, 200)
	clamp(&b.ShortcutTTL, 30, 86400)
	clamp(&b.Timeouts.Question, 10, 3600)
	clamp(&b.Timeouts.Permission, 10, 3600)
	clamp(&b.Timeouts.Stop, 10, 3600)
	clamp(&b.Handback.PresentWindow, 0, 3600)
	clamp(&b.Handback.PresentWait, 0, 600)
	clamp(&c.Workers.MaxTurns, 1, 500)
	clamp(&c.Workers.MaxParallel, 1, 8)
	for _, k := range []*int{&c.Keys.Allow, &c.Keys.Deny, &c.Keys.Yes, &c.Keys.No, &c.Keys.Continue,
		&c.Keys.Shortcuts, &c.Keys.Done, &c.Keys.PC, &c.Keys.Menu} {
		clamp(k, 1, 8)
	}
	if c.Keys.Allow == c.Keys.Deny || c.Keys.Yes == c.Keys.No || c.Keys.Continue == c.Keys.Done {
		return errors.New("opposite actions must be on different keys")
	}
	if len(c.Shortcuts) > 32 {
		c.Shortcuts = c.Shortcuts[:32]
	}
	return nil
}

func Path() string { return filepath.Join(Dir(), "config.yaml") }

// Load reads config.yaml, creating it with defaults on first run.
func Load() (Config, error) {
	c := Default()
	b, err := os.ReadFile(Path())
	if errors.Is(err, os.ErrNotExist) {
		return c, Save(c)
	}
	if err != nil {
		return c, err
	}
	if err := yaml.Unmarshal(b, &c); err != nil {
		return Default(), err
	}
	return c, c.Validate()
}

func Save(c Config) error {
	if err := c.Validate(); err != nil {
		return err
	}
	b, err := yaml.Marshal(c)
	if err != nil {
		return err
	}
	return writeAtomic(Path(), append([]byte("# Keypad settings - edit from the tray (Settings...) or by hand.\n"), b...), 0o644)
}

// ---- machine state ----------------------------------------------------------

type Device struct {
	ID         string   `json:"id"`
	Name       string   `json:"name"`
	Key        string   `json:"key,omitempty"` // pairing key (hex); empty = USB only
	Theme      string   `json:"theme"`         // dark | light | system
	Brightness int      `json:"brightness"`
	Projects   []string `json:"projects,omitempty"` // empty = all projects
	LastIP     string   `json:"last_ip,omitempty"`
	SSID       string   `json:"ssid,omitempty"`
}

type State struct {
	HostID  string    `json:"host_id"`
	Devices []*Device `json:"devices"`
}

// Store guards state.json.
type Store struct {
	mu sync.Mutex
	st State
}

func statePath() string { return filepath.Join(Dir(), "state.json") }

func OpenStore() (*Store, error) {
	s := &Store{}
	b, err := os.ReadFile(statePath())
	if err == nil {
		_ = json.Unmarshal(b, &s.st)
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	if s.st.HostID == "" {
		r := make([]byte, 4)
		_, _ = rand.Read(r)
		s.st.HostID = "h-" + hex.EncodeToString(r)
		return s, s.saveLocked()
	}
	return s, nil
}

func (s *Store) HostID() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.st.HostID
}

// Devices returns copies of the known keypads.
func (s *Store) Devices() []Device {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Device, 0, len(s.st.Devices))
	for _, d := range s.st.Devices {
		out = append(out, *d)
	}
	return out
}

func (s *Store) Device(id string) (Device, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, d := range s.st.Devices {
		if d.ID == id {
			return *d, true
		}
	}
	return Device{}, false
}

// Update creates or modifies a keypad record and persists it.
func (s *Store) Update(id string, fn func(d *Device)) (Device, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	var d *Device
	for _, x := range s.st.Devices {
		if x.ID == id {
			d = x
		}
	}
	if d == nil {
		d = &Device{ID: id, Name: id, Theme: "system", Brightness: 80}
		s.st.Devices = append(s.st.Devices, d)
	}
	fn(d)
	return *d, s.saveLocked()
}

func (s *Store) Remove(id string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := s.st.Devices[:0]
	for _, d := range s.st.Devices {
		if d.ID != id {
			out = append(out, d)
		}
	}
	s.st.Devices = out
	return s.saveLocked()
}

func (s *Store) saveLocked() error {
	b, _ := json.MarshalIndent(s.st, "", "  ")
	return writeAtomic(statePath(), b, 0o600) // holds pairing keys
}

func writeAtomic(path string, b []byte, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, b, mode); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}
