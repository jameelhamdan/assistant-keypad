// Package server exposes the agent over HTTP: the private IPC endpoint
// (hook/MCP shims, tray) and the loopback settings UI, which shares the same
// routes behind a one-time token.
package server

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log/slog"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/jameelhamdan/assistant-keypad/host/internal/claudecfg"
	"github.com/jameelhamdan/assistant-keypad/host/internal/config"
	"github.com/jameelhamdan/assistant-keypad/host/internal/core"
	"github.com/jameelhamdan/assistant-keypad/host/internal/firmware"
	"github.com/jameelhamdan/assistant-keypad/host/internal/osutil"
	"github.com/jameelhamdan/assistant-keypad/host/internal/proto"
	"github.com/jameelhamdan/assistant-keypad/host/internal/webui"
)

type Server struct {
	A        *core.Agent
	Bin      string        // absolute path of this executable (for hooks)
	Firmware func() []byte // bundled keypad firmware, nil if none
	Log      *slog.Logger
	Quit     func() // stops the agent

	uiOnce sync.Once
	uiURL  string
	token  string

	otaMu  sync.Mutex
	otaPct map[string]int
}

// ServeIPC serves the private endpoint until the listener closes.
func (s *Server) ServeIPC(l net.Listener) error {
	return (&http.Server{Handler: s.routes(), ReadHeaderTimeout: 5 * time.Second}).Serve(l)
}

// UIURL starts the loopback settings server on first use and returns a URL
// carrying a fresh session token.
func (s *Server) UIURL() (string, error) {
	var err error
	s.uiOnce.Do(func() {
		b := make([]byte, 16)
		_, _ = rand.Read(b)
		s.token = hex.EncodeToString(b)
		var l net.Listener
		l, err = net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			return
		}
		s.uiURL = "http://" + l.Addr().String()
		go (&http.Server{Handler: s.guard(l.Addr().String()), ReadHeaderTimeout: 5 * time.Second}).Serve(l)
	})
	if err != nil {
		return "", err
	}
	return s.uiURL + "/?token=" + s.token, nil
}

// guard protects the loopback UI: exact Host (DNS rebinding), token
// cookie, and a custom header on API calls (cross-site requests can't set it).
func (s *Server) guard(host string) http.Handler {
	static, _ := fs.Sub(webui.Files, "static")
	files := http.FileServerFS(static)
	api := s.routes()
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Host != host {
			http.Error(w, "bad host", http.StatusForbidden)
			return
		}
		if t := r.URL.Query().Get("token"); t != "" && t == s.token {
			http.SetCookie(w, &http.Cookie{Name: "keypad", Value: s.token, Path: "/", HttpOnly: true, SameSite: http.SameSiteStrictMode})
			http.Redirect(w, r, "/", http.StatusFound)
			return
		}
		if c, err := r.Cookie("keypad"); err != nil || c.Value != s.token {
			http.Error(w, "Open the settings from the Keypad tray icon.", http.StatusUnauthorized)
			return
		}
		if len(r.URL.Path) > 5 && r.URL.Path[:5] == "/api/" {
			if r.Header.Get("X-Keypad") != "1" {
				http.Error(w, "missing header", http.StatusForbidden)
				return
			}
			r2 := r.Clone(r.Context())
			r2.URL.Path = r.URL.Path[4:]
			api.ServeHTTP(w, r2)
			return
		}
		w.Header().Set("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'")
		files.ServeHTTP(w, r)
	})
}

func (s *Server) routes() http.Handler {
	mux := http.NewServeMux()
	a := s.A

	// ---- Claude Code shims ----
	mux.HandleFunc("POST /hook", func(w http.ResponseWriter, r *http.Request) {
		var req core.HookRequest
		if !decode(w, r, &req) {
			return
		}
		reply(w, a.Hook(r.Context(), req))
	})
	mux.HandleFunc("POST /ask", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			PID       int             `json:"pid"`
			Cwd       string          `json:"cwd"`
			Questions []core.Question `json:"questions"`
		}
		if !decode(w, r, &req) {
			return
		}
		type ans struct {
			Values []string `json:"values"`
			Yes    bool     `json:"yes"`
			Error  string   `json:"error,omitempty"`
		}
		var out []ans
		for _, x := range a.Ask(r.Context(), req.PID, req.Cwd, req.Questions) {
			e := ""
			if x.Err != nil {
				e = x.Err.Error()
			}
			out = append(out, ans{Values: x.Values, Yes: x.Yes, Error: e})
		}
		reply(w, out)
	})
	mux.HandleFunc("POST /toast", func(w http.ResponseWriter, r *http.Request) {
		var req struct{ Text, Level string }
		if !decode(w, r, &req) {
			return
		}
		a.Toast(req.Text, req.Level, 3000)
		reply(w, ok())
	})

	// ---- status and control ----
	mux.HandleFunc("GET /status", func(w http.ResponseWriter, r *http.Request) { reply(w, a.Snapshot()) })
	mux.HandleFunc("POST /pause", func(w http.ResponseWriter, r *http.Request) {
		var req struct{ Paused bool }
		if !decode(w, r, &req) {
			return
		}
		a.SetPaused(req.Paused)
		reply(w, ok())
	})
	mux.HandleFunc("POST /quit", func(w http.ResponseWriter, r *http.Request) {
		reply(w, ok())
		if s.Quit != nil {
			go func() { time.Sleep(100 * time.Millisecond); s.Quit() }()
		}
	})
	mux.HandleFunc("GET /ui", func(w http.ResponseWriter, r *http.Request) {
		u, err := s.UIURL()
		if err != nil {
			fail(w, err)
			return
		}
		reply(w, map[string]string{"url": u})
	})
	mux.HandleFunc("POST /shortcut", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Index   int    `json:"index"`
			Session string `json:"session"`
		}
		if !decode(w, r, &req) {
			return
		}
		if req.Session == "" {
			cur, _ := a.Sessions.Current()
			req.Session = cur.ID
		}
		go a.QueueShortcut(req.Index, req.Session)
		reply(w, ok())
	})
	mux.HandleFunc("POST /workers/{pid}/stop", func(w http.ResponseWriter, r *http.Request) {
		pid, _ := strconv.Atoi(r.PathValue("pid"))
		result(w, a.Workers.Stop(pid))
	})

	// ---- settings ----
	mux.HandleFunc("GET /config", func(w http.ResponseWriter, r *http.Request) { reply(w, a.Config()) })
	mux.HandleFunc("PUT /config", func(w http.ResponseWriter, r *http.Request) {
		c := config.Default()
		if !decode(w, r, &c) {
			return
		}
		if err := config.Save(c); err != nil {
			fail(w, err)
			return
		}
		a.SetConfig(c)
		// keep Claude Code's own continue cap in step
		if st := claudecfg.Check(s.Bin); st.Hooks > 0 {
			_ = claudecfg.Install(s.Bin, c.Behavior.MaxContinues)
		}
		reply(w, c)
	})
	mux.HandleFunc("GET /ssid", func(w http.ResponseWriter, r *http.Request) {
		reply(w, map[string]string{"ssid": osutil.SSID()})
	})

	// ---- keypads ----
	mux.HandleFunc("PATCH /devices/{id}", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Name       *string   `json:"name"`
			Theme      *string   `json:"theme"`
			Brightness *int      `json:"brightness"`
			Projects   *[]string `json:"projects"`
		}
		if !decode(w, r, &req) {
			return
		}
		id := r.PathValue("id")
		if _, known := a.Store.Device(id); !known {
			fail(w, errors.New("unknown keypad"))
			return
		}
		_, err := a.Store.Update(id, func(d *config.Device) {
			if req.Name != nil && *req.Name != "" {
				d.Name = clip(*req.Name, 24)
			}
			if req.Theme != nil && (*req.Theme == "dark" || *req.Theme == "light" || *req.Theme == "system") {
				d.Theme = *req.Theme
			}
			if req.Brightness != nil {
				d.Brightness = max(5, min(100, *req.Brightness))
			}
			if req.Projects != nil {
				d.Projects = *req.Projects
			}
		})
		if err == nil {
			a.Hub.SendSettings(id)
			a.Refresh() // the project filter may have changed
		}
		result(w, err)
	})
	mux.HandleFunc("POST /devices/{id}/provision", func(w http.ResponseWriter, r *http.Request) {
		var req struct{ SSID, Pass, Name string }
		if !decode(w, r, &req) {
			return
		}
		if req.SSID == "" {
			fail(w, errors.New("enter the Wi-Fi network name"))
			return
		}
		result(w, a.Hub.Provision(r.PathValue("id"), req.SSID, req.Pass, clip(req.Name, 24)))
	})
	mux.HandleFunc("POST /devices/{id}/unpair", func(w http.ResponseWriter, r *http.Request) {
		result(w, a.Hub.Unpair(r.PathValue("id")))
	})
	mux.HandleFunc("POST /devices/{id}/identify", func(w http.ResponseWriter, r *http.Request) {
		c := a.Hub.Get(r.PathValue("id"))
		if c == nil {
			fail(w, errors.New("keypad not connected"))
			return
		}
		host, _ := os.Hostname()
		result(w, c.Send(proto.Toast{T: "toast", Text: "This is " + firstField(host), Level: "ok", MS: 4000}))
	})
	mux.HandleFunc("POST /devices/{id}/update", func(w http.ResponseWriter, r *http.Request) {
		var req struct{ Path string }
		if !decode(w, r, &req) {
			return
		}
		bin := s.firmware()
		if req.Path != "" {
			b, err := os.ReadFile(req.Path)
			if err != nil || len(b) < 1024 || len(b) > 8<<20 {
				fail(w, fmt.Errorf("not a firmware image: %s", req.Path))
				return
			}
			bin = b
		}
		if bin == nil {
			fail(w, errors.New("this build has no bundled firmware"))
			return
		}
		id := r.PathValue("id")
		s.setOTA(id, 0)
		go func() {
			err := a.Hub.UpdateFirmware(id, bin, func(p int) { s.setOTA(id, p) })
			if err != nil {
				s.Log.Warn("firmware update failed", "id", id, "err", err)
				s.setOTA(id, -1)
				return
			}
			s.setOTA(id, 100)
		}()
		reply(w, ok())
	})
	mux.HandleFunc("GET /devices/{id}/update", func(w http.ResponseWriter, r *http.Request) {
		s.otaMu.Lock()
		p, found := s.otaPct[r.PathValue("id")]
		s.otaMu.Unlock()
		reply(w, map[string]any{"progress": p, "running": found})
	})
	mux.HandleFunc("GET /firmware", func(w http.ResponseWriter, r *http.Request) {
		reply(w, map[string]any{"bundled": s.firmware() != nil, "version": firmware.Version})
	})

	// ---- Claude Code integration ----
	mux.HandleFunc("GET /claude", func(w http.ResponseWriter, r *http.Request) {
		st := claudecfg.Check(s.Bin)
		reply(w, map[string]any{"status": st, "installed": st.Installed(), "claude": core.FindClaude()})
	})
	mux.HandleFunc("POST /claude/install", func(w http.ResponseWriter, r *http.Request) {
		result(w, claudecfg.Install(s.Bin, a.Config().Behavior.MaxContinues))
	})
	mux.HandleFunc("POST /claude/uninstall", func(w http.ResponseWriter, r *http.Request) {
		result(w, claudecfg.Uninstall())
	})
	mux.HandleFunc("POST /logs/open", func(w http.ResponseWriter, r *http.Request) {
		result(w, osutil.Open(config.LogDir()))
	})
	return mux
}

func (s *Server) firmware() []byte {
	if s.Firmware == nil {
		return nil
	}
	return s.Firmware()
}

func (s *Server) setOTA(id string, p int) {
	s.otaMu.Lock()
	defer s.otaMu.Unlock()
	if s.otaPct == nil {
		s.otaPct = map[string]int{}
	}
	s.otaPct[id] = p
}

// ---- helpers --------------------------------------------------------------------

func decode(w http.ResponseWriter, r *http.Request, v any) bool {
	if err := json.NewDecoder(io.LimitReader(r.Body, 2<<20)).Decode(v); err != nil && !errors.Is(err, io.EOF) {
		http.Error(w, `{"error":"invalid JSON"}`, http.StatusBadRequest)
		return false
	}
	return true
}

func reply(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}

func fail(w http.ResponseWriter, err error) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusBadRequest)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
}

func result(w http.ResponseWriter, err error) {
	if err != nil {
		fail(w, err)
		return
	}
	reply(w, ok())
}

func ok() map[string]bool { return map[string]bool{"ok": true} }

func firstField(host string) string {
	if i := strings.IndexByte(host, '.'); i > 0 {
		return host[:i]
	}
	return host
}

func clip(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}
