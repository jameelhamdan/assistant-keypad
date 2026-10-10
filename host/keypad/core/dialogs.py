"""Interactive requests: one dialog owns the keypads at a time, FIFO across
every Claude Code session."""

from __future__ import annotations

import logging
import queue
import secrets
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from .. import proto
from ..proto import Why
from .ctx import Ctx
from .stats import Stats

TICK = 0.25
RECONNECT_GRACE = 20.0  # s a dialog waits for a keypad that dropped to come back (its screen is shown again)


class DialogError(Exception):
    """The keypad did not decide (nobody there, no answer in time, the PC answered, it
    was unplugged): let Claude Code use its own UI. The message says why."""


def no_keypad() -> DialogError:
    return DialogError("no keypad connected")


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
    def targets(self) -> list[str]: ...
    def send_to(self, dev_id: str, msg: dict[str, Any]) -> None: ...


class Dialog:
    """One interactive request; it may show several screens."""

    def __init__(self, m: Dialogs, did: str, project: str, sid: str = ""):
        self.m, self.id, self.project = m, did, project
        self.sid = sid  # the Claude Code session asking: the keypads show it meanwhile
        self.turn = threading.Event()
        self.press: queue.Queue[tuple[dict[str, Any], str]] = queue.Queue(maxsize=4)
        self._lock = threading.Lock()
        self.screen: dict[str, Any] | None = None
        self.targets: list[str] = []
        self.cancelled = ""  # set when the PC answered first: why the screen goes away
        self.n = 0

    def show(self, ctx: Ctx, screen: dict[str, Any]) -> dict[str, Any]:
        """Displays a screen and waits for a press, which is returned.
        "pc" (hand to PC), timeouts and disconnects raise DialogError."""
        m = self.m
        targets = m.disp.targets()
        if not targets:
            raise no_keypad()
        with self._lock:
            self.n += 1
            s = proto.fit_screen(dict(screen, t="screen", id=f"{self.id}-{self.n}"))
            if (rem := ctx.remaining()) is not None:
                s["timeout"] = max(1, int(rem))
            self.screen, self.targets = s, list(targets)
        while not self.press.empty():  # presses for an earlier screen
            self.press.get_nowait()
        for dev in targets:
            m.disp.send_to(dev, s)

        lost = 0.0  # when the last keypad dropped (monotonic), 0 while one is connected
        while True:
            wait = TICK if (rem := ctx.remaining()) is None else max(0.0, min(TICK, rem))
            try:
                press, dev = self.press.get(timeout=wait)
            except queue.Empty:
                pass
            else:
                if press.get("id") != s["id"]:
                    m.stats.count("press_stale")
                    continue  # a late press for an earlier screen of this dialog
                if press.get("act") in ("pc", "done"):  # "done" on the finished screen: stop there
                    self.close_screen(Why.PC, dev)
                    raise DialogError("handed back to the PC")
                self.close_screen(Why.ANSWERED, dev)
                return press
            if self.cancelled:
                self.close_screen(Why.PC, "")
                raise DialogError(self.cancelled)
            if ctx.done():
                self.close_screen(Why.TIMEOUT, "")
                raise DialogError("no answer on the keypad")
            if m.disp.targets():
                lost = 0.0
            elif not lost:
                lost = time.monotonic()
            elif time.monotonic() - lost > RECONNECT_GRACE:
                self.close_screen(Why.DISCONNECTED, "")
                raise no_keypad()

    def close_screen(self, why: Why, except_dev: str) -> None:
        """Tells keypads (except the one that answered, which already locked
        itself) that the current screen is gone."""
        with self._lock:
            if self.screen is None:
                return
            sid, targets = self.screen["id"], list(self.targets)
            if why == Why.DONE:
                self.screen = None
        for dev in targets:
            if dev != except_dev or why == Why.DONE:
                self.m.disp.send_to(dev, {"t": "close", "id": sid, "why": why})


class Dialogs:
    def __init__(self, disp: Display, log: logging.Logger, stats: Stats | None = None):
        self.disp, self.log = disp, log
        self.stats = stats or Stats()
        self.on_change: Callable[[], None] = lambda: None
        self._lock = threading.Lock()
        self._queue: list[Dialog] = []
        self._active: Dialog | None = None
        self._seq = 0
        # Screen ids must not repeat across agent restarts: a keypad that sees the id of its last answer again
        # takes it for a resend and repeats that answer instead of showing the new screen.
        self._run = secrets.token_hex(2)

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

    def cancel(self, sid: str, why: str) -> bool:
        """The PC answered first (Claude moved on while sid's dialog was open):
        the dialog ends and its hook returns "no decision"."""
        with self._lock:
            d = self._active
        if d is None or not sid or d.sid != sid:
            return False
        d.cancelled = why
        return True

    def run(self, ctx: Ctx, project: str, kind: str, fn: Callable[[Dialog], Any], sid: str = "") -> Any:
        """Waits for the keypads, then runs fn(dialog) and returns its result.
        DialogError means "let Claude Code use its own UI"."""
        if not self.disp.targets():
            raise no_keypad()
        with self._lock:
            self._seq += 1
            d = Dialog(self, f"{kind[0]}{self._run}.{self._seq}", project, sid)
            self._queue.append(d)
            self._promote()
        self.on_change()
        while not d.turn.wait(TICK):
            if ctx.done():
                with self._lock:
                    if d in self._queue:
                        self._queue.remove(d)
                self._finish(d)  # no-op unless it won the race and became active
                raise DialogError("no answer on the keypad")
        try:
            return fn(d)
        finally:
            self._finish(d)

    def _promote(self) -> None:
        if self._active is not None or not self._queue:
            return
        d = self._queue.pop(0)
        self._active = d
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
            d.close_screen(Why.DONE, "")
        self.on_change()

    def press(self, dev: str, msg: dict[str, Any]) -> bool:
        """Routes a key press from a keypad; False if it is for a screen that is gone (the keypad
        is sent back to its status screen). A press the current screen did not offer is ignored
        and the screen stays: True."""
        with self._lock:
            d = self._active
        if d is None:
            self.stats.count("press_stale")
            return False
        with d._lock:  # queued under the lock: show() cannot swap the screen in between
            ok = d.screen is not None and d.screen["id"] == msg.get("id") and dev in d.targets
            if ok and not press_matches(d.screen, msg):
                self.stats.count("press_rejected")
                self.log.warning("ignored a press the screen did not offer: %s from %s", msg, dev)
                return True
            if ok:
                try:
                    d.press.put_nowait((msg, dev))
                except queue.Full:
                    self.stats.count("press_dropped")
                    self.log.warning("dropped a press: too many waiting for screen %s (from %s)", msg.get("id"), dev)
        if not ok:
            self.stats.count("press_stale")
        return ok

    def reshow(self, dev: str) -> bool:
        """Sends the active screen to a keypad that just (re)connected."""
        with self._lock:
            d = self._active
        if d is None:
            return False
        if dev not in self.disp.targets():
            return False
        with d._lock:
            if d.screen is None:
                return False
            if dev not in d.targets:
                d.targets.append(dev)
            s = d.screen
        self.disp.send_to(dev, s)
        return True
