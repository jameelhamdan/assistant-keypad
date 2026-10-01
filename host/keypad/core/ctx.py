"""A small deadline + cancellation token, passed down a request (like Go's context)."""

from __future__ import annotations

import time
from collections.abc import Callable


class Ctx:
    def __init__(self, deadline: float | None = None, cancelled: Callable[[], bool] | None = None,
                 parent: Ctx | None = None):
        if parent is not None and parent.deadline is not None:
            deadline = parent.deadline if deadline is None else min(deadline, parent.deadline)
        self.deadline = deadline
        self._cancelled = cancelled
        self._parent = parent
        self._cancel = False

    @classmethod
    def background(cls) -> Ctx:
        return cls()

    def with_timeout(self, seconds: float) -> Ctx:
        return Ctx(deadline=time.monotonic() + seconds, parent=self)

    def cancel(self) -> None:
        self._cancel = True

    def done(self) -> bool:
        if self._cancel:
            return True
        if self.deadline is not None and time.monotonic() >= self.deadline:
            return True
        if self._cancelled is not None and self._cancelled():
            return True
        return self._parent.done() if self._parent is not None else False

    def remaining(self) -> float | None:
        return None if self.deadline is None else max(0.0, self.deadline - time.monotonic())
