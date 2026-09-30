package core

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/jameelhamdan/assistant-keypad/host/internal/config"
	"github.com/jameelhamdan/assistant-keypad/host/internal/device"
)

type env struct {
	a    *Agent
	fake *device.Fake
	idle time.Duration
}

func setup(t *testing.T, policy func(device.FakeScreen) (int, string, *int, []int)) *env {
	t.Helper()
	t.Setenv("KEYPAD_HOME", t.TempDir())
	store, err := config.OpenStore()
	if err != nil {
		t.Fatal(err)
	}
	e := &env{idle: time.Hour}
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	a := NewAgent(config.Default(), store, func() (time.Duration, bool) { return e.idle, true }, log)
	hub := device.NewHub(store, a, device.Options{Log: log})
	a.SetHub(hub)
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	go a.Run(ctx)
	e.a = a
	if policy != nil {
		e.fake = device.NewFake("kp-000001")
		e.fake.Policy = policy
		go hub.Serve(e.fake)
		t.Cleanup(func() { e.fake.Close() })
		deadline := time.Now().Add(2 * time.Second)
		for len(hub.Conns()) == 0 {
			if time.Now().After(deadline) {
				t.Fatal("fake keypad never connected")
			}
			time.Sleep(5 * time.Millisecond)
		}
	}
	return e
}

func (e *env) hook(event string, p map[string]any) map[string]any {
	if p["session_id"] == nil {
		p["session_id"] = "sess-0001-aaaa"
	}
	if p["cwd"] == nil {
		p["cwd"] = "/work/money-mind"
	}
	return e.a.Hook(context.Background(), HookRequest{Event: event, PIDs: []int{42, 1}, Payload: p})
}

func behavior(out map[string]any) string {
	h, _ := out["hookSpecificOutput"].(map[string]any)
	d, _ := h["decision"].(map[string]any)
	s, _ := d["behavior"].(string)
	return s
}

func TestPermissionAllow(t *testing.T) {
	e := setup(t, device.PressAct("allow"))
	e.hook("SessionStart", map[string]any{})
	out := e.hook("PermissionRequest", map[string]any{"tool_name": "Bash", "tool_input": map[string]any{"command": "go test ./..."}})
	if behavior(out) != "allow" {
		t.Fatalf("want allow, got %v", out)
	}
	s, _ := e.fake.LastScreen()
	if s.Keys["1"]["act"] != "allow" || s.Keys["4"]["act"] != "deny" || s.Keys["8"]["act"] != "pc" {
		t.Fatalf("unexpected key map %v", s.Keys)
	}
	if s.Body != "go test ./..." {
		t.Fatalf("body %q", s.Body)
	}
}

func TestPermissionDeny(t *testing.T) {
	e := setup(t, device.PressAct("deny"))
	out := e.hook("PermissionRequest", map[string]any{"tool_name": "Write", "tool_input": map[string]any{"file_path": "/x/a.go"}})
	if behavior(out) != "deny" {
		t.Fatalf("want deny, got %v", out)
	}
}

func TestPermissionToPCFallsBack(t *testing.T) {
	e := setup(t, device.PressAct("pc"))
	out := e.hook("PermissionRequest", map[string]any{"tool_name": "Bash", "tool_input": map[string]any{"command": "rm -rf x"}})
	if len(out) != 0 {
		t.Fatalf("want {}, got %v", out)
	}
}

func TestNoKeypadIsInstantNoOp(t *testing.T) {
	e := setup(t, nil)
	start := time.Now()
	out := e.hook("PermissionRequest", map[string]any{"tool_name": "Bash", "tool_input": map[string]any{"command": "ls"}})
	if len(out) != 0 || time.Since(start) > 100*time.Millisecond {
		t.Fatalf("want instant {}, got %v after %v", out, time.Since(start))
	}
}

func TestPausedIsNoOp(t *testing.T) {
	e := setup(t, device.PressAct("allow"))
	e.a.SetPaused(true)
	if out := e.hook("PermissionRequest", map[string]any{"tool_name": "Bash"}); len(out) != 0 {
		t.Fatalf("paused agent decided: %v", out)
	}
}

func TestTimeoutFallsBack(t *testing.T) {
	e := setup(t, func(device.FakeScreen) (int, string, *int, []int) { return 0, "", nil, nil })
	e.fake.SetPolicy(nil) // never press
	cfg := config.Default()
	cfg.Behavior.Timeouts.Permission = 1
	e.a.SetConfig(cfg)
	out := e.hook("PermissionRequest", map[string]any{"tool_name": "Bash"})
	if len(out) != 0 {
		t.Fatalf("want {}, got %v", out)
	}
}

func TestHandbackOnPCInput(t *testing.T) {
	e := setup(t, device.FirstKey)
	e.fake.SetPolicy(nil)
	e.idle = 0 // the user keeps typing at the PC
	start := time.Now()
	out := e.hook("PermissionRequest", map[string]any{"tool_name": "Bash"})
	if len(out) != 0 || time.Since(start) > 4*time.Second {
		t.Fatalf("want quick hand-back, got %v after %v", out, time.Since(start))
	}
}

func TestNoHandbackWhenPCIsIdle(t *testing.T) {
	e := setup(t, device.FirstKey)
	e.fake.SetPolicy(nil)
	e.idle = time.Hour
	cfg := config.Default()
	cfg.Behavior.Timeouts.Permission = 3
	e.a.SetConfig(cfg)
	start := time.Now()
	e.hook("PermissionRequest", map[string]any{"tool_name": "Bash"})
	if time.Since(start) < 2900*time.Millisecond {
		t.Fatalf("handed back after %v although nobody used the PC", time.Since(start))
	}
}

func TestStopContinue(t *testing.T) {
	e := setup(t, device.PressAct("continue"))
	out := e.hook("Stop", map[string]any{"last_assistant_message": "All done."})
	if out["decision"] != "block" {
		t.Fatalf("want block, got %v", out)
	}
	if n := e.a.Sessions.Continues("sess-0001-aaaa"); n != 1 {
		t.Fatalf("continues = %d", n)
	}
}

func TestStopDone(t *testing.T) {
	e := setup(t, device.PressAct("done"))
	if out := e.hook("Stop", map[string]any{}); len(out) != 0 {
		t.Fatalf("want {}, got %v", out)
	}
}

func TestStopWithShortcut(t *testing.T) {
	e := setup(t, func(s device.FakeScreen) (int, string, *int, []int) {
		if s.Tpl == "list" {
			i := 2
			return 3, "item", &i, nil
		}
		return device.PressAct("shortcuts")(s)
	})
	out := e.hook("Stop", map[string]any{})
	reason, _ := out["reason"].(string)
	if out["decision"] != "block" || !contains(reason, config.Default().Shortcuts[2].Prompt) {
		t.Fatalf("got %v", out)
	}
}

func TestContinueLimit(t *testing.T) {
	e := setup(t, device.PressAct("no"))
	cfg := config.Default()
	cfg.Behavior.MaxContinues = 1
	e.a.SetConfig(cfg)
	e.a.Sessions.AddContinue("sess-0001-aaaa")
	if out := e.hook("Stop", map[string]any{}); len(out) != 0 {
		t.Fatalf("limit not enforced: %v", out)
	}
	s, _ := e.fake.LastScreen()
	if s.Title != "Continue limit reached" {
		t.Fatalf("screen %q", s.Title)
	}
}

func TestAskUserQuestion(t *testing.T) {
	e := setup(t, func(s device.FakeScreen) (int, string, *int, []int) {
		if s.Tpl == "multi" {
			return 8, "confirm", nil, []int{0, 2}
		}
		i := 1
		return 2, "item", &i, nil
	})
	in := map[string]any{"questions": []any{
		map[string]any{"question": "Which DB?", "header": "DB", "options": []any{
			map[string]any{"label": "Postgres"}, map[string]any{"label": "SQLite"}}},
		map[string]any{"question": "Checks?", "multiSelect": true, "options": []any{
			map[string]any{"label": "lint"}, map[string]any{"label": "test"}, map[string]any{"label": "build"}}},
	}}
	out := e.hook("PreToolUse", map[string]any{"tool_name": "AskUserQuestion", "tool_input": in})
	h, _ := out["hookSpecificOutput"].(map[string]any)
	upd, _ := h["updatedInput"].(map[string]any)
	ans, _ := upd["answers"].(map[string]any)
	if h["permissionDecision"] != "allow" || ans["Which DB?"] != "SQLite" || ans["Checks?"] != "lint, build" {
		t.Fatalf("got %v", out)
	}
}

func TestShortcutDeliveredOnNextTool(t *testing.T) {
	e := setup(t, device.FirstKey)
	e.hook("SessionStart", map[string]any{})
	e.hook("UserPromptSubmit", map[string]any{"prompt": "hi"})
	e.a.QueueShortcut(1, "sess-0001-aaaa") // busy session -> queued
	out := e.hook("PostToolUse", map[string]any{"tool_name": "Bash"})
	h, _ := out["hookSpecificOutput"].(map[string]any)
	if ctx, _ := h["additionalContext"].(string); !contains(ctx, config.Default().Shortcuts[1].Prompt) {
		t.Fatalf("shortcut not delivered: %v", out)
	}
}

func TestProjectFilter(t *testing.T) {
	e := setup(t, device.PressAct("allow"))
	_, _ = e.a.Store.Update("kp-000001", func(d *config.Device) { d.Projects = []string{"other"} })
	out := e.hook("PermissionRequest", map[string]any{"tool_name": "Bash"})
	if len(out) != 0 {
		t.Fatalf("filtered keypad answered: %v", out)
	}
}

func TestSessionsAndStatus(t *testing.T) {
	e := setup(t, device.FirstKey)
	e.hook("SessionStart", map[string]any{"session_id": "aaaaaaaa-1", "cwd": "/w/one"})
	e.hook("SessionStart", map[string]any{"session_id": "bbbbbbbb-2", "cwd": "/w/two"})
	if cur, _ := e.a.Sessions.Current(); cur.Project != "two" {
		t.Fatalf("current = %q", cur.Project)
	}
	e.a.Sessions.Select("aaaaaaaa")
	e.hook("PreToolUse", map[string]any{"session_id": "bbbbbbbb-2", "cwd": "/w/two", "tool_name": "Read"})
	if cur, _ := e.a.Sessions.Current(); cur.Project != "one" {
		t.Fatalf("pin not honoured, current = %q", cur.Project)
	}
	if s, ok := e.a.Sessions.ByPID(42); !ok || s.ID == "" {
		t.Fatal("ByPID failed")
	}
}

func TestRedact(t *testing.T) {
	for in, want := range map[string]string{
		"export API_KEY=abc123":         "export API_KEY=***",
		"curl -H 'Authorization: xyz'":  "curl -H 'Authorization: ***",
		"token sk-abcdefghijklmnop1234": "token ***",
	} {
		if got := Redact(in); got != want {
			t.Errorf("Redact(%q) = %q, want %q", in, got, want)
		}
	}
}

func contains(s, sub string) bool {
	return len(sub) > 0 && len(s) >= len(sub) && (s == sub || indexOf(s, sub) >= 0)
}

func indexOf(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}

func TestSnapshotListsAreNeverNull(t *testing.T) {
	e := setup(t, nil)
	b, _ := json.Marshal(e.a.Snapshot())
	if strings.Contains(string(b), "null") {
		t.Fatalf("snapshot has null lists: %s", b)
	}
}

func TestSnapshotNeverExposesPairingKeys(t *testing.T) {
	e := setup(t, nil)
	_, _ = e.a.Store.Update("kp-000002", func(d *config.Device) { d.Key = "deadbeef" })
	b, _ := json.Marshal(e.a.Snapshot())
	if strings.Contains(string(b), "deadbeef") || !strings.Contains(string(b), `"paired":true`) {
		t.Fatalf("snapshot: %s", b)
	}
}
