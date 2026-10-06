"""The registry of Claude Code sessions, keyed by session_id."""

from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .. import proto
from .markdown import device_text, render
from .text import clip, project_of

IDLE, THINKING, WORKING, TOOL = "idle", "thinking", "working", "tool"
PERMISSION, QUESTION = "permission", "question"
DONE, STOPPED, CONTINUING, ENDED = "done", "stopped", "continuing", "ended"

BUSY_STATES = {THINKING, WORKING, TOOL, PERMISSION, QUESTION, CONTINUING}
# What the keypad and the tray are told: the one place the states are grouped.
# working / continuing: Claude is busy; asking: a request waits for you; stopped: Claude
# finished and waits for you; idle: nothing going on. (ui.cpp compares against these words.)
PHASES = {THINKING: "working", WORKING: "working", TOOL: "working", CONTINUING: "continuing",
          PERMISSION: "asking", QUESTION: "asking", STOPPED: "stopped"}
STALE_AFTER = 6 * 3600

# transcript line kinds
LOG_USER, LOG_CLAUDE, LOG_RESULT = "u", "c", "r"
LOG_MAX = 32  # the keypad scrolls back through these (firmware MAX_LOG)
MAX_SESSIONS = 8  # what a keypad lists


@dataclass
class Session:
    id: str
    project: str = ""
    cwd: str = ""
    transcript: str = ""  # path of the session's transcript file (the live feed follows it)
    state: str = IDLE
    title: str = ""
    detail: str = ""
    continues: int = 0
    started: float = field(default_factory=time.time)
    turn: float = 0.0  # when the current stretch of work began (the keypad's elapsed timer)
    last: float = field(default_factory=time.time)
    name: str = ""  # the session's title, as on its terminal tab
    mode: str = ""  # Claude Code's permission mode (default, acceptEdits, plan, auto, dontAsk, bypassPermissions; "" = not seen yet)
    log: list[dict[str, str]] = field(default_factory=list)  # latest transcript lines, oldest first

    def view(self) -> dict[str, Any]:
        return {**{k: copy.copy(v) for k, v in self.__dict__.items()}, "phase": phase(self.state)}


def phase(state: str) -> str:
    return PHASES.get(state, "idle")


def fit_log(kind: str, text: str) -> str:
    """A transcript entry as the keypad stores it: Claude's text whole (up to
    8 KB) with its Markdown styled, your prompt up to 2 KB, tool lines short."""
    if kind == LOG_CLAUDE:
        return render(text, max_bytes=proto.LOG_CLAUDE_TEXT)
    return proto.fit(device_text(text), proto.LOG_USER_TEXT if kind == LOG_USER else proto.LOG_TEXT)


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

    def touch(self, sid: str, state: str, title: str, detail: str, cwd: str = "") -> None:
        """Records an event. It becomes the current session unless another is pinned."""
        with self._lock:
            x = self._get(sid)
            if cwd:
                x.cwd, x.project = cwd, project_of(cwd)
            if x.state not in BUSY_STATES and state in BUSY_STATES:
                x.turn = time.time()  # a new turn: restart the "Working… (12s)" timer
            x.state, x.title, x.detail, x.last = state, title, detail, time.time()
            if state == ENDED:
                if self._pinned == sid:
                    self._pinned = ""
                if self._current == sid:
                    self._current = self._latest(exclude=sid)
                return
            if not self._pinned or self._pinned == sid:
                self._current = sid

    def start(self, sid: str, cwd: str) -> None:
        """Registers a (re)started session and resets its counters."""
        with self._lock:
            x = self._get(sid)
            x.continues, x.started = 0, time.time()
        self.touch(sid, IDLE, "Ready", "", cwd)

    def get(self, sid: str) -> Session | None:
        with self._lock:
            x = self._m.get(sid)
            return copy.deepcopy(x) if x else None

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

    def set_transcript(self, sid: str, path: str) -> None:
        if path:
            with self._lock:
                self._get(sid).transcript = path

    def set_cwd(self, sid: str, cwd: str) -> None:
        """The working directory, when a session was found before any hook told us."""
        if cwd:
            with self._lock:
                x = self._get(sid)
                if not x.cwd:
                    x.cwd, x.project = cwd, project_of(cwd)

    def set_name(self, sid: str, name: str) -> None:
        if name:
            with self._lock:
                self._get(sid).name = name

    def set_mode(self, sid: str, mode: str) -> None:
        with self._lock:
            self._get(sid).mode = mode


def wire(sessions: list[Session]) -> list[dict[str, Any]]:
    """Live sessions for the status message (the latest 8)."""
    out = []
    now = time.time()
    for x in sessions:
        d = {"id": short(x.id), "project": proto.fit(x.project, proto.SESSION_PROJECT), "state": phase(x.state),
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
