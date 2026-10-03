"""Claude Code hook decisions. An empty dict always means "no decision":
Claude Code then does exactly what it would do without the keypad."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .. import proto
from .ctx import Ctx
from .dialogs import Dialog, DialogError, NoKeypad
from .sessions import (
    CONTINUING,
    DONE,
    ENDED,
    FAILED,
    IDLE,
    INPUT,
    LOG_CLAUDE,
    LOG_RESULT,
    LOG_TOOL,
    LOG_USER,
    PERMISSION,
    QUESTION,
    STOPPED,
    THINKING,
    TOOL,
    WORKING,
    short,
)
from .text import (
    always_label,
    clip,
    details,
    first_line,
    first_non_empty,
    permission_question,
    permission_title,
    project_of,
    redact,
    s,
    summarize,
    tool_name,
    tool_verb,
)

if TYPE_CHECKING:
    from .agent import Agent

CONTINUE_REASON = ("The user asked from the hardware keypad that you continue working. Continue from the "
                   "current task and make further useful progress. If the task is genuinely complete, say so "
                   "briefly and stop.")
KEYPAD_CONTEXT = ("[keypad] The user's hardware keypad (small screen + 8 buttons) is connected. When you need "
                  "a decision or clarification, prefer the AskUserQuestion tool or mcp__keypad__ask_user with "
                  "short, enumerable options (yes/no, or a few short choices) so the user can answer from the keypad.")


class Question:
    """One question to answer on the keypad."""

    def __init__(self, text: str, header: str = "", options: list[str] | None = None, multi: bool = False,
                 yes_no: bool = False, descriptions: list[str] | None = None):
        self.text, self.header, self.options, self.multi, self.yes_no = text, header, options or [], multi, yes_no
        self.descriptions = descriptions or []  # one per option ("" = none), as Claude Code shows under each

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Question:
        opts = [str(o) for o in d.get("options") or [] if str(o)]
        return cls(str(d.get("text", "")), str(d.get("header", "")), opts, bool(d.get("multi")), bool(d.get("yes_no")))


class Answer:
    """The result of a Question; err set means "ask on the PC"."""

    def __init__(self, values: list[str] | None = None, yes: bool = False, err: Exception | None = None):
        self.values, self.yes, self.err = values or [], yes, err


DIFF_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")  # their dialog body is a diff (colored on the keypad)


def with_context(out: dict[str, Any], event: str, text: str) -> dict[str, Any]:
    hso = out.setdefault("hookSpecificOutput", {"hookEventName": event})
    if prev := hso.get("additionalContext"):
        text = prev + "\n\n" + text
    hso["additionalContext"] = text
    return out


def remote_instruction(label: str, prompt: str) -> str:
    return f'[keypad] The user sent this instruction from the hardware keypad (shortcut "{label}"): {prompt}'


def claude_texts(v: Any) -> list[dict[str, str]]:
    """The hook shim's transcript_texts."""
    out = []
    for m in v if isinstance(v, list) else []:
        if t := s(m, "text").strip():
            out.append({"id": s(m, "id"), "text": redact(t)})
    return out


def clip_all(items: list[str]) -> list[str]:
    return [clip(x, 40) for x in items]


Q_ROWS, Q_ROWS_UNDER_BODY, Q_CELLS = 3, 2, 37  # what a dialog gives its question line (firmware drawDialog)


def question_text(text: str, options: list[str], descriptions: list[str]) -> dict[str, str]:
    """The q and body of a question screen. The option descriptions go in the
    scrollable body; a question too long for its lines goes there too, so it
    is never cut."""
    descs = "\n".join(f"{i + 1}. {o}: {d}" for i, (o, d) in enumerate(zip(options, descriptions, strict=False)) if d)
    rows = Q_ROWS_UNDER_BODY if descs else Q_ROWS
    if len(text) <= rows * Q_CELLS - 4:  # a little slack: lines break at word boundaries
        return {"q": text, "body": descs}
    return {"q": "", "body": text + ("\n\n" + descs if descs else "")}


class HookMixin:
    """Hook handling for Agent (kept apart for readability)."""

    def hook(self: Agent, ctx: Ctx, event: str, payload: dict[str, Any], pids: list[int] | None = None) -> dict[str, Any]:
        try:
            return self._hook(ctx, event, payload or {}, pids or [])
        except Exception:  # never let a bug turn into a decision
            self.log.exception("hook handler failed (%s)", event)
            return {}

    def _hook(self: Agent, ctx: Ctx, event: str, p: dict[str, Any], pids: list[int]) -> dict:
        out: dict[str, Any] = {}
        sid = clip(s(p, "session_id"), 64) or "unknown"
        cwd = s(p, "cwd")
        tool = s(p, "tool_name")
        inp = p.get("tool_input") if isinstance(p.get("tool_input"), dict) else {}
        subagent = bool(s(p, "agent_id"))

        def touch(state: str, title: str, detail: str) -> None:
            self.sessions.touch(sid, state, title, clip(detail, 160), cwd, pids)
            self.mark_dirty()

        # Mirror the terminal: the tab's title, the permission mode and Claude's latest messages.
        self.sessions.set_name(sid, redact(s(p, "session_title")))
        if "permission_mode" in p:
            self.sessions.set_mode(sid, s(p, "permission_mode"))
        texts = claude_texts(p.get("transcript_texts"))
        if not subagent and texts:
            self.sessions.add_claude_texts(sid, texts, before_tool=event in ("PostToolUse", "PostToolUseFailure"))
            self.mark_dirty()

        def log_line(kind: str, text: str) -> None:
            if not subagent:
                self.sessions.add_log(sid, {"k": kind, "t": text if kind in (LOG_CLAUDE, LOG_USER) else clip(text, 200)})

        if event == "SessionStart":
            self.sessions.start(sid, cwd, pids)
            self.mark_dirty()
            return self.announce("SessionStart", out)
        if event == "SessionEnd":
            touch(ENDED, "Session ended", s(p, "reason"))
            self.take_pending(sid)
        elif event == "UserPromptSubmit":
            self.sessions.reset_continues(sid)
            log_line(LOG_USER, redact(s(p, "prompt")))
            touch(THINKING, "Working", redact(s(p, "prompt")))
            return self.deliver_pending(sid, "UserPromptSubmit", self.announce("UserPromptSubmit", out))
        elif event == "PreToolUse":
            log_line(LOG_TOOL, f"{tool_name(tool)}({summarize(tool, inp)})")
            if tool == "AskUserQuestion":
                return self.ask_user_question(ctx, sid, cwd, pids, inp)
            touch(TOOL, tool_verb(tool), ("[agent] " if subagent else "") + summarize(tool, inp))
            self.sessions.enter_tool(sid)
            if not subagent:
                return self.deliver_pending(sid, "PreToolUse", out)
        elif event == "PostToolUse":
            # Subagents share the parent session's displayed state; if another
            # tool call (main or a different subagent) is still running, a
            # quick one finishing must not blank its state with plain "Working".
            if self.sessions.exit_tool(sid) == 0:
                touch(WORKING, "Working", "")
            if not subagent:
                return self.deliver_pending(sid, "PostToolUse", out)
        elif event == "PostToolUseFailure":
            self.sessions.exit_tool(sid)
            log_line(LOG_RESULT, "Error: " + first_line(redact(s(p, "error"))))
            touch(WORKING, "Working", tool_name(tool) + " failed: " + redact(s(p, "error")))
        elif event == "PermissionRequest":
            if tool == "AskUserQuestion":  # the question itself is asked on the keypad (PreToolUse); allowing the tool is no decision
                return {}
            sugg = p.get("permission_suggestions")
            return self.permission(ctx, sid, cwd, pids, tool, inp, s(p, "agent_type"), sugg if isinstance(sugg, list) else [])
        elif event == "Stop":
            if not texts:  # no transcript: the final message is all we have
                log_line(LOG_CLAUDE, redact(s(p, "last_assistant_message")))
            return self.stop(ctx, sid, cwd, pids, redact(s(p, "last_assistant_message")))
        elif event == "StopFailure":
            log_line(LOG_RESULT, "Error: " + first_line(redact(s(p, "error"))))
            touch(FAILED, "Failed", redact(s(p, "error") + " " + s(p, "error_type")))
        elif event == "Notification":
            msg = redact(s(p, "message"))
            kind = s(p, "notification_type")
            if kind == "permission_prompt":
                if not self.dialogs.busy():
                    touch(PERMISSION, "Permission", "Waiting on the PC")
            elif kind == "idle_prompt":
                touch(IDLE, "Idle", "Waiting for your input")
            elif kind in ("elicitation_dialog", "agent_needs_input"):
                touch(INPUT, "Input needed", msg)
            elif msg:
                self.toast(msg, "info", 3000)
        return out

    def announce(self: Agent, event: str, out: dict[str, Any]) -> dict[str, Any]:
        if self.config().behavior.announce_in_context and self.targets("") and not self.paused():
            return with_context(out, event, KEYPAD_CONTEXT)
        return out

    def deliver_pending(self: Agent, sid: str, event: str, out: dict[str, Any]) -> dict[str, Any]:
        sc = self.take_pending(sid)
        if not sc:
            return out
        self.log.info("shortcut delivered via=%s label=%s", event, sc.label)
        return with_context(out, event, remote_instruction(sc.label, sc.prompt))

    def usable(self: Agent, project: str) -> bool:
        """Whether the keypad should be asked at all."""
        return not self.paused() and bool(self.targets(project))

    # ---- permission ----

    def permission(self: Agent, ctx: Ctx, sid: str, cwd: str, pids: list[int], tool: str, inp: dict,
                   agent_type: str, suggestions: list[Any]) -> dict[str, Any]:
        """Claude Code's permission dialog: 1. Yes, 2. Yes and don't ask again
        (when Claude Code suggests a rule), 3. No. Esc leaves it to the PC."""
        name, summary = tool_name(tool), summarize(tool, inp)
        self.sessions.touch(sid, PERMISSION, "Permission", f"{name}: {summary}", cwd, pids)
        self.mark_dirty()
        project = self.sessions.project(sid)
        if not self.usable(project):
            return {}
        ctx = ctx.with_timeout(self.config().behavior.timeout)
        title = permission_title(tool)
        if agent_type:
            title = f"[{clip(agent_type, 12)}] {title}"
        acts, items = ["allow"], ["Yes"]
        if suggestions:
            acts.append("always")
            items.append(always_label(suggestions))
        acts.append("deny")
        items.append("No")
        err: Exception | None = None
        decision = ""
        try:
            idx = self.dialogs.run(ctx, project, "permission", True, lambda d: self.picked(d.show(ctx, {
                "tpl": "select", "tone": "warn", "title": title, "project": project, "body": details(tool, inp),
                "diff": tool in DIFF_TOOLS, "q": permission_question(tool, inp), "items": items, "esc": "pc"})), sid=sid)
            decision = acts[idx] if idx is not None and 0 <= idx < len(acts) else ""
        except DialogError as e:
            err = e
        if decision in ("allow", "always"):
            self.log.info("permission allowed on keypad tool=%s summary=%s always=%s", name, summary, decision == "always")
            self.sessions.touch(sid, WORKING, "Allowed", f"{name}: {summary}")
            self.mark_dirty()
            allow: dict[str, Any] = {"behavior": "allow"}
            if decision == "always":
                allow["updatedPermissions"] = suggestions
            return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": allow}}
        if decision == "deny":
            self.log.info("permission denied on keypad tool=%s summary=%s", name, summary)
            self.sessions.touch(sid, WORKING, "Denied", f"{name}: {summary}")
            self.sessions.add_log(sid, {"k": LOG_RESULT, "t": "Denied on the keypad"})
            self.mark_dirty()
            return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {
                "behavior": "deny", "message": "The user denied this from the hardware keypad. Ask before retrying it."}}}
        self.log.info("permission left to the PC tool=%s reason=%s", name, err or "no decision")
        self.sessions.touch(sid, PERMISSION, "Permission", "Answer on the PC")
        self.mark_dirty()
        return {}

    # ---- stop ----

    def stop(self: Agent, ctx: Ctx, sid: str, cwd: str, pids: list[int], last: str) -> dict[str, Any]:
        """Claude finished: continue or a saved prompt; Esc leaves it to the PC."""
        cfg = self.config()
        self.sessions.touch(sid, STOPPED, "Finished", last, cwd, pids)
        self.mark_dirty()
        project = self.sessions.project(sid)
        if not cfg.behavior.ask_on_stop:
            self.sessions.touch(sid, DONE, "Done", last)
            return {}
        # A queued shortcut goes straight in, unless the continue limit is
        # reached: then it stays queued and the keypad asks first (below).
        if self.sessions.continues(sid) < cfg.behavior.max_continues and (sc := self.take_pending(sid)):
            return self.continue_with(sid, remote_instruction(sc.label, sc.prompt), "Shortcut: " + sc.label)
        if not self.usable(project):
            return {}
        # At the PC you carry on there: asking would only hold up your next prompt.
        if not self.away(cfg.behavior.stop_when_away):
            self.sessions.touch(sid, IDLE, "Idle", "Waiting for your next prompt")
            self.mark_dirty()
            return {}
        ctx = ctx.with_timeout(cfg.behavior.timeout)

        def fn(d: Dialog) -> dict[str, Any] | None:
            count, max_c = self.sessions.continues(sid), cfg.behavior.max_continues
            if count >= max_c:
                yes = self.picked(d.show(ctx, self.yes_no(
                    project, "Continue limit reached",
                    f"{count} keypad continues in a row. Continue anyway? Yes resets the counter.", "pc"))) == 0
                if yes:
                    self.sessions.reset_continues(sid)
                    if sc := self.take_pending(sid):
                        return self.continue_with(sid, remote_instruction(sc.label, sc.prompt), "Shortcut: " + sc.label)
                    return self.continue_with(sid, CONTINUE_REASON, "Continue")
                self.sessions.touch(sid, DONE, "Done", "Waiting for your next prompt")
                return None
            # A shortcut may have been queued while this request waited its turn.
            if sc := self.take_pending(sid):
                return self.continue_with(sid, remote_instruction(sc.label, sc.prompt), "Shortcut: " + sc.label)
            screen = self.shortcut_screen("Claude finished", ["continue"], ["keep going"], "pc")
            idx = self.picked(d.show(ctx, {**screen, "project": project}))
            n = len(cfg.shortcuts)
            if idx == 0:
                return self.continue_with(sid, CONTINUE_REASON, "Continue")
            if idx is not None and 1 <= idx <= n:
                sc = cfg.shortcuts[idx - 1]
                return self.continue_with(sid, remote_instruction(sc.label, sc.prompt), "Shortcut: " + sc.label)
            self.sessions.touch(sid, DONE, "Done", "Waiting for your next prompt")
            return None

        try:
            result = self.dialogs.run(ctx, project, "stop", True, fn, sid=sid)
        except DialogError as e:
            self.log.info("stop left to the PC reason=%s", e)
            self.sessions.touch(sid, IDLE, "Idle", "Waiting for input on the PC")
            result = None
        self.mark_dirty()
        return result or {}

    def continue_with(self: Agent, sid: str, reason: str, label: str) -> dict[str, Any]:
        n = self.sessions.add_continue(sid)
        max_c = self.config().behavior.max_continues
        self.sessions.touch(sid, CONTINUING, "Continuing", f"{label} ({n}/{max_c})")
        self.sessions.add_log(sid, {"k": LOG_RESULT, "t": label + " from the keypad"})
        self.mark_dirty()
        self.log.info("continue from keypad session=%s what=%s count=%d", short(sid), label, n)
        return {"decision": "block", "reason": reason, "systemMessage": f"{label} from the keypad ({n}/{max_c})."}

    # ---- questions ----

    def ask_one(self: Agent, ctx: Ctx, d: Dialog, project: str, q: Question) -> Answer:
        title = first_non_empty(q.header, "Question")
        try:
            if q.yes_no or not q.options:
                screen = {**self.yes_no(project, title, "", "pc"), **question_text(q.text, [], [])}
                yes = self.picked(d.show(ctx, screen)) == 0
                return Answer(["Yes" if yes else "No"], yes=yes)
            if len(q.options) > proto.MAX_ITEMS:
                return Answer(err=ValueError("too many options"))
            screen = {"tpl": "multi" if q.multi else "select", "title": title, "project": project,
                      **question_text(q.text, q.options, q.descriptions), "items": clip_all(q.options), "esc": "pc"}
            p = d.show(ctx, screen)
            if q.multi:
                vals = [q.options[i] for i in p.get("sel") or [] if 0 <= i < len(q.options)]
                return Answer(vals) if vals else Answer(err=ValueError("nothing selected"))
            idx = self.picked(p)
            if idx is None or not 0 <= idx < len(q.options):
                return Answer(err=ValueError("invalid option"))
            return Answer([q.options[idx]])
        except DialogError as e:
            return Answer(err=e)

    def ask(self: Agent, ctx: Ctx, pid: int, cwd: str, qs: list[Question], sid: str = "") -> list[Answer]:
        """Runs a set of questions as one dialog (MCP ask_user, AskUserQuestion)."""
        project = project_of(cwd)
        if pid and (sess := self.sessions.by_pid(pid)):
            project, sid = sess.project, sess.id
        answers = [Answer(err=NoKeypad()) for _ in qs]
        if not self.usable(project):
            return answers
        ctx = ctx.with_timeout(self.config().behavior.timeout)

        def fn(d: Dialog) -> None:
            for i, q in enumerate(qs):
                answers[i] = self.ask_one(ctx, d, project, q)
                if answers[i].err:
                    return

        try:
            self.dialogs.run(ctx, project, "question", True, fn, sid=sid)
        except DialogError as e:
            for a in answers:
                if a.err is not None:
                    a.err = e
        return answers

    def ask_user_question(self: Agent, ctx: Ctx, sid: str, cwd: str, pids: list[int], inp: dict) -> dict:
        """Answers Claude Code's native AskUserQuestion on the keypad by
        allowing the tool call with the answers filled in."""
        self.sessions.touch(sid, QUESTION, "Question", "", cwd, pids)
        self.mark_dirty()
        raw = inp.get("questions") if isinstance(inp.get("questions"), list) else []
        project = self.sessions.project(sid)
        if not self.config().behavior.intercept_ask_user_question or not raw or len(raw) > 4 or not self.usable(project):
            return {}
        qs = []
        for i, q in enumerate(raw):
            text = s(q, "question").strip()
            if not text:
                return {}
            opts, descs = [], []
            for o in (q.get("options") or []) if isinstance(q, dict) else []:
                if isinstance(o, dict) and s(o, "label"):
                    opts.append(s(o, "label"))
                    descs.append(redact(s(o, "description")))
                elif isinstance(o, str) and o:
                    opts.append(o)
                    descs.append("")
            if not opts:
                return {}  # free-text questions are answered on the PC
            header = s(q, "header")
            if len(raw) > 1:
                header = f"{i + 1}/{len(raw)} {header}"
            qs.append(Question(text, header, opts, multi=bool(q.get("multiSelect")), descriptions=descs))
        answers = self.ask(ctx, 0, cwd, qs, sid)
        out = {}
        for q, a in zip(qs, answers, strict=True):
            if a.err is not None:
                self.log.info("question left to the PC reason=%s", a.err)
                self.sessions.touch(sid, QUESTION, "Question", "Answer on the PC")
                self.mark_dirty()
                return {}
            out[q.text] = ", ".join(a.values)
        self.sessions.touch(sid, WORKING, "Working", "Answer sent")
        self.mark_dirty()
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "allow",
            "permissionDecisionReason": "Answered on the hardware keypad.",
            "updatedInput": {**inp, "answers": out},
        }}
