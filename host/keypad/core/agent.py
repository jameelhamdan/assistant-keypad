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
from .ctx import Ctx
from .dialogs import Dialog, DialogError, Dialogs
from .hooks import Hooks
from .sessions import (
    BUSY_STATES,
    CONTINUING,
    DONE,
    IDLE,
    THINKING,
    TOOL,
    WORKING,
    Sessions,
    short,
    wire,
)
from .text import clip, first_line, tool_verb
from .transcript import CLAUDE, INTERRUPT, RESULT, USER, Tail
from .transcript import TOOL as T_TOOL

if TYPE_CHECKING:
    from ..device.hub import Conn, Hub


SHORTCUT_TTL = 900  # s a queued saved prompt waits for the session's next hook
POLL_EVERY = 0.3  # s between looks at the sessions' transcripts
DISCOVER_WITHIN = 900  # s: transcripts touched this recently belong to sessions already running when the agent starts
# states in which new transcript lines may change the shown state (not while a request waits on someone)
FOLLOWABLE = {IDLE, DONE, THINKING, WORKING, TOOL, CONTINUING}


class Agent:
    def __init__(self, cfg: config.Config, store: config.Store, log: logging.Logger, presence: Callable[[], tuple[float, bool]] | None = None):
        self.log = log
        self.presence = presence  # seconds since any input at the PC (None = unknown: treated as away)
        self.store = store
        self.hub: Hub | None = None
        self.sessions = Sessions()
        self._cfg_lock = threading.Lock()
        self._cfg = cfg
        self._rev = 1  # bumped on every change of the settings (a client's edit must start from the current one)
        self._paused = False
        self._dirty = threading.Event()
        self._pend_lock = threading.Lock()
        self._pending: dict[str, tuple[config.Shortcut, float]] = {}
        self.dialogs = Dialogs(self, log)
        self._hooks = Hooks(self)
        self.dialogs.on_change = self.mark_dirty
        self._tails: dict[str, Tail] = {}
        self._unreadable: set[str] = set()  # sessions whose transcript could not be understood (logged once)
        self._tail_lock = threading.Lock()

    def hook(self, ctx: Ctx, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Claude Code's hook decision; {} means none."""
        return self._hooks.hook(ctx, event, payload)

    # ---- settings ----

    def config(self) -> config.Config:
        with self._cfg_lock:
            return self._cfg.copy()

    def config_rev(self) -> int:
        with self._cfg_lock:
            return self._rev

    def set_config(self, c: config.Config, rev: int | None = None) -> bool:
        """Replaces the settings. With rev, only when nobody changed them since that revision."""
        with self._cfg_lock:
            if rev is not None and rev != self._rev:
                return False
            self._cfg = c.copy()
            self._rev += 1
        self.mark_dirty()
        return True

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
            self.sessions.touch(sid, THINKING, "Working", clip(last.text, 160))
        elif last.kind == T_TOOL:
            self.sessions.touch(sid, TOOL, tool_verb(last.tool), last.detail)
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

    def shown_id(self) -> str:
        """The session the keypads show: the one a request on screen came
        from, else the pinned or latest one."""
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
            st: dict[str, Any] = {"t": "status", "sessions": lst, "sel": sel, "pinned": self.sessions.pinned(),
                                  "queue": self.dialogs.queued(), "paused": self.paused(), "menu": menu}
            c.send(st)
            self.push_feed(c, sel, next((x.log for x in live if short(x.id) == sel), []))

    def push_feed(self, c: Conn, sel: str, log: list[dict[str, str]]) -> None:
        """Gives a keypad the transcript of the selected session, when it changed."""
        # The oldest entries go until the text fits the keypad's buffer and the message the protocol limit.
        while log and sum(len(x["t"].encode()) + 1 for x in log) > proto.LOG_POOL:
            log = log[1:]
        if c.feed == (sel, log):
            return
        while True:
            msg = {"t": "feed", "sid": sel, "full": log}
            try:
                proto.encode(msg)
                break
            except proto.InvalidMessage:
                if not log:
                    return
                log = log[1:]
        if c.send(msg):
            c.feed = (sel, log)

    # ---- device events ----

    def connected(self, c: Conn) -> None:
        c.feed = None  # a (re)connected keypad has no transcript yet
        if c.live:  # a cable used for setup shows nothing
            self.push_status()
            self.dialogs.reshow(c.id)

    def disconnected(self, c: Conn) -> None:
        self.mark_dirty()

    def message(self, c: Conn, m: dict[str, Any]) -> None:
        t = m["t"]
        if t == "press":
            if m.get("id") == "status":
                if m.get("act") == "menu":
                    threading.Thread(target=self.shortcut_menu, daemon=True).start()
                return
            if not self.dialogs.press(c.id, m):
                # stale screen on that keypad: send it back to the status screen
                c.send({"t": "close", "id": m.get("id", ""), "why": "stale"})
        elif t == "session":
            if m.get("act") == "follow":
                self.sessions.follow()
            else:
                self.sessions.select(m.get("sid", ""))
            self.mark_dirty()

    # ---- shortcuts ----

    def shortcut_labels(self) -> list[str]:
        return [clip(s.label, 28) for s in self.config().shortcuts]

    def shortcut_screen(self, title: str, items: list[str], notes: list[str], esc: str) -> dict[str, Any]:
        """The prompt box with saved prompts as suggestions (prompt template)."""
        cfg = self.config()
        return {"tpl": "prompt", "title": title, "items": items + self.shortcut_labels(),
                "notes": notes + [clip(first_line(sc.prompt), 46) for sc in cfg.shortcuts], "esc": esc}

    def shortcut_menu(self) -> None:
        """Enter on the status screen: send a saved prompt to the shown session."""
        cur = self.sessions.get(self.shown_id())
        if not cur:
            return
        ctx = Ctx().with_timeout(30)

        def fn(d: Dialog) -> None:
            p = d.show(ctx, {**self.shortcut_screen("Send to Claude", [], [], "back"), "project": cur.project})
            if p.get("act") == "pick" and isinstance(p.get("idx"), int):
                threading.Thread(target=self.queue_shortcut, args=(p["idx"], cur.id), daemon=True).start()

        try:
            self.dialogs.run(ctx, cur.project, "menu", fn, sid=cur.id)
        except DialogError:
            pass

    def queue_shortcut(self, idx: int, sid: str) -> None:
        """Queues saved prompt idx for a session. It is delivered on the
        session's next hook: the next tool call while Claude works, its stop,
        or your next prompt."""
        cfg = self.config()
        if not 0 <= idx < len(cfg.shortcuts):
            return
        sc = cfg.shortcuts[idx]
        s = self.sessions.get(sid)
        if not s:
            return
        with self._pend_lock:
            self._pending[sid] = (sc, time.monotonic())
        self.log.info("shortcut queued session=%s project=%s label=%s", short(sid), s.project, sc.label)
        where = "" if self.sessions.is_busy(sid) else " (with your next prompt)"
        self.toast(f"Queued for {s.project}: {sc.label}{where}", "info", 2500)

    def take_pending(self, sid: str) -> config.Shortcut | None:
        with self._pend_lock:
            p = self._pending.pop(sid, None)
        if not p or time.monotonic() - p[1] > SHORTCUT_TTL:
            return None
        return p[0]

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
            "presence": self.presence_view(), "current": self.shown_id(), "pinned": self.sessions.pinned(),
            "shortcuts": self.shortcut_labels(),
        }
