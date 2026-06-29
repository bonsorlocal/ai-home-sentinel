"""Shared helper functions.

Small utilities used across the project (timestamps, logging helpers, etc.).
Phase 1 keeps this minimal; later phases add more helpers here.
"""

from __future__ import annotations

import datetime as _datetime


def now_iso() -> str:
    """Return the current local time as an ISO 8601 string.

    Used to put consistent, human-readable timestamps on events and logs.
    """
    return _datetime.datetime.now().isoformat(timespec="seconds")
