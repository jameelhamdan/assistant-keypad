"""Saved prompts: the list on the keypad, and the one queued for a session's next hook."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

from .. import config, proto
from .ctx import Ctx
from .dialogs import Dialog, DialogError
from .sessions import short
from .text import clip

if TYPE_CHECKING:
    from .agent import Agent

TTL = 900  # s a queued saved prompt waits for the session's next hook


class Shortcuts:
    def __init__(self, agent: Agent):
        self.a = agent
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[config.Shortcut, float]] = {}

    def labels(self) -> list[str]:
        return [clip(s.label, 28) for s in self.a.config().shortcuts]

    def quick(self) -> list[str]:
        """The first saved prompts, short, for keys 1-3 on the keypad's status screen."""
        return [clip(s.label, 8) for s in self.a.config().shortcuts[:proto.MAX_QUICK]]

    def screen(self, title: str, items: list[str], esc: str) -> dict[str, Any]:
        """The prompt box with saved prompts as suggestions (prompt template)."""
        return {"tpl": "prompt", "title": title, "items": items + self.labels(), "esc": esc}

    def menu(self) -> None:
        """Enter on the status screen: send a saved prompt to the shown session."""
        cur = self.a.sessions.get(self.a.shown_id())
        if not cur:
            return
        ctx = Ctx().with_timeout(30)

        def fn(d: Dialog) -> None:
            p = d.show(ctx, {**self.screen("Send to Claude", [], "back"), "project": cur.project})
            if p.get("act") == "pick" and isinstance(p.get("idx"), int):
                threading.Thread(target=self.queue, args=(p["idx"], cur.id), daemon=True).start()

        try:
            self.a.dialogs.run(ctx, cur.project, "menu", fn, sid=cur.id)
        except DialogError:
            pass

    def queue(self, idx: int, sid: str) -> None:
        """Queues saved prompt idx for a session. It is delivered on the
        session's next hook: the next tool result while Claude works (it queues
        the prompt itself), its stop, or your next prompt."""
        cfg = self.a.config()
        if not 0 <= idx < len(cfg.shortcuts):
            return
        sc = cfg.shortcuts[idx]
        s = self.a.sessions.get(sid)
        if not s:
            return
        with self._lock:
            self._pending[sid] = (sc, time.monotonic())
        self.a.log.info("shortcut queued session=%s project=%s label=%s", short(sid), s.project, sc.label)
        where = "" if self.a.sessions.is_busy(sid) else " (with your next prompt)"
        self.a.toast(f"Queued for {s.project}: {sc.label}{where}", "info", 2500)
        self.a.mark_dirty()

    def queued(self, sid: str) -> str:
        """The label of the saved prompt waiting for a session's next hook ("" if none)."""
        with self._lock:
            p = self._pending.get(sid)
        return clip(p[0].label, 20) if p and time.monotonic() - p[1] <= TTL else ""

    def cancel(self, sid: str) -> bool:
        """Takes back a queued saved prompt that Claude has not received yet."""
        label = self.queued(sid)
        with self._lock:
            self._pending.pop(sid, None)
        if label:
            self.a.log.info("shortcut cancelled session=%s label=%s", short(sid), label)
            self.a.toast(f"Cancelled: {label}", "info", 2000)
            self.a.mark_dirty()
        return bool(label)

    def take(self, sid: str) -> config.Shortcut | None:
        with self._lock:
            p = self._pending.pop(sid, None)
        if p:
            self.a.mark_dirty()  # the keypad stops showing it as waiting
        if not p or time.monotonic() - p[1] > TTL:
            return None
        return p[0]
