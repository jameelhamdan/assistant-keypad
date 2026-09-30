package device

import (
	"encoding/json"
	"sort"
	"strconv"
	"sync"
	"time"
)

// Fake is an in-process keypad for tests and `keypad agent --fake-device`.
// Policy decides what it presses for each screen; nil = never press.
type Fake struct {
	ID     string
	Policy func(s FakeScreen) (key int, act string, idx *int, sel []int)
	Delay  time.Duration

	mu      sync.Mutex
	in      chan []byte
	out     chan []byte
	closed  chan struct{}
	once    sync.Once
	Screens []FakeScreen
	Status  []map[string]any
}

// FakeScreen is the part of a screen message the fake cares about.
type FakeScreen struct {
	T     string                       `json:"t"`
	ID    string                       `json:"id"`
	Tpl   string                       `json:"tpl"`
	Title string                       `json:"title"`
	Body  string                       `json:"body"`
	Items []string                     `json:"items"`
	Keys  map[string]map[string]string `json:"keys"`
	Click string                       `json:"click"`
}

// FirstKey presses the lowest-numbered bound key (or the first item).
func FirstKey(s FakeScreen) (int, string, *int, []int) {
	switch s.Tpl {
	case "list":
		i := 0
		return 1, "item", &i, nil
	case "multi":
		return 8, "confirm", nil, []int{0}
	}
	keys := make([]int, 0, len(s.Keys))
	for k := range s.Keys {
		n, _ := strconv.Atoi(k)
		keys = append(keys, n)
	}
	sort.Ints(keys)
	if len(keys) == 0 {
		return 0, s.Click, nil, nil
	}
	return keys[0], s.Keys[strconv.Itoa(keys[0])]["act"], nil, nil
}

// PressAct returns a policy that presses whichever key is bound to act.
func PressAct(act string) func(FakeScreen) (int, string, *int, []int) {
	return func(s FakeScreen) (int, string, *int, []int) {
		for k, v := range s.Keys {
			if v["act"] == act {
				n, _ := strconv.Atoi(k)
				return n, act, nil, nil
			}
		}
		return FirstKey(s)
	}
}

func NewFake(id string) *Fake {
	return &Fake{ID: id, in: make(chan []byte, 64), out: make(chan []byte, 64), closed: make(chan struct{})}
}

func (f *Fake) Kind() string { return "fake" }
func (f *Fake) Addr() string { return "memory" }

func (f *Fake) Close() error {
	f.once.Do(func() { close(f.closed) })
	return nil
}

// Send receives a host message (host -> fake).
func (f *Fake) Send(b []byte) error {
	select {
	case <-f.closed:
		return errClosed
	default:
	}
	f.handle(append([]byte(nil), b...))
	return nil
}

// Recv returns the next device message (fake -> host).
func (f *Fake) Recv() ([]byte, error) {
	select {
	case b := <-f.out:
		return b, nil
	case <-f.closed:
		return nil, errClosed
	}
}

func (f *Fake) emit(v map[string]any) {
	b, _ := json.Marshal(v)
	select {
	case f.out <- b:
	case <-f.closed:
	}
}

func (f *Fake) handle(b []byte) {
	var head struct {
		T  string `json:"t"`
		ID string `json:"id"`
	}
	if json.Unmarshal(b, &head) != nil {
		return
	}
	switch head.T {
	case "who", "ping":
		go f.emit(map[string]any{"t": "hello", "v": 2, "id": f.ID, "fw": "fake", "name": "Fake keypad", "link": "usb"})
	case "status":
		var m map[string]any
		_ = json.Unmarshal(b, &m)
		f.mu.Lock()
		f.Status = append(f.Status, m)
		f.mu.Unlock()
	case "screen":
		var s FakeScreen
		_ = json.Unmarshal(b, &s)
		f.mu.Lock()
		f.Screens = append(f.Screens, s)
		policy := f.Policy
		f.mu.Unlock()
		go func() {
			f.emit(map[string]any{"t": "ack", "id": s.ID})
			if policy == nil {
				return
			}
			time.Sleep(f.Delay)
			key, act, idx, sel := policy(s)
			m := map[string]any{"t": "press", "id": s.ID, "key": key, "act": act}
			if idx != nil {
				m["idx"] = *idx
			}
			if sel != nil {
				m["sel"] = sel
			}
			f.emit(m)
		}()
	case "close":
		go f.emit(map[string]any{"t": "ack", "id": head.ID})
	}
}

// SetPolicy changes the answering policy at runtime.
func (f *Fake) SetPolicy(p func(FakeScreen) (int, string, *int, []int)) {
	f.mu.Lock()
	f.Policy = p
	f.mu.Unlock()
}

// LastScreen returns the most recent screen, if any.
func (f *Fake) LastScreen() (FakeScreen, bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.Screens) == 0 {
		return FakeScreen{}, false
	}
	return f.Screens[len(f.Screens)-1], true
}

type closedErr struct{}

func (closedErr) Error() string { return "link closed" }

var errClosed = closedErr{}
