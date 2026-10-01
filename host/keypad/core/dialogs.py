"""Interactive requests: one dialog owns the keypads at a time, FIFO across
every Claude Code session."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from .. import proto
from .ctx import Ctx

HANDBACK_GRACE = 2.0
RESEND_AFTER = 1.5
TICK = 0.25


class DialogError(Exception):
    """Any of these means: let Claude Code use its own UI."""


class NoKeypad(DialogError):
    def __init__(self) -> None:
        super().__init__("no keypad connected")


class Timeout(DialogError):
    def __init__(self) -> None:
        super().__init__("no answer on the keypad")


class ToPC(DialogError):
    def __init__(self) -> None:
        super().__init__("handed back to the PC")


def press_matches(screen: dict[str, Any], press: dict[str, Any]) -> bool:
    """Whether a press is one the screen offered: Esc with the screen's esc
    action, Enter on an option, a number key on its own option, or Submit with
    valid picks. Anything else is not a decision, whatever it claims."""
    key, act = press.get("key"), press.get("act")
    if not isinstance(key, int) or not isinstance(act, str):
        return False
    items = screen.get("items") or []
    if act == screen.get("esc") and act:
        return key in proto.ESC_KEYS
    if screen.get("tpl") == "multi":
        sel = press.get("sel")
        return (act == "submit" and key == proto.KEY_ENTER and isinstance(sel, list) and bool(sel)
                and all(isinstance(i, int) and 0 <= i < len(items) for i in sel))
    idx = press.get("idx")
    if act != "pick" or not isinstance(idx, int) or not 0 <= idx < len(items):
        return False
    return key == proto.KEY_ENTER or (1 <= key <= proto.DIRECT_PICKS and idx == key - 1)


class Display(Protocol):
    def targets(self, project: str) -> list[str]: ...
    def send_to(self, dev_id: str, msg: dict[str, Any]) -> None: ...


class Dialog:
    """One interactive request; it may show several screens."""

    def __init__(self, m: Dialogs, did: str, project: str, handback: bool, sid: str = ""):
        self.m, self.id, self.project, self.handback = m, did, project, handback
        self.sid = sid  # the Claude Code session asking: the keypads show it meanwhile
        self.turn = threading.Event()
        self.press: queue.Queue[tuple[dict[str, Any], str]] = queue.Queue(maxsize=4)
        self._lock = threading.Lock()
        self.screen: dict[str, Any] | None = None
        self.targets: list[str] = []
        self.acked: set[str] = set()
        self.activated = 0.0
        self.idle_ok = False
        self.n = 0

    def show(self, ctx: Ctx, screen: dict[str, Any]) -> dict[str, Any]:
        """Displays a screen and waits for a press, which is returned.
        "pc" (hand to PC), timeouts and disconnects raise DialogError."""
        m = self.m
        targets = m.disp.targets(self.project)
        if not targets:
            raise NoKeypad()
        with self._lock:
            self.n += 1
            s = proto.fit_screen(dict(screen, t="screen", id=f"{self.id}-{self.n}"))
            if (rem := ctx.remaining()) is not None:
                s["timeout"] = max(1, int(rem))
            self.screen, self.targets, self.acked = s, list(targets), set()
        while not self.press.empty():  # presses for an earlier screen
            self.press.get_nowait()
        for dev in targets:
            m.disp.send_to(dev, s)

        resend_at = time.monotonic() + RESEND_AFTER
        while True:
            wait = TICK if (rem := ctx.remaining()) is None else max(0.0, min(TICK, rem))
            try:
                press, dev = self.press.get(timeout=wait)
            except queue.Empty:
                pass
            else:
                if press.get("act") == "pc":
                    self.close_screen("pc", dev)
                    raise ToPC()
                self.close_screen("answered", dev)
                return press
            if ctx.done():
                self.close_screen("timeout", "")
                raise Timeout()
            if resend_at and time.monotonic() >= resend_at:  # re-sent once to keypads that didn't ack
                resend_at = 0.0
                with self._lock:
                    late = [d for d in self.targets if d not in self.acked]
                for dev in late:
                    m.disp.send_to(dev, s)
            if not m.disp.targets(self.project):
                self.close_screen("disconnected", "")
                raise NoKeypad()
            if self.should_hand_back():
                m.log.info("PC activity - handing the request back to the computer (screen %s)", s["id"])
                self.close_screen("pc", "")
                raise ToPC()

    def close_screen(self, why: str, except_dev: str) -> None:
        """Tells keypads (except the one that answered, which already locked
        itself) that the current screen is gone."""
        with self._lock:
            if self.screen is None:
                return
            sid, targets = self.screen["id"], list(self.targets)
            if why == "done":
                self.screen = None
        for dev in targets:
            if dev != except_dev or why == "done":
                self.m.disp.send_to(dev, {"t": "close", "id": sid, "why": why})

    def should_hand_back(self) -> bool:
        m = self.m
        if not self.handback or m.idle is None or not self.idle_ok:
            return False
        hb = m.handback()
        if not hb.enabled:
            return False
        idle, ok = m.idle()
        if not ok:
            return False
        # Input that began after the request appeared (with a grace period, so
        # typing that was already under way doesn't count) means "I'm at the PC".
        return idle + HANDBACK_GRACE < time.monotonic() - self.activated


class Dialogs:
    def __init__(self, disp: Display, idle: Callable[[], tuple[float, bool]] | None, handback: Callable[[], Any],
                 log: logging.Logger):
        self.disp, self.idle, self.handback, self.log = disp, idle, handback, log
        self.on_change: Callable[[], None] = lambda: None
        self._lock = threading.Lock()
        self._queue: list[Dialog] = []
        self._active: Dialog | None = None
        self._seq = 0

    def queued(self) -> int:
        """How many dialogs wait behind the active one."""
        with self._lock:
            return len(self._queue)

    def busy(self) -> bool:
        with self._lock:
            return self._active is not None

    def active_sid(self) -> str:
        """The session the active dialog belongs to ("" if none)."""
        with self._lock:
            return self._active.sid if self._active else ""

    def run(self, ctx: Ctx, project: str, kind: str, handback: bool, fn: Callable[[Dialog], Any], sid: str = "") -> Any:
        """Waits for the keypads, then runs fn(dialog) and returns its result.
        DialogError means "let Claude Code use its own UI"."""
        if not self.disp.targets(project):
            raise NoKeypad()
        with self._lock:
            self._seq += 1
            d = Dialog(self, f"{kind[0]}{self._seq}", project, handback, sid)
            self._queue.append(d)
            self._promote()
        self.on_change()
        while not d.turn.wait(TICK):
            if ctx.done():
                with self._lock:
                    if d in self._queue:
                        self._queue.remove(d)
                self._finish(d)  # no-op unless it won the race and became active
                raise Timeout()
        try:
            return fn(d)
        finally:
            self._finish(d)

    def _promote(self) -> None:
        if self._active is not None or not self._queue:
            return
        d = self._queue.pop(0)
        self._active = d
        d.activated = time.monotonic()
        if self.idle is not None:
            _, d.idle_ok = self.idle()
        d.turn.set()

    def _finish(self, d: Dialog) -> None:
        """Ends d if it is active and hands the keypads to the next dialog. The
        keypads are told outside the lock: a slow link must not stall others."""
        with self._lock:
            mine = self._active is d
            if mine:
                self._active = None
                self._promote()
        if mine:
            d.close_screen("done", "")
        self.on_change()

    def press(self, dev: str, msg: dict[str, Any]) -> bool:
        """Routes a key press from a keypad; False if it answers nothing."""
        with self._lock:
            d = self._active
        if d is None:
            return False
        with d._lock:
            ok = d.screen is not None and d.screen["id"] == msg.get("id") and dev in d.targets
            if ok and not press_matches(d.screen, msg):
                self.log.warning("ignored a press the screen did not offer: %s from %s", msg, dev)
                return False
        if ok:
            try:
                d.press.put_nowait((msg, dev))
            except queue.Full:
                pass
        return ok

    def ack(self, dev: str, sid: str) -> None:
        with self._lock:
            d = self._active
        if d is None:
            return
        with d._lock:
            if d.screen is not None and d.screen["id"] == sid:
                d.acked.add(dev)

    def reshow(self, dev: str) -> bool:
        """Sends the active screen to a keypad that just (re)connected."""
        with self._lock:
            d = self._active
        if d is None:
            return False
        if dev not in self.disp.targets(d.project):
            return False
        with d._lock:
            if d.screen is None:
                return False
            if dev not in d.targets:
                d.targets.append(dev)
            s = d.screen
        self.disp.send_to(dev, s)
        return True
