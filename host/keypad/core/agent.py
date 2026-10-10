"""Agent ties sessions, dialogs, keypads and configuration together. It is
the device-event sink and the Display for dialogs."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import config, proto
from ..proto import Why
from .ctx import Ctx
from .dialogs import Dialogs
from .hooks import Hooks
from .sessions import BUSY_STATES, CONTINUING, IDLE, WORKING, Sessions, short, wire
from .shortcuts import Shortcuts
from .stats import Stats
from .text import clip, tool_verb
from .transcript import CLAUDE, INTERRUPT, RESULT, USER, Tail
from .transcript import TOOL as T_TOOL

if TYPE_CHECKING:
    from ..device.hub import Conn, Hub


POLL_EVERY = 0.3  # s between looks at the sessions' transcripts
DISCOVER_WITHIN = 900  # s: transcripts touched this recently belong to sessions already running when the agent starts
# states in which new transcript lines may change the shown state (not while a request waits on someone)
FOLLOWABLE = {IDLE, WORKING, CONTINUING}


class Agent:
    def __init__(self, cfg: config.Config, store: config.Store, log: logging.Logger, presence: Callable[[], tuple[float, bool]] | None = None):
        self.log = log
        self.presence = presence  # seconds since any input at the PC (None = unknown: treated as away)
        self.store = store
        self.hub: Hub | None = None
        self.sessions = Sessions()
        self._cfg_lock = threading.Lock()
        self._cfg = cfg
        self._paused = False
        self._dirty = threading.Event()
        self.stats = Stats()
        self.dialogs = Dialogs(self, log, self.stats)
        self.shortcuts = Shortcuts(self)
        self._hooks = Hooks(self)
        self.dialogs.on_change = self.mark_dirty
        self.sessions.on_change = self.mark_dirty
        self._tails: dict[str, Tail] = {}
        self._unreadable: set[str] = set()  # sessions whose transcript could not be understood (logged once)
        self._tail_lock = threading.Lock()

    def hook(self, ctx: Ctx, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Claude Code's hook decision; {} means none."""
        t0 = time.monotonic()
        try:
            return self._hooks.hook(ctx, event, payload)
        finally:
            self.stats.hook(event, time.monotonic() - t0)

    # ---- settings ----

    def config(self) -> config.Config:
        with self._cfg_lock:
            return self._cfg.copy()

    def set_config(self, c: config.Config) -> None:
        with self._cfg_lock:
            self._cfg = c.copy()
        self.mark_dirty()

    def away(self, seconds: int) -> bool:
        """Whether nobody has used the PC for `seconds` (true when unknown)."""
        if seconds <= 0 or self.presence is None:
            return True
        idle, ok = self.presence()
        return not ok or idle >= seconds

    def presence_view(self) -> dict[str, Any]:
        """Whether you are at the PC, as `away` sees it (shown by `keypad status`)."""
        idle, ok = self.presence() if self.presence else (0.0, False)
        return {"known": True, "idle": int(idle)} if ok else {"known": False}

    def paused(self) -> bool:
        return self._paused

    def set_paused(self, p: bool) -> None:
        self._paused = p
        self.mark_dirty()

    def mark_dirty(self) -> None:
        self._dirty.set()

    def run(self, stop: threading.Event) -> None:
        """Follows the sessions' transcripts and pushes status updates to the
        keypads until stop is set."""
        self.discover()
        next_poll = 0.0
        while not stop.is_set():
            if self._dirty.wait(POLL_EVERY):
                time.sleep(0.08)  # coalesce bursts of events
                self._dirty.clear()
                self.push_status()
            if time.monotonic() >= next_poll:
                next_poll = time.monotonic() + POLL_EVERY
                self.sync()

    # ---- the live feed: the sessions' transcripts ----

    def discover(self, root: Path | None = None) -> None:
        """Picks up sessions that were already running when the agent started:
        their transcripts under ~/.claude/projects, touched a few minutes ago."""
        from .. import claudecfg

        root = root or claudecfg.home() / "projects"
        now = time.time()
        try:
            files = [f for f in root.glob("*/*.jsonl") if now - f.stat().st_mtime < DISCOVER_WITHIN]
        except OSError:
            return
        for f in sorted(files, key=lambda f: f.stat().st_mtime)[-8:]:
            if self.sessions.get(f.stem) is None:
                self.sessions.touch(f.stem, IDLE, "Ready", "")
                self.sessions.set_transcript(f.stem, str(f))
        self.sync()

    def forget(self, sid: str) -> None:
        with self._tail_lock:
            self._tails.pop(sid, None)
        self._unreadable.discard(sid)

    def sync(self, only: str = "") -> None:
        """Reads what the sessions' transcripts gained since last time (all of
        them, or just `only`) and mirrors it on the keypads."""
        changed = False
        for x in self.sessions.live():
            if (only and x.id != only) or not x.transcript:
                continue
            with self._tail_lock:
                tail = self._tails.get(x.id)
                if tail is None or tail.path != x.transcript:
                    tail = self._tails[x.id] = Tail(x.transcript)
                first, before = not tail.seen, tail.activity
                entries = tail.poll()
                moved = tail.activity - before
            if not tail.readable() and x.id not in self._unreadable:
                self._unreadable.add(x.id)
                self.log.warning("the transcript of session %s is not in a format Keypad knows (%d lines, no messages): "
                                 "its feed stays empty", short(x.id), tail.lines)
            if tail.title:
                self.sessions.set_name(x.id, tail.title)
            if tail.mode:
                self.sessions.set_mode(x.id, tail.mode)
            self.sessions.set_cwd(x.id, tail.cwd)
            if first or not entries and not moved:
                changed = changed or first
                for e in entries if first else []:
                    self.sessions.add_log(x.id, {"k": e.kind, "t": e.text})
                continue
            self.follow(x.id, entries, moved)
            changed = True
        if changed:
            self.mark_dirty()

    def follow(self, sid: str, entries: list, moved: int) -> None:
        """New lines of a session's transcript: log them, and move its state along."""
        for e in entries:
            if e.kind != INTERRUPT:
                self.sessions.add_log(sid, {"k": e.kind, "t": e.text})
        if moved and self.dialogs.cancel(sid, "answered on the PC"):
            self.log.info("session %s moved on while a request was open: the PC answered", short(sid))
        s = self.sessions.get(sid)
        if not s or s.state not in FOLLOWABLE or not entries:
            return
        last = entries[-1]
        if last.kind == INTERRUPT:
            self.sessions.touch(sid, IDLE, "Interrupted", "")
        elif last.kind == USER:
            self.sessions.touch(sid, WORKING, "Working", clip(last.text, 160))
        elif last.kind == T_TOOL:
            self.sessions.touch(sid, WORKING, tool_verb(last.tool), last.detail)
        elif last.kind in (CLAUDE, RESULT) and s.state in BUSY_STATES:
            self.sessions.touch(sid, WORKING, "Working", "")

    # ---- Display ----

    def targets(self) -> list[str]:
        """The keypads that are connected over Wi-Fi."""
        if self.hub is None:
            return []
        return [c.id for c in self.hub.conns() if c.live]

    def send_to(self, dev_id: str, msg: dict[str, Any]) -> None:
        if self.hub and (c := self.hub.get(dev_id)):
            c.send(msg)

    def toast(self, text: str, level: str = "info", ms: int = 2500) -> None:
        """A short message on every keypad."""
        if self.hub is None:
            return
        for c in self.hub.conns():
            if c.live:
                c.send({"t": "toast", "text": proto.fit(clip(text, 60), proto.TOAST_TEXT), "level": level, "ms": ms})

    def notify(self, text: str) -> None:
        """Lights up the keypads with a short message (Claude finished and nothing was asked on them)."""
        if self.config().behavior.notify_when_finished and not self.paused():
            self.toast(text, "ok", 4000)

    def shown_id(self) -> str:
        """The session the keypads show: the one a request on screen came
        from, else the one chosen on the keypad or the latest."""
        if sid := self.dialogs.active_sid():
            return sid
        cur = self.sessions.current()
        return cur.id if cur else ""

    def push_status(self) -> None:
        if self.hub is None:
            return
        live = self.sessions.live()
        shown = short(self.shown_id())
        menu = bool(self.config().shortcuts) and bool(shown)
        for c in (c for c in self.hub.conns() if c.live):
            lst = wire(live)
            sel = shown
            if not any(s["id"] == sel for s in lst):
                sel = lst[0]["id"] if lst else ""
            st: dict[str, Any] = {"t": "status", "sessions": lst, "sel": sel,
                                  "queue": self.dialogs.queued(), "paused": self.paused(), "menu": menu,
                                  "quick": self.shortcuts.quick() if menu else [],
                                  "queued": self.shortcuts.queued(self.shown_id())}
            c.send(st)
            c.push_feed(sel, next((x.log for x in live if short(x.id) == sel), []))

    # ---- device events ----

    def connected(self, c: Conn) -> None:
        c.feed = None  # a (re)connected keypad has no transcript yet
        if c.live:  # a cable used for setup shows nothing
            self.stats.count("keypad_connects")
            self.push_status()
            self.dialogs.reshow(c.id)

    def disconnected(self, c: Conn) -> None:
        if c.live:
            self.stats.count("keypad_disconnects")
        self.mark_dirty()

    def message(self, c: Conn, m: dict[str, Any]) -> None:
        t = m["t"]
        if t == "press":
            if m.get("id") == "status":
                sid = self.shown_id()
                if m.get("act") == "menu":
                    threading.Thread(target=self.shortcuts.menu, daemon=True).start()
                elif m.get("act") == "unqueue" and sid:
                    self.shortcuts.cancel(sid)
                elif m.get("act") == "quick" and isinstance(m.get("idx"), int) and sid:
                    threading.Thread(target=self.shortcuts.queue, args=(m["idx"], sid), daemon=True).start()
                return
            if not self.dialogs.press(c.id, m):
                # stale screen on that keypad: send it back to the status screen
                c.send({"t": "close", "id": m.get("id", ""), "why": Why.STALE})
        elif t == "session":
            self.sessions.select(m.get("sid", ""))
            self.mark_dirty()

    # ---- screen builders ----

    @staticmethod
    def picked(press: dict[str, Any]) -> int | None:
        """The option a press chose, or None (Esc "back")."""
        idx = press.get("idx")
        return idx if press.get("act") == "pick" and isinstance(idx, int) else None

    @staticmethod
    def yes_no(project: str, title: str, question: str) -> dict[str, Any]:
        """A select screen with 1. Yes / 2. No (option 0 is Yes)."""
        return {"tpl": "select", "title": title, "project": project, "q": question, "items": ["Yes", "No"]}

    # ---- snapshot for the tray and CLI ----

    def snapshot(self) -> dict[str, Any]:
        live = self.sessions.live()
        conns = self.hub.conns() if self.hub is not None else []
        on = {x["id"] for x in wire(live)}  # the sessions a keypad lists (the latest 8)
        sessions = []
        for x in live:
            v = x.view()
            v["on_keypad"] = short(x.id) in on
            v["feed_ok"] = x.id not in self._unreadable
            sessions.append(v)
        return {
            "host_id": self.store.host_id(), "paused": self.paused(), "busy": self.dialogs.busy(),
            "queue": self.dialogs.queued(), "keypads": [c.info() for c in conns],
            "devices": [d.view() for d in self.store.devices()], "problems": dict(self.hub.problems) if self.hub else {},
            "sessions": sessions,
            "presence": self.presence_view(), "current": self.shown_id(),
            "shortcuts": self.shortcuts.labels(), "stats": self.stats.view(),
        }
