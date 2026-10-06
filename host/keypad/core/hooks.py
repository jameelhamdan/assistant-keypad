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
    IDLE,
    LOG_RESULT,
    PERMISSION,
    QUESTION,
    STOPPED,
    THINKING,
    WORKING,
    short,
)
from .text import (
    always_label,
    clip,
    details,
    first_non_empty,
    permission_question,
    permission_title,
    project_of,
    redact,
    s,
    summarize,
    tool_name,
)

if TYPE_CHECKING:
    from .agent import Agent

CONTINUE_REASON = ("The user asked from the hardware keypad that you continue working. Continue from the "
                   "current task and make further useful progress. If the task is genuinely complete, say so "
                   "briefly and stop.")


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
    """Hook handling for Agent (kept apart for readability). Hooks only decide:
    permissions, questions and stop. What the keypad shows of the session comes
    from its transcript (core/transcript.py)."""

    def hook(self: Agent, ctx: Ctx, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._hook(ctx, event, payload or {})
        except Exception:  # never let a bug turn into a decision
            self.log.exception("hook handler failed (%s)", event)
            return {}

    def _hook(self: Agent, ctx: Ctx, event: str, p: dict[str, Any]) -> dict:
        out: dict[str, Any] = {}
        sid = clip(s(p, "session_id"), 64) or "unknown"
        cwd = s(p, "cwd")
        tool = s(p, "tool_name")
        inp = p.get("tool_input") if isinstance(p.get("tool_input"), dict) else {}

        if event == "SessionStart":
            self.sessions.start(sid, cwd)
        else:
            self.sessions.set_cwd(sid, cwd)
        self.sessions.set_transcript(sid, s(p, "transcript_path"))
        if "permission_mode" in p:
            self.sessions.set_mode(sid, s(p, "permission_mode"))

        if event == "SessionStart":
            self.sync(sid)  # whatever the transcript holds now is history; what follows is live
            self.mark_dirty()
        elif event == "SessionEnd":
            self.sessions.touch(sid, ENDED, "Session ended", clip(s(p, "reason"), 160), cwd)
            self.take_pending(sid)
            self.forget(sid)
            self.mark_dirty()
        elif event == "UserPromptSubmit":
            self.sessions.reset_continues(sid)
            self.sessions.touch(sid, THINKING, "Working", clip(redact(s(p, "prompt")), 160), cwd)
            self.mark_dirty()
            return self.deliver_pending(sid, "UserPromptSubmit", out)
        elif event == "PreToolUse":
            if tool == "AskUserQuestion":
                return self.ask_user_question(ctx, sid, cwd, inp)
        elif event == "PermissionRequest":
            if tool == "AskUserQuestion":  # the question itself is asked on the keypad (PreToolUse); allowing the tool is no decision
                return {}
            sugg = p.get("permission_suggestions")
            return self.permission(ctx, sid, cwd, tool, inp, s(p, "agent_type"), sugg if isinstance(sugg, list) else [])
        elif event == "Stop":
            return self.stop(ctx, sid, cwd, redact(s(p, "last_assistant_message")))
        return out

    def deliver_pending(self: Agent, sid: str, event: str, out: dict[str, Any]) -> dict[str, Any]:
        sc = self.take_pending(sid)
        if not sc:
            return out
        self.log.info("shortcut delivered via=%s label=%s", event, sc.label)
        return with_context(out, event, remote_instruction(sc.label, sc.prompt))

    def usable(self: Agent) -> bool:
        """Whether the keypad should be asked at all."""
        return not self.paused() and bool(self.targets())

    # ---- permission ----

    def permission(self: Agent, ctx: Ctx, sid: str, cwd: str, tool: str, inp: dict,
                   agent_type: str, suggestions: list[Any]) -> dict[str, Any]:
        """Claude Code's permission dialog: 1. Yes, 2. Yes and don't ask again
        (when Claude Code suggests a rule), 3. No. Esc leaves it to the PC."""
        name, summary = tool_name(tool), summarize(tool, inp)
        self.sessions.touch(sid, PERMISSION, "Permission", f"{name}: {summary}", cwd)
        self.sync(sid)
        self.mark_dirty()
        project = self.sessions.project(sid)
        if not self.usable():
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
            idx = self.dialogs.run(ctx, project, "permission", lambda d: self.picked(d.show(ctx, {
                "tpl": "select", "tone": "warn", "title": title, "project": project, "body": details(tool, inp),
                "diff": tool in DIFF_TOOLS, "q": permission_question(tool, inp), "items": items})), sid=sid)
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

    def stop(self: Agent, ctx: Ctx, sid: str, cwd: str, last: str) -> dict[str, Any]:
        """Claude finished: continue or a saved prompt; Esc leaves it to the PC."""
        cfg = self.config()
        self.sync(sid)  # Claude's last message is in the feed before the keypad asks
        self.sessions.touch(sid, STOPPED, "Finished", clip(last, 160), cwd)
        self.mark_dirty()
        project = self.sessions.project(sid)
        if cfg.behavior.ask_when_finished < 0:
            self.sessions.touch(sid, DONE, "Done", last)
            return {}
        # A queued shortcut goes straight in, unless the continue limit is
        # reached: then it stays queued and the keypad asks first (below).
        if self.sessions.continues(sid) < cfg.behavior.max_continues and (sc := self.take_pending(sid)):
            return self.continue_with(sid, remote_instruction(sc.label, sc.prompt), "Shortcut: " + sc.label)
        if not self.usable():
            return {}
        # At the PC you carry on there: asking would only hold up your next prompt.
        if not self.away(cfg.behavior.ask_when_finished):
            self.sessions.touch(sid, IDLE, "Idle", "Waiting for your next prompt")
            self.mark_dirty()
            return {}
        ctx = ctx.with_timeout(cfg.behavior.timeout)

        def fn(d: Dialog) -> dict[str, Any] | None:
            count, max_c = self.sessions.continues(sid), cfg.behavior.max_continues
            if count >= max_c:
                yes = self.picked(d.show(ctx, self.yes_no(
                    project, "Continue limit reached",
                    f"{count} keypad continues in a row. Continue anyway? Yes resets the counter."))) == 0
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
            screen = self.shortcut_screen("Claude finished", ["continue"], ["keep going"], "done")
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
            result = self.dialogs.run(ctx, project, "stop", fn, sid=sid)
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
                screen = {**self.yes_no(project, title, ""), **question_text(q.text, [], [])}
                yes = self.picked(d.show(ctx, screen)) == 0
                return Answer(["Yes" if yes else "No"], yes=yes)
            if len(q.options) > proto.MAX_ITEMS:
                return Answer(err=ValueError("too many options"))
            screen = {"tpl": "multi" if q.multi else "select", "title": title, "project": project,
                      **question_text(q.text, q.options, q.descriptions), "items": clip_all(q.options)}
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

    def ask(self: Agent, ctx: Ctx, cwd: str, qs: list[Question], sid: str = "") -> list[Answer]:
        """Runs a set of questions as one dialog (AskUserQuestion)."""
        project = project_of(cwd)
        answers = [Answer(err=NoKeypad()) for _ in qs]
        if not self.usable():
            return answers
        ctx = ctx.with_timeout(self.config().behavior.timeout)

        def fn(d: Dialog) -> None:
            for i, q in enumerate(qs):
                answers[i] = self.ask_one(ctx, d, project, q)
                if answers[i].err:
                    return

        try:
            self.dialogs.run(ctx, project, "question", fn, sid=sid)
        except DialogError as e:
            for a in answers:
                if a.err is not None:
                    a.err = e
        return answers

    def ask_user_question(self: Agent, ctx: Ctx, sid: str, cwd: str, inp: dict) -> dict:
        """Answers Claude Code's native AskUserQuestion on the keypad by
        allowing the tool call with the answers filled in."""
        self.sessions.touch(sid, QUESTION, "Question", "", cwd)
        self.sync(sid)
        self.mark_dirty()
        raw = inp.get("questions") if isinstance(inp.get("questions"), list) else []
        if not self.config().behavior.intercept_ask_user_question or not raw or len(raw) > 4 or not self.usable():
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
        answers = self.ask(ctx, cwd, qs, sid)
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
