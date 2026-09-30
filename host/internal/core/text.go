package core

import (
	"path/filepath"
	"regexp"
	"strings"
	"unicode"
)

var (
	secretRe = regexp.MustCompile(`(?i)(api[_-]?key|secret|token|password|passwd|authorization|bearer)(\s*[=:]\s*)\S+`)
	tokenRe  = regexp.MustCompile(`\b(sk-[A-Za-z0-9_\-]{12,}|ghp_[A-Za-z0-9]{20,}|gh[ousr]_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9\-]{10,}|AKIA[0-9A-Z]{16})\b`)
	urlRe    = regexp.MustCompile(`^https?://`)
)

// Redact hides obvious secrets before text reaches a screen or a log.
func Redact(s string) string {
	s = secretRe.ReplaceAllString(s, "$1$2***")
	return tokenRe.ReplaceAllString(s, "***")
}

// clip shortens s to n runes, marking the cut with an ellipsis.
func clip(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n-1]) + "…"
}

func firstLine(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		return strings.TrimSpace(s[:i])
	}
	return s
}

func base(p string) string {
	p = strings.TrimRight(strings.ReplaceAll(p, `\`, "/"), "/")
	if i := strings.LastIndexByte(p, '/'); i >= 0 {
		return p[i+1:]
	}
	return p
}

func projectOf(cwd string) string {
	if cwd == "" {
		return ""
	}
	return base(filepath.Clean(cwd))
}

func str(m map[string]any, k string) string {
	if v, ok := m[k].(string); ok {
		return v
	}
	return ""
}

// ToolName shortens MCP tool names: mcp__server__tool -> server:tool.
func ToolName(name string) string {
	if p := strings.Split(name, "__"); len(p) >= 3 && p[0] == "mcp" {
		return p[1] + ":" + p[2]
	}
	return name
}

// ToolVerb is the status title while a tool runs.
func ToolVerb(name string) string {
	switch name {
	case "Edit", "Write", "NotebookEdit", "MultiEdit":
		return "Editing"
	case "Read", "Glob", "Grep":
		return "Reading"
	case "Bash", "PowerShell":
		return "Running"
	case "WebFetch", "WebSearch":
		return "Fetching"
	case "Agent", "Task":
		return "Delegating"
	}
	return "Using " + ToolName(name)
}

// Summarize is one short line describing a tool call.
func Summarize(name string, in map[string]any) string {
	var s string
	switch name {
	case "Bash", "PowerShell":
		s = firstLine(str(in, "command"))
		if s == "" {
			s = str(in, "description")
		}
	case "Edit", "Write", "Read", "NotebookEdit", "MultiEdit":
		s = base(str(in, "file_path") + str(in, "notebook_path"))
	case "Glob", "Grep":
		s = str(in, "pattern")
	case "WebFetch", "WebSearch":
		s = urlRe.ReplaceAllString(str(in, "url")+str(in, "query"), "")
	case "Agent", "Task":
		s = str(in, "description")
	case "ExitPlanMode":
		s = "Plan ready for approval"
	default:
		for _, k := range []string{"description", "command", "file_path", "pattern", "query", "prompt", "title", "url"} {
			if s = str(in, k); s != "" {
				break
			}
		}
	}
	return clip(Redact(firstLine(s)), 80)
}

// Details is the longer permission-screen text.
func Details(name string, in map[string]any) string {
	var s string
	switch name {
	case "Bash", "PowerShell":
		s = str(in, "command")
		if d := str(in, "description"); d != "" {
			s = d + "\n" + s
		}
	case "Edit", "Write", "MultiEdit", "Read", "NotebookEdit":
		s = str(in, "file_path") + str(in, "notebook_path")
	case "ExitPlanMode":
		s = str(in, "plan")
	default:
		s = Summarize(name, in)
	}
	return clip(Redact(strings.ReplaceAll(s, "\r", "")), 400)
}

// CleanAnswer normalises text coming back from the keypad.
func CleanAnswer(s string) string {
	s = strings.Map(func(r rune) rune {
		if r == '\n' || unicode.IsPrint(r) {
			return r
		}
		return -1
	}, s)
	return clip(strings.Join(strings.Fields(s), " "), 256)
}
