package device

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/libp2p/zeroconf/v2"

	"github.com/jameelhamdan/assistant-keypad/host/internal/proto"
)

// Seen is a keypad found on the network by mDNS.
type Seen struct {
	ID     string    `json:"id"`
	Addr   string    `json:"addr"`
	FW     string    `json:"fw"`
	Paired bool      `json:"paired"`
	At     time.Time `json:"at"`
}

type discovery struct {
	mu   sync.Mutex
	seen map[string]Seen
}

func newDiscovery() *discovery { return &discovery{seen: map[string]Seen{}} }

func (d *discovery) get(id string) (Seen, bool) {
	d.mu.Lock()
	defer d.mu.Unlock()
	s, ok := d.seen[id]
	return s, ok && time.Since(s.At) < 5*time.Minute
}

func (d *discovery) list() []Seen {
	d.mu.Lock()
	defer d.mu.Unlock()
	var out []Seen
	for _, s := range d.seen {
		if time.Since(s.At) < 5*time.Minute {
			out = append(out, s)
		}
	}
	return out
}

// run browses continuously, restarting the browse every 30 s so that
// keypads that changed IP are picked up quickly.
func (d *discovery) run(ctx context.Context, logf func(string, ...any)) {
	for ctx.Err() == nil {
		entries := make(chan *zeroconf.ServiceEntry, 16)
		bctx, cancel := context.WithTimeout(ctx, 30*time.Second)
		go func() {
			for e := range entries {
				d.add(e)
			}
		}()
		if err := zeroconf.Browse(bctx, proto.MDNSService, "local.", entries); err != nil {
			logf("mdns browse: %v", err)
		}
		<-bctx.Done()
		cancel()
	}
}

func (d *discovery) add(e *zeroconf.ServiceEntry) {
	s := Seen{ID: e.Instance, At: time.Now()}
	for _, t := range e.Text {
		k, v, _ := strings.Cut(t, "=")
		switch k {
		case "id":
			s.ID = v
		case "fw":
			s.FW = v
		case "paired":
			s.Paired = v == "1"
		}
	}
	if len(e.AddrIPv4) == 0 || !strings.HasPrefix(s.ID, "kp-") {
		return
	}
	s.Addr = fmt.Sprintf("%s:%d", e.AddrIPv4[0], e.Port)
	d.mu.Lock()
	d.seen[s.ID] = s
	d.mu.Unlock()
}
