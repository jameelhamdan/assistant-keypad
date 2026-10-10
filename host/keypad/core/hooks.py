"""Claude Code hook decisions. An empty dict always means "no decision":
Claude Code then does exactly what it would do without the keypad."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .. import proto
from ..config import MAX_CONTINUES
from .ctx import Ctx
from .dialogs import Dialog, DialogError, no_keypad
from .sessions import ASKING, CONTINUING, ENDED, IDLE, LOG_RESULT, STOPPED, WORKING, short
from .text import (
    always_label,
    clip,
    details,
    first_non_empty,
    for_session,
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


@dataclass
class Question:
    """One question to answer on the keypad."""

    text: str
    header: str = ""
    options: list[str] = field(default_factory=list)
    multi: bool = False
    descriptions: list[str] = field(default_factory=list)  # one per option ("" = none), as Claude Code shows under each


@dataclass
class Answer:
    """The result of a Question; err set means "ask on the PC"."""

    values: list[str] = field(default_factory=list)
    yes: bool = False
    err: Exception | None = None


OTHER = "Other… (type on the PC)"
DIFF_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")  # their dialog body is a diff (colored on the keypad)


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


class Hooks:
    """Hook handling for the agent. Hooks only decide: permissions, questions and
    stop. What the keypad shows of the session comes from its transcript
    (core/transcript.py)."""

    def __init__(self, agent: Agent):
        self.a = agent

    def hook(self, ctx: Ctx, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._hook(ctx, event, payload or {})
        except Exception:  # never let a bug turn into a decision
            self.a.log.exception("hook handler failed (%s)", event)
            return {}

    def _hook(self, ctx: Ctx, event: str, p: dict[str, Any]) -> dict:
        out: dict[str, Any] = {}
        sid = clip(s(p, "session_id"), 64) or "unknown"
        cwd = s(p, "cwd")
        tool = s(p, "tool_name")
        inp = p.get("tool_input") if isinstance(p.get("tool_input"), dict) else {}

        if event == "SessionStart":
            self.a.sessions.start(sid, cwd)
        else:
            self.a.sessions.set_cwd(sid, cwd)
        self.a.sessions.set_transcript(sid, s(p, "transcript_path"))
        if "permission_mode" in p:
            self.a.sessions.set_mode(sid, s(p, "permission_mode"))

        if event == "SessionStart":
            self.a.sync(sid)  # whatever the transcript holds now is history; what follows is live
        elif event == "SessionEnd":
            self.a.sessions.touch(sid, ENDED, "Session ended", clip(s(p, "reason"), 160), cwd)
            self.a.forget(sid)
        elif event == "UserPromptSubmit":
            self.a.sessions.reset_continues(sid)
            self.a.sessions.touch(sid, WORKING, "Working", clip(redact(s(p, "prompt")), 160), cwd)
            return out
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

    def usable(self) -> bool:
        """Whether the keypad should be asked at all."""
        return not self.a.paused() and bool(self.a.targets())

    # ---- permission ----

    def permission(self, ctx: Ctx, sid: str, cwd: str, tool: str, inp: dict,
                   agent_type: str, suggestions: list[Any]) -> dict[str, Any]:
        """Claude Code's permission dialog: 1. Yes, 2. Yes and don't ask again
        (when Claude Code suggests a rule), 3. No. Esc leaves it to the PC."""
        name, summary = tool_name(tool), summarize(tool, inp)
        self.a.sessions.touch(sid, ASKING, "Permission", f"{name}: {summary}", cwd)
        self.a.sync(sid)
        project = self.a.sessions.project(sid)
        if not self.usable():
            return {}
        ctx = ctx.with_timeout(self.a.config().behavior.timeout)
        title = permission_title(tool)
        if agent_type:
            title = f"[{clip(agent_type, 12)}] {title}"
        session_only = self.a.config().behavior.always_for_session
        acts, items = ["allow"], ["Yes"]
        if suggestions:
            acts.append("always")
            items.append(always_label(suggestions, session_only))
        acts.append("deny")
        items.append("No")
        err: Exception | None = None
        decision = ""
        try:
            idx = self.a.dialogs.run(ctx, project, "permission", lambda d: self.a.picked(d.show(ctx, {
                "tpl": "select", "tone": "warn", "title": title, "project": project, "body": details(tool, inp),
                "diff": tool in DIFF_TOOLS, "q": permission_question(tool, inp), "items": items})), sid=sid)
            decision = acts[idx] if idx is not None and 0 <= idx < len(acts) else ""
        except DialogError as e:
            err = e
        if decision in ("allow", "always"):
            self.a.log.info("permission allowed on keypad tool=%s summary=%s always=%s", name, summary, decision == "always")
            self.a.sessions.touch(sid, WORKING, "Allowed", f"{name}: {summary}")
            allow: dict[str, Any] = {"behavior": "allow"}
            if decision == "always":
                allow["updatedPermissions"] = for_session(suggestions) if session_only else suggestions
            return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": allow}}
        if decision == "deny":
            self.a.log.info("permission denied on keypad tool=%s summary=%s", name, summary)
            self.a.sessions.touch(sid, WORKING, "Denied", f"{name}: {summary}")
            self.a.sessions.add_log(sid, {"k": LOG_RESULT, "t": "Denied on the keypad"})
            return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {
                "behavior": "deny", "message": "The user denied this from the hardware keypad. Ask before retrying it."}}}
        self.a.log.info("permission left to the PC tool=%s reason=%s", name, err or "no decision")
        self.a.sessions.touch(sid, ASKING, "Permission", "Answer on the PC")
        return {}

    # ---- stop ----

    def stop(self, ctx: Ctx, sid: str, cwd: str, last: str) -> dict[str, Any]:
        """Claude finished: continue; Esc leaves it to the PC."""
        cfg = self.a.config()
        self.a.sync(sid)  # Claude's last message is in the feed before the keypad asks
        self.a.sessions.touch(sid, STOPPED, "Finished", clip(last, 160), cwd)
        project = self.a.sessions.project(sid)
        # (After MAX_CONTINUES in a row Claude Code's own cap, which the installer sets to the same number,
        # ends the turn: nothing to ask here.)
        if cfg.behavior.ask_when_finished < 0:
            self.a.sessions.touch(sid, IDLE, "Done", last)
            self.a.notify(f"Claude finished: {project}")
            return {}
        if not self.usable():
            return {}
        # At the PC you carry on there: asking would only hold up your next prompt.
        if not self.a.away(cfg.behavior.ask_when_finished):
            self.a.sessions.touch(sid, IDLE, "Idle", "Waiting for your next prompt")
            self.a.notify(f"Claude finished: {project}")
            return {}
        ctx = ctx.with_timeout(cfg.behavior.timeout)

        try:
            result = self.a.dialogs.run(ctx, project, "stop", lambda d: self.stop_screen(d, ctx, sid, project), sid=sid)
        except DialogError as e:
            self.a.log.info("stop left to the PC reason=%s", e)
            self.a.sessions.touch(sid, IDLE, "Idle", "Waiting for input on the PC")
            result = None
        return result or {}

    def stop_screen(self, d: Dialog, ctx: Ctx, sid: str, project: str) -> dict[str, Any] | None:
        """What the keypad shows when Claude finished: continue."""
        screen = {"tpl": "prompt", "title": "Claude finished", "items": ["continue"], "esc": "done", "project": project}
        if self.a.picked(d.show(ctx, screen)) == 0:
            return self.continue_with(sid, CONTINUE_REASON, "Continue")
        self.a.sessions.touch(sid, IDLE, "Done", "Waiting for your next prompt")
        return None

    def continue_with(self, sid: str, reason: str, label: str) -> dict[str, Any]:
        n = self.a.sessions.add_continue(sid)
        self.a.sessions.touch(sid, CONTINUING, "Continuing", f"{label} ({n}/{MAX_CONTINUES})")
        self.a.sessions.add_log(sid, {"k": LOG_RESULT, "t": label + " from the keypad"})
        self.a.log.info("continue from keypad session=%s what=%s count=%d", short(sid), label, n)
        return {"decision": "block", "reason": reason, "systemMessage": f"{label} from the keypad ({n}/{MAX_CONTINUES})."}

    # ---- questions ----

    def ask_one(self, ctx: Ctx, d: Dialog, project: str, q: Question) -> Answer:
        title = first_non_empty(q.header, "Question")
        try:
            if not q.options:
                screen = {**self.a.yes_no(project, title, ""), **question_text(q.text, [], [])}
                yes = self.a.picked(d.show(ctx, screen)) == 0
                return Answer(["Yes" if yes else "No"], yes=yes)
            if len(q.options) >= proto.MAX_ITEMS:  # room for Other
                return Answer(err=ValueError("too many options"))
            # Claude Code's own list ends in "Other" (free text): the keypad cannot type, so it hands that to the PC
            items = clip_all(q.options) + ([] if q.multi else [OTHER])
            screen = {"tpl": "multi" if q.multi else "select", "title": title, "project": project,
                      **question_text(q.text, q.options, q.descriptions), "items": items}
            p = d.show(ctx, screen)
            if q.multi:
                vals = [q.options[i] for i in p.get("sel") or [] if 0 <= i < len(q.options)]
                return Answer(vals) if vals else Answer(err=ValueError("nothing selected"))
            idx = self.a.picked(p)
            if idx == len(q.options):
                return Answer(err=DialogError("chose Other: answer on the PC"))
            if idx is None or not 0 <= idx < len(q.options):
                return Answer(err=ValueError("invalid option"))
            return Answer([q.options[idx]])
        except DialogError as e:
            return Answer(err=e)

    def ask(self, ctx: Ctx, cwd: str, qs: list[Question], sid: str = "") -> list[Answer]:
        """Runs a set of questions as one dialog (AskUserQuestion)."""
        project = project_of(cwd)
        answers = [Answer(err=no_keypad()) for _ in qs]
        if not self.usable():
            return answers
        ctx = ctx.with_timeout(self.a.config().behavior.timeout)

        def fn(d: Dialog) -> None:
            for i, q in enumerate(qs):
                answers[i] = self.ask_one(ctx, d, project, q)
                if answers[i].err:
                    return

        try:
            self.a.dialogs.run(ctx, project, "question", fn, sid=sid)
        except DialogError as e:
            for a in answers:
                if a.err is not None:
                    a.err = e
        return answers

    def ask_user_question(self, ctx: Ctx, sid: str, cwd: str, inp: dict) -> dict:
        """Answers Claude Code's native AskUserQuestion on the keypad by
        allowing the tool call with the answers filled in."""
        self.a.sessions.touch(sid, ASKING, "Question", "", cwd)
        self.a.sync(sid)
        raw = inp.get("questions") if isinstance(inp.get("questions"), list) else []
        if not raw or len(raw) > 4 or not self.usable():
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
                self.a.log.info("question left to the PC reason=%s", a.err)
                self.a.sessions.touch(sid, ASKING, "Question", "Answer on the PC")
                return {}
            out[q.text] = ", ".join(a.values)
        self.a.sessions.touch(sid, WORKING, "Working", "Answer sent")
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "allow",
            "permissionDecisionReason": "Answered on the hardware keypad.",
            "updatedInput": {**inp, "answers": out},
        }}
