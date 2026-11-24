"""One clock for the whole system, so a simulated pilot can run weeks of traffic in minutes.

Production never touches it: ``now()`` is the wall clock unless a simulation sets a time explicitly.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

_lock = threading.Lock()
_fixed: datetime | None = None


def now() -> datetime:
    with _lock:
        return _fixed if _fixed is not None else datetime.now(UTC)


def set_time(t: datetime | None) -> None:
    """Pin the clock to ``t`` (tz-aware), or pass None to return to wall-clock time."""
    global _fixed
    with _lock:
        _fixed = t if t is None or t.tzinfo else t.replace(tzinfo=UTC)
