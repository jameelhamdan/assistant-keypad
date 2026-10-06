"""An in-process keypad for the tests.
policy decides what it presses for each screen; None = never press."""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from keypad import proto
from keypad.device.links import Link, LinkClosed

Policy = Callable[[dict[str, Any]], dict[str, Any]]  # screen -> press fields (key, act, idx?, sel?)


def first_key(s: dict[str, Any]) -> dict[str, Any]:
    """Picks the first option (Enter on the highlighted one), or submits the first item."""
    if s.get("tpl") == "multi":
        return {"key": 7, "act": "submit", "sel": [0]}
    if not s.get("items"):
        return {"key": 5, "act": s.get("esc", "")}
    return {"key": 1, "act": "pick", "idx": 0}


def press_label(label: str) -> Policy:
    """Picks the option whose label starts with label (Esc for "pc")."""
    def policy(s: dict[str, Any]) -> dict[str, Any]:
        if label == "pc":
            return {"key": 5, "act": "pc"}
        for i, item in enumerate(s.get("items") or []):
            if str(item).lower().startswith(label.lower()):
                return {"key": 7, "act": "pick", "idx": i}
        return first_key(s)
    return policy


class Fake(Link):
    kind = "fake"
    addr = "memory"

    def __init__(self, dev_id: str, policy: Policy | None = None, delay: float = 0.0):
        self.id, self.policy, self.delay = dev_id, policy, delay
        self._lock = threading.Lock()
        self._out: queue.Queue[bytes] = queue.Queue()
        self._closed = threading.Event()
        self.screens: list[dict[str, Any]] = []
        self.status: list[dict[str, Any]] = []
        self.log: list[dict[str, Any]] = []  # the transcript, kept the way the firmware does from feed messages
        self.feeds: list[dict[str, Any]] = []

    def close(self) -> None:
        self._closed.set()

    def send(self, b: bytes) -> None:
        """A host message arriving at the fake."""
        if self._closed.is_set():
            raise LinkClosed("closed")
        self._handle(json.loads(b))

    def recv(self) -> bytes:
        while not self._closed.is_set():
            try:
                return self._out.get(timeout=0.2)
            except queue.Empty:
                continue
        raise LinkClosed("closed")

    def _emit(self, m: dict[str, Any]) -> None:
        self._out.put(json.dumps(m).encode())

    def _handle(self, m: dict[str, Any]) -> None:
        t = m.get("t")
        if t == "hello":  # like the firmware: the host says hello, we introduce ourselves
            self._emit({"t": "hello", "v": proto.VERSION, "id": self.id, "fw": "fake", "name": "Fake keypad"})
        elif t == "ping":
            self._emit({"t": "pong"})
        elif t == "status":
            with self._lock:
                self.status.append(m)
        elif t == "feed":
            with self._lock:
                self.feeds.append(m)
                self.log = list(m["full"])
        elif t == "screen":
            with self._lock:
                self.screens.append(m)
                policy = self.policy
            if policy is not None:
                threading.Thread(target=self._press, args=(m, policy), daemon=True).start()

    def _press(self, s: dict[str, Any], policy: Policy) -> None:
        time.sleep(self.delay)
        self._emit({"t": "press", "id": s["id"], **policy(s)})

    def last_screen(self) -> dict[str, Any] | None:
        with self._lock:
            return self.screens[-1] if self.screens else None

    def status_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{**s, "log": list(self.log)} if s is self.status[-1] else s for s in self.status]
