package main

import (
	"context"
	"encoding/json"
	"io"
	"os"
	"time"

	"github.com/jameelhamdan/assistant-keypad/host/internal/core"
	"github.com/jameelhamdan/assistant-keypad/host/internal/ipc"
	"github.com/jameelhamdan/assistant-keypad/host/internal/osutil"
)

// Events whose hook may wait for a key press.
var blocking = map[string]bool{"PermissionRequest": true, "Stop": true, "PreToolUse": true}

// runHook is the Claude Code command hook. It must never fail: whatever
// happens, it prints a JSON object and exits 0. `{}` means "no decision".
func runHook(args []string) int {
	out := []byte("{}")
	defer func() {
		recover()
		os.Stdout.Write(out)
	}()
	if len(args) != 1 {
		return 0
	}
	event := args[0]
	raw, err := io.ReadAll(io.LimitReader(os.Stdin, 5<<20))
	if err != nil {
		return 0
	}
	var payload map[string]any
	if json.Unmarshal(raw, &payload) != nil {
		return 0
	}
	ppid := os.Getppid()
	req := core.HookRequest{
		Event:   event,
		PIDs:    []int{ppid, osutil.ParentPID(ppid)},
		Worker:  os.Getenv(core.WorkerEnv) == "1",
		Payload: slim(event, payload),
	}
	wait := 10 * time.Second
	if blocking[event] {
		wait = time.Hour + 20*time.Second
	}
	ctx, cancel := context.WithTimeout(context.Background(), wait)
	defer cancel()
	var reply map[string]any
	if err := ipc.NewClient().Do(ctx, "POST", "/hook", req, &reply); err != nil || reply == nil {
		return 0 // agent not running: behave as if no hook existed
	}
	if b, err := json.Marshal(reply); err == nil {
		out = b
	}
	return 0
}

// slim keeps only what the agent displays or needs to answer. Tool output,
// file contents and transcripts never leave the hook process.
func slim(event string, p map[string]any) map[string]any {
	out := map[string]any{}
	for _, k := range []string{"session_id", "cwd", "hook_event_name", "permission_mode", "agent_id", "agent_type",
		"source", "reason", "notification_type", "error_type", "stop_hook_active"} {
		if v, ok := p[k]; ok {
			out[k] = v
		}
	}
	for k, n := range map[string]int{"prompt": 400, "last_assistant_message": 600, "message": 300, "error": 400} {
		if s, ok := p[k].(string); ok {
			out[k] = clip(s, n)
		}
	}
	tool, _ := p["tool_name"].(string)
	if tool == "" {
		return out
	}
	out["tool_name"] = tool
	in, _ := p["tool_input"].(map[string]any)
	if tool == "AskUserQuestion" {
		out["tool_input"] = in // echoed back in updatedInput, must be complete
		return out
	}
	keep := map[string]any{}
	for _, k := range []string{"command", "description", "file_path", "notebook_path", "pattern", "url", "query", "prompt", "title", "path", "plan"} {
		if s, ok := in[k].(string); ok {
			keep[k] = clip(s, 600)
		}
	}
	if len(keep) == 0 {
		n := 0
		for k, v := range in {
			if s, ok := v.(string); ok && n < 4 {
				keep[k] = clip(s, 200)
				n++
			}
		}
	}
	out["tool_input"] = keep
	return out
}

func clip(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n]) + "…"
	}
	return s
}
