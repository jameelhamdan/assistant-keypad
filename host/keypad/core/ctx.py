"""A deadline plus a cancellation check, passed down a request."""

from __future__ import annotations

import time
from collections.abc import Callable


class Ctx:
    def __init__(self, deadline: float | None = None, cancelled: Callable[[], bool] | None = None):
        self.deadline = deadline  # time.monotonic() value, or None
        self._cancelled = cancelled

    def with_timeout(self, seconds: float) -> Ctx:
        """The same context, ending at most `seconds` from now."""
        end = time.monotonic() + seconds
        return Ctx(end if self.deadline is None else min(end, self.deadline), self._cancelled)

    def done(self) -> bool:
        if self.deadline is not None and time.monotonic() >= self.deadline:
            return True
        return self._cancelled is not None and self._cancelled()

    def remaining(self) -> float | None:
        return None if self.deadline is None else max(0.0, self.deadline - time.monotonic())
