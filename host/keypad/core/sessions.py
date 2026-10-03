"""The registry of Claude Code sessions, keyed by session_id."""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from typing import Any

from .. import proto
from .text import clip, markdown, project_of

IDLE, THINKING, WORKING, TOOL = "idle", "thinking", "working", "tool"
PERMISSION, QUESTION, INPUT = "permission", "question", "input"
DONE, STOPPED, CONTINUING, FAILED, ENDED = "done", "stopped", "continuing", "failed", "ended"

# This state taxonomy is duplicated by hand in two other places that must
# stay in sync: host/keypad/tray.py (state_word/_color_for) and
# firmware/src/ui.cpp (busy()/waiting()/stateColor()/stateWord()). A new
# state added here needs updating in both.
BUSY_STATES = {THINKING, WORKING, TOOL, PERMISSION, QUESTION, INPUT, CONTINUING}
WAITING_STATES = {PERMISSION, QUESTION, STOPPED, INPUT}
WORKING_STATES = {THINKING, WORKING, TOOL, CONTINUING}
STALE_AFTER = 6 * 3600

# transcript line kinds
LOG_USER, LOG_CLAUDE, LOG_TOOL, LOG_RESULT = "u", "c", "t", "r"
LOG_MAX = 32  # the keypad scrolls back through these (firmware MAX_LOG)
MAX_SESSIONS = 32


@dataclass
class Session:
    id: str
    project: str = ""
    cwd: str = ""
    pids: list[int] = field(default_factory=list)  # claude process ancestry reported by the hook shim
    state: str = IDLE
    title: str = ""
    detail: str = ""
    continues: int = 0
    started: float = field(default_factory=time.time)
    turn: float = 0.0  # when the current stretch of work began (the keypad's elapsed timer)
    last: float = field(default_factory=time.time)
    name: str = ""  # the session's title, as on its terminal tab
    mode: str = ""  # Claude Code's permission mode (acceptEdits, plan, bypassPermissions; "" = default)
    log: list[dict[str, str]] = field(default_factory=list)  # latest transcript lines, oldest first
    seen: set[str] = field(default_factory=set, repr=False)  # transcript message ids already in log
    inflight: int = field(default=0, repr=False)  # tool calls (main + subagents) currently running

    def view(self) -> dict[str, Any]:
        return {k: copy.copy(v) for k, v in self.__dict__.items() if k != "seen"}


def fit_log(kind: str, text: str) -> str:
    """A transcript entry as the keypad stores it: Claude's text whole (up to
    8 KB) with its Markdown styled, your prompt up to 2 KB, tool lines short."""
    if kind == LOG_CLAUDE:
        return proto.fit(markdown(text), proto.LOG_CLAUDE_TEXT)
    return proto.fit(text, proto.LOG_USER_TEXT if kind == LOG_USER else proto.LOG_TEXT)


def short(sid: str) -> str:
    return sid[:8]


class Sessions:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._m: dict[str, Session] = {}
        self._current = ""
        self._pinned = ""

    def _get(self, sid: str) -> Session:
        x = self._m.get(sid)
        if x is None:
            x = self._m[sid] = Session(id=sid)
            self._prune(keep=sid)
        return x

    def _prune(self, keep: str) -> None:
        """Drops the least recently active session once there are too many,
        never the one just added, the current or the pinned one."""
        if len(self._m) <= MAX_SESSIONS:
            return
        cands = [x for x in self._m.values() if x.id not in (keep, self._current, self._pinned)]
        if cands:
            del self._m[min(cands, key=lambda x: x.last).id]

    def touch(self, sid: str, state: str, title: str, detail: str, cwd: str = "", pids: list[int] | None = None) -> None:
        """Records an event. It becomes the current session unless another is pinned."""
        with self._lock:
            x = self._get(sid)
            if cwd:
                x.cwd, x.project = cwd, project_of(cwd)
            if pids:
                x.pids = list(pids)
            if x.state not in BUSY_STATES and state in BUSY_STATES:
                x.turn = time.time()  # a new turn: restart the "Brewing… (12s)" timer
            x.state, x.title, x.detail, x.last = state, title, detail, time.time()
            if state == ENDED:
                if self._pinned == sid:
                    self._pinned = ""
                if self._current == sid:
                    self._current = self._latest(exclude=sid)
                return
            if not self._pinned or self._pinned == sid:
                self._current = sid

    def enter_tool(self, sid: str) -> None:
        """A tool call (main session or a subagent) started running."""
        with self._lock:
            self._get(sid).inflight += 1

    def exit_tool(self, sid: str) -> int:
        """A tool call finished; returns how many are still running for sid.
        Concurrent subagents share one session's displayed state, so a quick
        call finishing must not blank out the state of another still running
        -- the caller checks this before overwriting it with a generic one."""
        with self._lock:
            x = self._get(sid)
            x.inflight = max(0, x.inflight - 1)
            return x.inflight

    def start(self, sid: str, cwd: str, pids: list[int] | None) -> None:
        """Registers a (re)started session and resets its counters."""
        with self._lock:
            x = self._get(sid)
            x.continues, x.started = 0, time.time()
        self.touch(sid, IDLE, "Ready", "", cwd, pids)

    def get(self, sid: str) -> Session | None:
        with self._lock:
            x = self._m.get(sid)
            return copy.deepcopy(x) if x else None

    def by_pid(self, pid: int) -> Session | None:
        """The live session whose claude process is pid (the MCP server's parent)."""
        with self._lock:
            c = [x for x in self._m.values() if pid > 0 and pid in x.pids and x.state != ENDED]
            return copy.deepcopy(max(c, key=lambda x: x.last)) if c else None

    def project(self, sid: str) -> str:
        x = self.get(sid)
        return x.project if x else ""

    def is_busy(self, sid: str) -> bool:
        x = self.get(sid)
        return bool(x and x.state in BUSY_STATES)

    def continues(self, sid: str) -> int:
        x = self.get(sid)
        return x.continues if x else 0

    def add_continue(self, sid: str) -> int:
        with self._lock:
            x = self._get(sid)
            x.continues += 1
            return x.continues

    def reset_continues(self, sid: str) -> None:
        with self._lock:
            if x := self._m.get(sid):
                x.continues = 0

    def select(self, prefix: str) -> bool:
        """Pins the display to the session whose id starts with prefix."""
        with self._lock:
            for sid, x in self._m.items():
                if prefix and sid.startswith(prefix) and x.state != ENDED:
                    self._pinned = self._current = sid
                    return True
        return False

    def follow(self) -> None:
        """Unpins: the display follows the latest activity again."""
        with self._lock:
            self._pinned = ""
            if latest := self._latest():
                self._current = latest

    def current(self) -> Session | None:
        with self._lock:
            sid = self._current
        return self.get(sid)

    def pinned(self) -> bool:
        with self._lock:
            return bool(self._pinned)

    def live(self) -> list[Session]:
        """Sessions with recent activity, newest first."""
        now = time.time()
        with self._lock:
            out = [copy.deepcopy(x) for x in self._m.values() if x.state != ENDED and now - x.last < STALE_AFTER]
        return sorted(out, key=lambda x: x.last, reverse=True)

    def _latest(self, exclude: str = "") -> str:
        c = [x for x in self._m.values() if x.id != exclude and x.state != ENDED]
        return max(c, key=lambda x: x.last).id if c else ""

    # ---- the mirrored transcript ----

    def add_log(self, sid: str, *lines: dict[str, str]) -> None:
        """Appends transcript lines, skipping empty ones and immediate repeats."""
        with self._lock:
            x = self._get(sid)
            for line in lines:
                line = {"k": line["k"], "t": fit_log(line["k"], line["t"])}
                if line["t"] and not (x.log and x.log[-1] == line):
                    x.log.append(line)
            del x.log[:-LOG_MAX]

    def add_claude_texts(self, sid: str, texts: list[dict[str, str]], before_tool: bool) -> None:
        """Logs Claude's transcript messages not logged before. before_tool
        places them ahead of the latest tool line: Claude Code writes a
        message to the transcript only after the PreToolUse hook of the tool
        call it introduces, so it reaches us with that tool's PostToolUse."""
        with self._lock:
            x = self._get(sid)
            lines = []
            for t in texts:
                key = t["id"] + "\0" + t["text"]
                if key not in x.seen:
                    x.seen.add(key)
                    lines.append({"k": LOG_CLAUDE, "t": fit_log(LOG_CLAUDE, t["text"])})
            if len(x.seen) > 64:  # only recent ids matter
                x.seen = {t["id"] + "\0" + t["text"] for t in texts}
            at = -1
            if before_tool and lines:
                for i in range(len(x.log) - 1, -1, -1):
                    if x.log[i]["k"] == LOG_TOOL:
                        at = i
                        break
                    if x.log[i]["k"] != LOG_RESULT:
                        break  # the latest tool already has text after it: append
            if at >= 0:
                x.log[at:at] = lines
                del x.log[:-LOG_MAX]
                return
        self.add_log(sid, *lines)

    # ---- kept across agent restarts ----

    def dump(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{**{k: copy.copy(v) for k, v in x.__dict__.items() if k != "seen"}, "seen": sorted(x.seen)}
                    for x in self._m.values() if x.state != ENDED]

    def restore(self, saved: list[Any]) -> None:
        """Sessions from dump(), minus stale ones; the display follows the latest."""
        now = time.time()
        names = {f.name for f in fields(Session)}
        with self._lock:
            for d in saved if isinstance(saved, list) else []:
                if not isinstance(d, dict) or not isinstance(d.get("id"), str) or now - float(d.get("last", 0)) > STALE_AFTER:
                    continue
                try:
                    x = Session(**{k: v for k, v in d.items() if k in names and k != "seen"})
                except TypeError:
                    continue
                x.seen = {str(k) for k in d.get("seen") or []}
                self._m[x.id] = x
            self._current = self._latest()

    def set_name(self, sid: str, name: str) -> None:
        if name:
            with self._lock:
                self._get(sid).name = name

    def set_mode(self, sid: str, mode: str) -> None:
        with self._lock:
            self._get(sid).mode = "" if mode == "default" else mode


def wire(sessions: list[Session], allow: Callable[[str], bool] | None = None) -> list[dict[str, Any]]:
    """Live sessions for the status message, filtered by project."""
    out = []
    now = time.time()
    for x in sessions:
        if allow and not allow(x.project):
            continue
        d = {"id": short(x.id), "project": proto.fit(x.project, proto.SESSION_PROJECT), "state": x.state,
             "title": proto.fit(clip(x.title, 40), proto.SESSION_TITLE), "since": int(now - (x.turn or x.started))}
        if x.detail:
            d["detail"] = proto.fit(clip(x.detail, 120), proto.SESSION_DETAIL)
        if x.name:
            d["name"] = proto.fit(clip(x.name, 38), proto.SESSION_NAME)
        if x.mode:
            d["mode"] = proto.fit(x.mode, proto.SESSION_MODE)
        out.append(d)
        if len(out) == 8:
            break
    return out
