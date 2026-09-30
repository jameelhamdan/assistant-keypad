// Package claudecfg installs and removes the keypad's Claude Code
// integration: command hooks in ~/.claude/settings.json and a user-scope
// MCP server. Only entries that run our own binary are ever touched, and
// every write is preceded by a timestamped backup.
package claudecfg

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// Blocking hooks wait for a key press; the agent enforces the real timeout
// (at most 3600 s), so Claude Code must never cut them off first.
const (
	blockingTimeout = 3630
	quickTimeout    = 15
	MCPName         = "keypad"
)

var Events = map[string]int{
	"SessionStart": quickTimeout, "SessionEnd": quickTimeout, "UserPromptSubmit": quickTimeout,
	"PreToolUse": blockingTimeout, "PostToolUse": quickTimeout, "PostToolUseFailure": quickTimeout,
	"PermissionRequest": blockingTimeout, "Stop": blockingTimeout, "StopFailure": quickTimeout,
	"SubagentStart": quickTimeout, "SubagentStop": quickTimeout, "Notification": quickTimeout,
	"PreCompact": quickTimeout,
}

func home() string {
	if d := os.Getenv("CLAUDE_CONFIG_DIR"); d != "" {
		return d
	}
	h, _ := os.UserHomeDir()
	return filepath.Join(h, ".claude")
}

// SettingsPath is the user-scope Claude Code settings file.
func SettingsPath() string { return filepath.Join(home(), "settings.json") }

// Status describes what is installed.
type Status struct {
	Hooks    int    `json:"hooks"`    // number of our hook entries
	Expected int    `json:"expected"` // number we would install
	MCP      bool   `json:"mcp"`      // MCP server registered
	Binary   string `json:"binary"`   // binary the hooks point at
	Stale    bool   `json:"stale"`    // hooks point at another binary path
	Settings string `json:"settings"`
}

func (s Status) Installed() bool { return s.Hooks == s.Expected && s.MCP && !s.Stale }

func readSettings() (map[string]any, error) {
	b, err := os.ReadFile(SettingsPath())
	if errors.Is(err, os.ErrNotExist) {
		return map[string]any{}, nil
	}
	if err != nil {
		return nil, err
	}
	m := map[string]any{}
	if len(strings.TrimSpace(string(b))) == 0 {
		return m, nil
	}
	if err := json.Unmarshal(b, &m); err != nil {
		return nil, fmt.Errorf("%s is not valid JSON: %w", SettingsPath(), err)
	}
	return m, nil
}

func writeSettings(m map[string]any) error {
	p := SettingsPath()
	if err := os.MkdirAll(filepath.Dir(p), 0o700); err != nil {
		return err
	}
	if old, err := os.ReadFile(p); err == nil {
		bak := p + ".keypad-backup-" + time.Now().Format("20060102-150405")
		if err := os.WriteFile(bak, old, 0o600); err != nil {
			return err
		}
	}
	b, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	tmp := p + ".tmp"
	if err := os.WriteFile(tmp, append(b, '\n'), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, p)
}

// ours reports whether a hook handler runs a keypad binary, and which one.
func ours(h any) (string, bool) {
	m, ok := h.(map[string]any)
	if !ok {
		return "", false
	}
	cmd, _ := m["command"].(string)
	args, _ := m["args"].([]any)
	name := strings.TrimSuffix(strings.ToLower(filepath.Base(cmd)), ".exe")
	if name != "keypad" || len(args) == 0 || args[0] != "hook" {
		return "", false
	}
	return cmd, true
}

// strip removes our handlers from hooks[event], dropping empty groups.
func strip(hooks map[string]any) {
	for ev, v := range hooks {
		groups, _ := v.([]any)
		var keep []any
		for _, g := range groups {
			gm, ok := g.(map[string]any)
			if !ok {
				keep = append(keep, g)
				continue
			}
			list, _ := gm["hooks"].([]any)
			var rest []any
			for _, h := range list {
				if _, mine := ours(h); !mine {
					rest = append(rest, h)
				}
			}
			if len(rest) == 0 && len(list) > 0 {
				continue
			}
			gm["hooks"] = rest
			keep = append(keep, gm)
		}
		if len(keep) == 0 {
			delete(hooks, ev)
		} else {
			hooks[ev] = keep
		}
	}
}

// Install adds hooks for bin and registers the MCP server. maxContinues
// raises Claude Code's own Stop-hook block cap to the same number.
func Install(bin string, maxContinues int) error {
	m, err := readSettings()
	if err != nil {
		return err
	}
	hooks, _ := m["hooks"].(map[string]any)
	if hooks == nil {
		hooks = map[string]any{}
	}
	strip(hooks)
	for ev, to := range Events {
		entry := map[string]any{"type": "command", "command": bin, "args": []any{"hook", ev}, "timeout": to}
		groups, _ := hooks[ev].([]any)
		hooks[ev] = append(groups, map[string]any{"hooks": []any{entry}})
	}
	m["hooks"] = hooks
	env, _ := m["env"].(map[string]any)
	if env == nil {
		env = map[string]any{}
	}
	env["CLAUDE_CODE_STOP_HOOK_BLOCK_CAP"] = strconv.Itoa(maxContinues)
	m["env"] = env
	if err := writeSettings(m); err != nil {
		return err
	}
	return registerMCP(bin)
}

// Uninstall removes everything Install added.
func Uninstall() error {
	m, err := readSettings()
	if err != nil {
		return err
	}
	if hooks, ok := m["hooks"].(map[string]any); ok {
		strip(hooks)
		if len(hooks) == 0 {
			delete(m, "hooks")
		}
	}
	if env, ok := m["env"].(map[string]any); ok {
		delete(env, "CLAUDE_CODE_STOP_HOOK_BLOCK_CAP")
		if len(env) == 0 {
			delete(m, "env")
		}
	}
	if err := writeSettings(m); err != nil {
		return err
	}
	return unregisterMCP()
}

// Check inspects the current settings.
func Check(bin string) Status {
	st := Status{Expected: len(Events), Binary: bin, Settings: SettingsPath()}
	m, err := readSettings()
	if err != nil {
		return st
	}
	hooks, _ := m["hooks"].(map[string]any)
	for ev := range Events {
		groups, _ := hooks[ev].([]any)
		for _, g := range groups {
			gm, _ := g.(map[string]any)
			list, _ := gm["hooks"].([]any)
			for _, h := range list {
				if cmd, mine := ours(h); mine {
					st.Hooks++
					if cmd != bin {
						st.Stale = true
					}
				}
			}
		}
	}
	st.MCP = mcpRegistered(bin)
	return st
}

// ---- MCP registration (user scope, in ~/.claude.json) --------------------------

func claudeJSON() string {
	if d := os.Getenv("CLAUDE_CONFIG_DIR"); d != "" {
		return filepath.Join(d, ".claude.json")
	}
	h, _ := os.UserHomeDir()
	return filepath.Join(h, ".claude.json")
}

// FindClaude is set by the caller to locate the claude CLI.
var FindClaude = func() string { p, _ := exec.LookPath("claude"); return p }

func registerMCP(bin string) error {
	if c := FindClaude(); c != "" {
		_ = exec.Command(c, "mcp", "remove", "--scope", "user", MCPName).Run()
		out, err := exec.Command(c, "mcp", "add", "--scope", "user", MCPName, "--", bin, "mcp").CombinedOutput()
		if err == nil {
			return nil
		}
		return fmt.Errorf("claude mcp add: %v: %s", err, strings.TrimSpace(string(out)))
	}
	return editClaudeJSON(func(servers map[string]any) {
		servers[MCPName] = map[string]any{"type": "stdio", "command": bin, "args": []any{"mcp"}}
	})
}

func unregisterMCP() error {
	if c := FindClaude(); c != "" {
		_ = exec.Command(c, "mcp", "remove", "--scope", "user", MCPName).Run()
		return nil
	}
	return editClaudeJSON(func(servers map[string]any) { delete(servers, MCPName) })
}

func editClaudeJSON(fn func(servers map[string]any)) error {
	p := claudeJSON()
	m := map[string]any{}
	b, err := os.ReadFile(p)
	if err == nil {
		if err := json.Unmarshal(b, &m); err != nil {
			return fmt.Errorf("%s is not valid JSON: %w", p, err)
		}
		if err := os.WriteFile(p+".keypad-backup", b, 0o600); err != nil {
			return err
		}
	}
	servers, _ := m["mcpServers"].(map[string]any)
	if servers == nil {
		servers = map[string]any{}
	}
	fn(servers)
	m["mcpServers"] = servers
	out, _ := json.MarshalIndent(m, "", "  ")
	return os.WriteFile(p, out, 0o600)
}

func mcpRegistered(bin string) bool {
	b, err := os.ReadFile(claudeJSON())
	if err != nil {
		return false
	}
	var m struct {
		MCPServers map[string]struct {
			Command string `json:"command"`
		} `json:"mcpServers"`
	}
	if json.Unmarshal(b, &m) != nil {
		return false
	}
	s, ok := m.MCPServers[MCPName]
	return ok && s.Command == bin
}
