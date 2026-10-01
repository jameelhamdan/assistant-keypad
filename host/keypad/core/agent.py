"""Agent ties sessions, dialogs, keypads and configuration together. It is
the device-event sink and the Display for dialogs."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .. import config, proto
from .ctx import Ctx
from .dialogs import Dialog, DialogError, Dialogs
from .hooks import HookMixin
from .sessions import Sessions, short, wire
from .text import clip, first_line

if TYPE_CHECKING:
    from ..device.hub import Conn, Hub


SAVE_EVERY = 3.0  # s between writes of sessions.json


class Agent(HookMixin):
    def __init__(self, cfg: config.Config, store: config.Store, idle: Callable[[], tuple[float, bool]] | None,
                 log: logging.Logger, presence: Callable[[], tuple[float, bool]] | None = None):
        self.log = log
        self.presence = presence  # seconds since any input at the PC (None = unknown: treated as away)
        self.store = store
        self.hub: Hub | None = None
        self.sessions = Sessions()
        self._cfg_lock = threading.Lock()
        self._cfg = cfg
        self._paused = False
        self._dirty = threading.Event()
        self._pend_lock = threading.Lock()
        self._pending: dict[str, tuple[config.Shortcut, float]] = {}
        self.dialogs = Dialogs(self, idle, lambda: self.config().behavior.pc_handback, log)
        self.dialogs.on_change = self.mark_dirty
        self._sessions_file = config.sessions_path()  # fixed at start: a late save must not land elsewhere
        self._load_sessions()

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

    def paused(self) -> bool:
        return self._paused

    def set_paused(self, p: bool) -> None:
        self._paused = p
        self.mark_dirty()

    def mark_dirty(self) -> None:
        self._dirty.set()

    def run(self, stop: threading.Event) -> None:
        """Pushes status updates to keypads until stop is set, and keeps the
        sessions on disk (at most every few seconds)."""
        saved_at, unsaved = 0.0, False
        while not stop.is_set():
            if self._dirty.wait(0.5):
                time.sleep(0.08)  # coalesce bursts of hook events
                self._dirty.clear()
                self.push_status()
                unsaved = True
            if unsaved and time.monotonic() - saved_at > SAVE_EVERY:
                self._save_sessions()
                saved_at, unsaved = time.monotonic(), False
        if unsaved:
            self._save_sessions()

    def _load_sessions(self) -> None:
        try:
            self.sessions.restore(json.loads(self._sessions_file.read_text(encoding="utf-8")))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            self.log.warning("could not restore sessions: %s", e)

    def _save_sessions(self) -> None:
        """The session list and transcripts (already redacted), for the user only."""
        try:
            config.write_atomic(self._sessions_file, json.dumps(self.sessions.dump()), 0o600)
        except OSError as e:
            self.log.warning("could not save sessions: %s", e)

    # ---- Display ----

    def targets(self, project: str) -> list[str]:
        """Connected keypads whose project filter accepts project."""
        if self.hub is None:
            return []
        return [c.id for c in self.hub.conns() if self.accepts(c.id, project)]

    def send_to(self, dev_id: str, msg: dict[str, Any]) -> None:
        if self.hub and (c := self.hub.get(dev_id)):
            c.send(msg)

    def accepts(self, dev_id: str, project: str) -> bool:
        d = self.store.device(dev_id)
        if d is None or not d.projects or not project:
            return True
        return any(p.lower() == project.lower() for p in d.projects)

    def toast(self, text: str, level: str = "info", ms: int = 2500) -> None:
        """A short message on every keypad."""
        if self.hub is None:
            return
        for c in self.hub.conns():
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
        for c in self.hub.conns():
            lst = wire(live, lambda p, i=c.id: self.accepts(i, p))
            sel = shown
            if not any(s["id"] == sel for s in lst):
                sel = lst[0]["id"] if lst else ""
            st: dict[str, Any] = {"t": "status", "sessions": lst, "sel": sel, "pinned": self.sessions.pinned(),
                                  "queue": self.dialogs.queued(), "paused": self.paused(), "menu": menu}
            log = next((x.log for x in live if short(x.id) == sel), [])
            # The transcript is the flexible part: drop its oldest entries until
            # its text fits the keypad's buffer and the message the protocol limit.
            while log and sum(len(x["t"].encode()) + 1 for x in log) > proto.LOG_POOL:
                log = log[1:]
            while True:
                if log:
                    st["log"] = log
                else:
                    st.pop("log", None)
                try:
                    proto.encode(st)
                    break
                except proto.InvalidMessage:
                    if not log:
                        break
                    log = log[1:]
            c.send(st)

    # ---- device events ----

    def connected(self, c: Conn) -> None:
        self.push_status()
        self.dialogs.reshow(c.id)

    def disconnected(self, c: Conn) -> None:
        self.mark_dirty()

    def message(self, c: Conn, m: dict[str, Any]) -> None:
        t = m["t"]
        if t == "ack":
            self.dialogs.ack(c.id, m.get("id", ""))
        elif t == "press":
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
        elif t == "wifi":
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
        ctx = Ctx.background().with_timeout(30)

        def fn(d: Dialog) -> None:
            p = d.show(ctx, {**self.shortcut_screen("Send to Claude", [], [], "back"), "project": cur.project})
            if p.get("act") == "pick" and isinstance(p.get("idx"), int):
                threading.Thread(target=self.queue_shortcut, args=(p["idx"], cur.id), daemon=True).start()

        try:
            self.dialogs.run(ctx, cur.project, "menu", False, fn, sid=cur.id)
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
        if not p or time.monotonic() - p[1] > self.config().behavior.shortcut_ttl:
            return None
        return p[0]

    # ---- screen builders ----

    @staticmethod
    def picked(press: dict[str, Any]) -> int | None:
        """The option a press chose, or None (Esc "back")."""
        idx = press.get("idx")
        return idx if press.get("act") == "pick" and isinstance(idx, int) else None

    @staticmethod
    def yes_no(project: str, title: str, question: str, esc: str) -> dict[str, Any]:
        """A select screen with 1. Yes / 2. No (option 0 is Yes)."""
        return {"tpl": "select", "title": title, "project": project, "q": question, "items": ["Yes", "No"], "esc": esc}

    # ---- snapshot for the tray and CLI ----

    def snapshot(self) -> dict[str, Any]:
        live = self.sessions.live()
        conns = self.hub.conns() if self.hub is not None else []
        # which sessions the connected keypads list (project filters, at most 8 each)
        on = {x["id"] for c in conns for x in wire(live, lambda p, i=c.id: self.accepts(i, p))}
        sessions = []
        for x in live:
            v = x.view()
            v["on_keypad"] = short(x.id) in on
            sessions.append(v)
        return {
            "host_id": self.store.host_id(), "paused": self.paused(), "busy": self.dialogs.busy(),
            "queue": self.dialogs.queued(), "keypads": [c.info() for c in conns],
            "devices": [d.view() for d in self.store.devices()], "sessions": sessions,
            "current": self.shown_id(), "pinned": self.sessions.pinned(),
            "shortcuts": self.shortcut_labels(),
        }
