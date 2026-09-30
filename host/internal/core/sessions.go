package core

import (
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/jameelhamdan/assistant-keypad/host/internal/proto"
)

// Session states shown on the keypad.
const (
	StIdle       = "idle"
	StThinking   = "thinking"
	StWorking    = "working"
	StTool       = "tool"
	StPermission = "permission"
	StQuestion   = "question"
	StInput      = "input"
	StDone       = "done"
	StStopped    = "stopped"
	StContinuing = "continuing"
	StFailed     = "failed"
	StEnded      = "ended"
)

var busyStates = []string{StThinking, StWorking, StTool, StPermission, StQuestion, StInput, StContinuing}

const staleAfter = 6 * time.Hour

type Session struct {
	ID        string    `json:"id"`
	Project   string    `json:"project"`
	Cwd       string    `json:"cwd"`
	PIDs      []int     `json:"pids"` // claude process ancestry reported by the hook shim
	State     string    `json:"state"`
	Title     string    `json:"title"`
	Detail    string    `json:"detail"`
	Continues int       `json:"continues"`
	Started   time.Time `json:"started"`
	Last      time.Time `json:"last"`
	Worker    bool      `json:"worker"`
}

// Sessions is the registry of Claude Code sessions, keyed by session_id.
type Sessions struct {
	mu      sync.Mutex
	m       map[string]*Session
	current string
	pinned  string
}

func NewSessions() *Sessions { return &Sessions{m: map[string]*Session{}} }

func (s *Sessions) get(id string) *Session {
	x := s.m[id]
	if x == nil {
		x = &Session{ID: id, State: StIdle, Started: time.Now()}
		s.m[id] = x
		s.pruneLocked()
	}
	return x
}

// Touch records an event: new state, optional cwd/pid. It becomes the
// current session unless another one is pinned.
func (s *Sessions) Touch(id, state, title, detail, cwd string, pids []int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	x := s.get(id)
	if cwd != "" {
		x.Cwd = cwd
		x.Project = projectOf(cwd)
	}
	if len(pids) > 0 {
		x.PIDs = pids
	}
	x.State, x.Title, x.Detail, x.Last = state, title, detail, time.Now()
	if state == StEnded {
		if s.pinned == id {
			s.pinned = ""
		}
		if s.current == id {
			s.current = s.latestLocked(id)
		}
		return
	}
	if s.pinned == "" || s.pinned == id {
		s.current = id
	}
}

// Start registers a (re)started session and resets its counters.
func (s *Sessions) Start(id, cwd string, pids []int, worker bool) {
	s.mu.Lock()
	x := s.get(id)
	x.Continues, x.Started = 0, time.Now()
	x.Worker = x.Worker || worker
	s.mu.Unlock()
	s.Touch(id, StIdle, "Ready", "", cwd, pids)
}

func (s *Sessions) Get(id string) (Session, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	x, ok := s.m[id]
	if !ok {
		return Session{}, false
	}
	return *x, true
}

// ByPID finds the live session whose claude process is pid (the MCP
// server's parent).
func (s *Sessions) ByPID(pid int) (Session, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	var best *Session
	for _, x := range s.m {
		if pid > 0 && slices.Contains(x.PIDs, pid) && x.State != StEnded && (best == nil || x.Last.After(best.Last)) {
			best = x
		}
	}
	if best == nil {
		return Session{}, false
	}
	return *best, true
}

func (s *Sessions) Project(id string) string {
	x, _ := s.Get(id)
	return x.Project
}

func (s *Sessions) IsBusy(id string) bool {
	x, ok := s.Get(id)
	return ok && slices.Contains(busyStates, x.State)
}

func (s *Sessions) Continues(id string) int {
	x, _ := s.Get(id)
	return x.Continues
}

func (s *Sessions) AddContinue(id string) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	x := s.get(id)
	x.Continues++
	return x.Continues
}

func (s *Sessions) ResetContinues(id string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if x, ok := s.m[id]; ok {
		x.Continues = 0
	}
}

// Select pins the display to the session whose id starts with prefix.
func (s *Sessions) Select(prefix string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	for id, x := range s.m {
		if prefix != "" && strings.HasPrefix(id, prefix) && x.State != StEnded {
			s.pinned, s.current = id, id
			return true
		}
	}
	return false
}

// Follow unpins: the display follows the latest activity again.
func (s *Sessions) Follow() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.pinned = ""
	if l := s.latestLocked(""); l != "" {
		s.current = l
	}
}

// Current returns the session shown on the keypads.
func (s *Sessions) Current() (Session, bool) {
	s.mu.Lock()
	id := s.current
	s.mu.Unlock()
	return s.Get(id)
}

func (s *Sessions) Pinned() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.pinned != ""
}

// Live lists sessions with recent activity, newest first.
func (s *Sessions) Live() []Session {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []Session
	for _, x := range s.m {
		if x.State != StEnded && time.Since(x.Last) < staleAfter {
			out = append(out, *x)
		}
	}
	slices.SortFunc(out, func(a, b Session) int { return b.Last.Compare(a.Last) })
	return out
}

// Wire converts live sessions for the status message, filtered by project.
func Wire(list []Session, allow func(project string) bool) []proto.Session {
	out := []proto.Session{}
	for _, x := range list {
		if allow != nil && !allow(x.Project) {
			continue
		}
		out = append(out, proto.Session{ID: short(x.ID), Project: x.Project, State: x.State,
			Title: clip(x.Title, 40), Detail: clip(x.Detail, 120), Since: int(time.Since(x.Started).Seconds())})
		if len(out) == 8 {
			break
		}
	}
	return out
}

func (s *Sessions) latestLocked(exclude string) string {
	var best *Session
	for id, x := range s.m {
		if id != exclude && x.State != StEnded && (best == nil || x.Last.After(best.Last)) {
			best = x
		}
	}
	if best == nil {
		return ""
	}
	return best.ID
}

func (s *Sessions) pruneLocked() {
	if len(s.m) <= 32 {
		return
	}
	var oldest *Session
	for id, x := range s.m {
		if id != s.current && (oldest == nil || x.Last.Before(oldest.Last)) {
			oldest = x
		}
	}
	if oldest != nil {
		delete(s.m, oldest.ID)
	}
}

func short(id string) string {
	if len(id) > 8 {
		return id[:8]
	}
	return id
}
