package core

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/jameelhamdan/assistant-keypad/host/internal/proto"
)

// HookRequest is what `keypad hook <Event>` forwards to the agent.
type HookRequest struct {
	Event   string         `json:"event"`
	PIDs    []int          `json:"pids"`   // hook shim's parent, grandparent (the claude process)
	Worker  bool           `json:"worker"` // started by the agent as a worker
	Payload map[string]any `json:"payload"`
}

const (
	continueReason = "The user asked from the hardware keypad that you continue working. Continue from the " +
		"current task and make further useful progress. If the task is genuinely complete, say so briefly and stop."
	keypadContext = "[keypad] The user's hardware keypad (small screen + 8 buttons) is connected. When you need " +
		"a decision or clarification, prefer the AskUserQuestion tool or mcp__keypad__ask_user with short, " +
		"enumerable options (yes/no, or up to 8 choices) so the user can answer from the keypad."
)

// Hook handles one Claude Code hook event and returns the hook's JSON
// output. An empty map always means "no decision": Claude Code then does
// exactly what it would do without the keypad.
func (a *Agent) Hook(ctx context.Context, r HookRequest) (out map[string]any) {
	out = map[string]any{}
	defer func() {
		if rec := recover(); rec != nil {
			a.Log.Error("hook handler panic", "event", r.Event, "panic", rec)
			out = map[string]any{}
		}
	}()
	p := r.Payload
	if p == nil {
		return out
	}
	sid := clip(str(p, "session_id"), 64)
	if sid == "" {
		sid = "unknown"
	}
	cwd := str(p, "cwd")
	tool := str(p, "tool_name")
	in, _ := p["tool_input"].(map[string]any)
	subagent := str(p, "agent_id") != ""
	touch := func(state, title, detail string) {
		a.Sessions.Touch(sid, state, title, clip(detail, 160), cwd, r.PIDs)
		a.markDirty()
	}

	switch r.Event {
	case "SessionStart":
		a.Sessions.Start(sid, cwd, r.PIDs, r.Worker)
		a.markDirty()
		return a.announce("SessionStart", out)

	case "SessionEnd":
		touch(StEnded, "Session ended", str(p, "reason"))
		a.takePending(sid)

	case "UserPromptSubmit":
		a.Sessions.ResetContinues(sid)
		touch(StThinking, "Working", Redact(str(p, "prompt")))
		out = a.announce("UserPromptSubmit", out)
		return a.deliverPending(sid, "UserPromptSubmit", out)

	case "PreToolUse":
		if tool == "AskUserQuestion" {
			return a.askUserQuestion(ctx, sid, cwd, r.PIDs, in)
		}
		prefix := ""
		if subagent {
			prefix = "[agent] "
		}
		touch(StTool, ToolVerb(tool), prefix+Summarize(tool, in))
		if !subagent {
			return a.deliverPending(sid, "PreToolUse", out)
		}

	case "PostToolUse":
		touch(StWorking, "Working", "")
		if !subagent {
			return a.deliverPending(sid, "PostToolUse", out)
		}

	case "PostToolUseFailure":
		touch(StWorking, "Working", ToolName(tool)+" failed: "+Redact(str(p, "error")))

	case "PermissionRequest":
		return a.permission(ctx, sid, cwd, r.PIDs, tool, in, str(p, "agent_type"))

	case "Stop":
		return a.stop(ctx, sid, cwd, r.PIDs, Redact(str(p, "last_assistant_message")))

	case "StopFailure":
		touch(StFailed, "Failed", Redact(str(p, "error")+" "+str(p, "error_type")))

	case "SubagentStart":
		a.Toast("Agent started: "+str(p, "agent_type"), "info", 2000)

	case "SubagentStop":
		a.Toast("Agent done: "+str(p, "agent_type"), "info", 2000)

	case "PreCompact":
		a.Toast("Compacting context…", "info", 2000)

	case "Notification":
		msg := Redact(str(p, "message"))
		switch str(p, "notification_type") {
		case "permission_prompt":
			if !a.Dialogs.Busy() {
				touch(StPermission, "Permission", "Waiting on the PC")
			}
		case "idle_prompt":
			touch(StIdle, "Idle", "Waiting for your input")
		case "elicitation_dialog", "agent_needs_input":
			touch(StInput, "Input needed", msg)
		default:
			if msg != "" {
				a.Toast(msg, "info", 3000)
			}
		}
	}
	return out
}

func (a *Agent) announce(event string, out map[string]any) map[string]any {
	if a.Config().Behavior.AnnounceInContext && len(a.Targets("")) > 0 && !a.Paused() {
		return withContext(out, event, keypadContext)
	}
	return out
}

func withContext(out map[string]any, event, text string) map[string]any {
	hso, _ := out["hookSpecificOutput"].(map[string]any)
	if hso == nil {
		hso = map[string]any{"hookEventName": event}
		out["hookSpecificOutput"] = hso
	}
	if prev, _ := hso["additionalContext"].(string); prev != "" {
		text = prev + "\n\n" + text
	}
	hso["additionalContext"] = text
	return out
}

func remoteInstruction(label, prompt string) string {
	return fmt.Sprintf("[keypad] The user sent this instruction from the hardware keypad (shortcut %q): %s", label, prompt)
}

func (a *Agent) deliverPending(sid, event string, out map[string]any) map[string]any {
	sc, ok := a.takePending(sid)
	if !ok {
		return out
	}
	a.Log.Info("shortcut delivered", "via", event, "label", sc.Label)
	return withContext(out, event, remoteInstruction(sc.Label, sc.Prompt))
}

// usable reports whether the keypad should be asked at all.
func (a *Agent) usable(project string) bool {
	return !a.Paused() && len(a.Targets(project)) > 0
}

func timeout(ctx context.Context, secs int) (context.Context, context.CancelFunc) {
	return context.WithTimeout(ctx, time.Duration(secs)*time.Second)
}

// ---- permission -------------------------------------------------------------------

func (a *Agent) permission(ctx context.Context, sid, cwd string, pids []int, tool string, in map[string]any, agentType string) map[string]any {
	name := ToolName(tool)
	summary := Summarize(tool, in)
	a.Sessions.Touch(sid, StPermission, "Permission", name+": "+summary, cwd, pids)
	a.markDirty()
	project := a.Sessions.Project(sid)
	if !a.usable(project) {
		return map[string]any{}
	}
	cfg := a.Config()
	ctx, cancel := timeout(ctx, cfg.Behavior.Timeouts.Permission)
	defer cancel()

	title := "Allow " + name + "?"
	if agentType != "" {
		title = "[" + clip(agentType, 12) + "] " + title
	}
	k := cfg.Keys
	keys := withPC(proto.Keys{}.Set(k.Allow, "Allow", "allow", "ok").Set(k.Deny, "Deny", "deny", "danger"), k.PC)
	var decision string
	err := a.Dialogs.Run(ctx, project, "permission", true, func(d *Dialog) error {
		p, err := d.Show(ctx, proto.Screen{Tpl: "prompt", Tone: "warn", Title: title, Project: project,
			Body: Details(tool, in), Keys: keys, Click: "pc"})
		decision = p.Act
		return err
	})
	switch {
	case err == nil && decision == "allow":
		a.Log.Info("permission allowed on keypad", "tool", name, "summary", summary)
		a.Sessions.Touch(sid, StWorking, "Allowed", name+": "+summary, "", nil)
		a.markDirty()
		return map[string]any{"hookSpecificOutput": map[string]any{
			"hookEventName": "PermissionRequest", "decision": map[string]any{"behavior": "allow"}}}
	case err == nil && decision == "deny":
		a.Log.Info("permission denied on keypad", "tool", name, "summary", summary)
		a.Sessions.Touch(sid, StWorking, "Denied", name+": "+summary, "", nil)
		a.markDirty()
		return map[string]any{"hookSpecificOutput": map[string]any{
			"hookEventName": "PermissionRequest", "decision": map[string]any{"behavior": "deny",
				"message": "The user denied this from the hardware keypad. Ask before retrying it."}}}
	}
	a.Log.Info("permission left to the PC", "tool", name, "reason", errText(err))
	a.Sessions.Touch(sid, StPermission, "Permission", "Answer on the PC", "", nil)
	a.markDirty()
	return map[string]any{}
}

func errText(err error) string {
	if err == nil {
		return "no decision"
	}
	return err.Error()
}

// ---- stop ----------------------------------------------------------------------------

func (a *Agent) stop(ctx context.Context, sid, cwd string, pids []int, last string) map[string]any {
	cfg := a.Config()
	a.Sessions.Touch(sid, StStopped, "Finished", last, cwd, pids)
	a.markDirty()
	project := a.Sessions.Project(sid)
	if !cfg.Behavior.AskOnStop {
		a.Sessions.Touch(sid, StDone, "Done", last, "", nil)
		return map[string]any{}
	}
	if sc, ok := a.takePending(sid); ok && a.Sessions.Continues(sid) < cfg.Behavior.MaxContinues {
		return a.continueWith(sid, remoteInstruction(sc.Label, sc.Prompt), "Shortcut: "+sc.Label)
	}
	if !a.usable(project) {
		return map[string]any{}
	}
	ctx, cancel := timeout(ctx, cfg.Behavior.Timeouts.Stop)
	defer cancel()

	var result map[string]any
	err := a.Dialogs.Run(ctx, project, "stop", true, func(d *Dialog) error {
		// A shortcut may have been queued while this request waited its turn.
		if sc, ok := a.takePending(sid); ok {
			result = a.continueWith(sid, remoteInstruction(sc.Label, sc.Prompt), "Shortcut: "+sc.Label)
			return nil
		}
		count, maxC := a.Sessions.Continues(sid), cfg.Behavior.MaxContinues
		if count >= maxC {
			p, err := d.Show(ctx, a.yesNo(project, "Continue limit reached",
				fmt.Sprintf("%d keypad continues in a row. Continue anyway? Yes resets the counter.", count), "pc"))
			if err != nil {
				return err
			}
			if p.Act == "yes" {
				a.Sessions.ResetContinues(sid)
				result = a.continueWith(sid, continueReason, "Continue")
			}
			return nil
		}
		k := cfg.Keys
		keys := proto.Keys{}.Set(k.Continue, "Continue", "continue", "ok").Set(k.Done, "Done", "done", "dim")
		if len(cfg.Shortcuts) > 0 {
			keys.Set(k.Shortcuts, "Shortcut...", "shortcuts", "accent")
		}
		withPC(keys, k.PC)
		stopScreen := proto.Screen{Tpl: "prompt", Tone: "accent", Title: "Claude finished", Project: project,
			Body: firstNonEmpty(last, "Claude finished its response."), Keys: keys, Click: "pc"}
		for {
			p, err := d.Show(ctx, stopScreen)
			if err != nil {
				return err
			}
			switch p.Act {
			case "continue":
				result = a.continueWith(sid, continueReason, "Continue")
				return nil
			case "done":
				a.Sessions.Touch(sid, StDone, "Done", "Waiting for your next prompt", "", nil)
				return nil
			case "shortcuts":
				p, err := d.Show(ctx, proto.Screen{Tpl: "list", Title: "Continue with…", Project: project,
					Items: a.shortcutLabels(), Click: "back"})
				if err != nil && !errors.Is(err, ErrToPC) {
					return err
				}
				if err == nil && p.Act == "item" && p.Idx != nil && *p.Idx < len(cfg.Shortcuts) {
					sc := cfg.Shortcuts[*p.Idx]
					result = a.continueWith(sid, remoteInstruction(sc.Label, sc.Prompt), "Shortcut: "+sc.Label)
					return nil
				}
				// back: show the stop screen again
			default:
				return nil
			}
		}
	})
	if result != nil {
		return result
	}
	if err != nil {
		a.Log.Info("stop left to the PC", "reason", err.Error())
		a.Sessions.Touch(sid, StIdle, "Idle", "Waiting for input on the PC", "", nil)
	}
	a.markDirty()
	return map[string]any{}
}

func (a *Agent) continueWith(sid, reason, label string) map[string]any {
	n := a.Sessions.AddContinue(sid)
	maxC := a.Config().Behavior.MaxContinues
	a.Sessions.Touch(sid, StContinuing, "Continuing", fmt.Sprintf("%s (%d/%d)", label, n, maxC), "", nil)
	a.markDirty()
	a.Log.Info("continue from keypad", "session", short(sid), "what", label, "count", n)
	return map[string]any{"decision": "block", "reason": reason,
		"systemMessage": fmt.Sprintf("%s from the keypad (%d/%d).", label, n, maxC)}
}

func firstNonEmpty(s ...string) string {
	for _, x := range s {
		if strings.TrimSpace(x) != "" {
			return x
		}
	}
	return ""
}

// ---- questions --------------------------------------------------------------------

// Question is one question to answer on the keypad.
type Question struct {
	Text    string   `json:"text"`
	Header  string   `json:"header,omitempty"`
	Options []string `json:"options,omitempty"`
	Multi   bool     `json:"multi,omitempty"`
	YesNo   bool     `json:"yes_no,omitempty"`
}

// Answer is the result of a Question; Err != nil means "ask on the PC".
type Answer struct {
	Values []string
	Yes    bool
	Err    error
}

func (a *Agent) ask(ctx context.Context, d *Dialog, project string, q Question) Answer {
	title := firstNonEmpty(q.Header, "Question")
	switch {
	case q.YesNo || len(q.Options) == 0:
		p, err := d.Show(ctx, a.yesNo(project, title, q.Text, "pc"))
		if err != nil {
			return Answer{Err: err}
		}
		return Answer{Yes: p.Act == "yes", Values: []string{map[bool]string{true: "Yes", false: "No"}[p.Act == "yes"]}}
	case q.Multi:
		if len(q.Options) > 7 {
			return Answer{Err: errors.New("too many options for multi-select")}
		}
		p, err := d.Show(ctx, proto.Screen{Tpl: "multi", Title: title, Project: project, Body: q.Text,
			Items: clipAll(q.Options), Click: "pc"})
		if err != nil {
			return Answer{Err: err}
		}
		var vals []string
		for _, i := range p.Sel {
			if i >= 0 && i < len(q.Options) {
				vals = append(vals, q.Options[i])
			}
		}
		if len(vals) == 0 {
			return Answer{Err: errors.New("nothing selected")}
		}
		return Answer{Values: vals}
	default:
		if len(q.Options) > proto.MaxItems {
			return Answer{Err: errors.New("too many options")}
		}
		p, err := d.Show(ctx, proto.Screen{Tpl: "list", Title: title, Project: project, Body: q.Text,
			Items: clipAll(q.Options), Click: "pc"})
		if err != nil {
			return Answer{Err: err}
		}
		if p.Idx == nil || *p.Idx < 0 || *p.Idx >= len(q.Options) {
			return Answer{Err: errors.New("invalid option")}
		}
		return Answer{Values: []string{q.Options[*p.Idx]}}
	}
}

func clipAll(in []string) []string {
	out := make([]string, len(in))
	for i, s := range in {
		out[i] = clip(s, 40)
	}
	return out
}

// Ask runs a set of questions as one dialog (used by MCP ask_user).
func (a *Agent) Ask(ctx context.Context, pid int, cwd string, qs []Question) []Answer {
	project := projectOf(cwd)
	if s, ok := a.Sessions.ByPID(pid); ok {
		project = s.Project
	}
	answers := make([]Answer, len(qs))
	for i := range answers {
		answers[i].Err = ErrNoKeypad
	}
	if !a.usable(project) {
		return answers
	}
	ctx, cancel := timeout(ctx, a.Config().Behavior.Timeouts.Question)
	defer cancel()
	_ = a.Dialogs.Run(ctx, project, "question", true, func(d *Dialog) error {
		for i, q := range qs {
			answers[i] = a.ask(ctx, d, project, q)
			if answers[i].Err != nil {
				return answers[i].Err
			}
		}
		return nil
	})
	return answers
}

// askUserQuestion answers Claude Code's native AskUserQuestion tool on the
// keypad by allowing the tool call with the answers filled in.
func (a *Agent) askUserQuestion(ctx context.Context, sid, cwd string, pids []int, in map[string]any) map[string]any {
	a.Sessions.Touch(sid, StQuestion, "Question", "", cwd, pids)
	a.markDirty()
	raw, _ := in["questions"].([]any)
	project := a.Sessions.Project(sid)
	if !a.Config().Behavior.InterceptAskUser || len(raw) == 0 || len(raw) > 4 || !a.usable(project) {
		return map[string]any{}
	}
	var qs []Question
	for i, r := range raw {
		q, _ := r.(map[string]any)
		text := strings.TrimSpace(str(q, "question"))
		if text == "" {
			return map[string]any{}
		}
		var opts []string
		if list, ok := q["options"].([]any); ok {
			for _, o := range list {
				if om, ok := o.(map[string]any); ok && str(om, "label") != "" {
					opts = append(opts, str(om, "label"))
				} else if s, ok := o.(string); ok && s != "" {
					opts = append(opts, s)
				}
			}
		}
		if len(opts) == 0 {
			return map[string]any{} // free-text questions are answered on the PC
		}
		header := str(q, "header")
		if len(raw) > 1 {
			header = fmt.Sprintf("%d/%d %s", i+1, len(raw), header)
		}
		multi, _ := q["multiSelect"].(bool)
		qs = append(qs, Question{Text: text, Header: header, Options: opts, Multi: multi})
	}
	answers := a.Ask(ctx, 0, cwd, qs)
	out := map[string]any{}
	for i, ans := range answers {
		if ans.Err != nil {
			a.Log.Info("question left to the PC", "reason", ans.Err.Error())
			a.Sessions.Touch(sid, StQuestion, "Question", "Answer on the PC", "", nil)
			a.markDirty()
			return map[string]any{}
		}
		out[qs[i].Text] = strings.Join(ans.Values, ", ")
	}
	a.Sessions.Touch(sid, StWorking, "Working", "Answer sent", "", nil)
	a.markDirty()
	updated := map[string]any{}
	for k, v := range in {
		updated[k] = v
	}
	updated["answers"] = out
	return map[string]any{"hookSpecificOutput": map[string]any{
		"hookEventName":            "PreToolUse",
		"permissionDecision":       "allow",
		"permissionDecisionReason": "Answered on the hardware keypad.",
		"updatedInput":             updated,
	}}
}
