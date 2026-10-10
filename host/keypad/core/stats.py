"""Health counters for `keypad status`: how the hooks and the keypads have behaved since the agent started."""

from __future__ import annotations

import threading
import time
from collections import Counter
from typing import Any


class Stats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Counter[str] = Counter()
        self._hooks: dict[str, list[float]] = {}  # event -> [n, total s, max s]
        self.started = time.time()

    def count(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._counts[name] += n

    def hook(self, event: str, seconds: float) -> None:
        with self._lock:
            h = self._hooks.setdefault(event, [0, 0.0, 0.0])
            h[0] += 1
            h[1] += seconds
            h[2] = max(h[2], seconds)

    def view(self) -> dict[str, Any]:
        with self._lock:
            return {
                "uptime": int(time.time() - self.started),
                "counts": dict(sorted(self._counts.items())),
                "hooks": {e: {"n": int(n), "avg_ms": int(t / n * 1000), "max_ms": int(m * 1000)}
                          for e, (n, t, m) in sorted(self._hooks.items())},
            }
