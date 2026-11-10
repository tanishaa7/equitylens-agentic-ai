"""Usage limits for the public website — protects the free Gemini quota.

- Each visitor: MAX_RUNS_PER_VISITOR_HOUR new comparisons per rolling hour.
- Whole site:   MAX_RUNS_PER_DAY new comparisons per day (India time).
Saved results (same companies, same day) are free and never count.

Visitors are told apart by a hash of their IP address, kept in memory only —
never written to disk or logs. Limits reset when the app restarts, which is
fine for a prototype. Override the numbers with env vars / Streamlit secrets.

    msg = check(visitor_id)      # None = allowed, else a message for the user
    record(visitor_id)           # after a new (non-cached) comparison starts
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from datetime import datetime, timedelta

from data.store import IST, today_ist

MAX_RUNS_PER_VISITOR_HOUR = int(os.getenv("MAX_RUNS_PER_VISITOR_HOUR", "3"))
MAX_RUNS_PER_DAY = int(os.getenv("MAX_RUNS_PER_DAY", "40"))
WINDOW = 3600

_lock = threading.Lock()
_visitor_runs: dict[str, list[float]] = {}
_day = {"date": "", "count": 0}


def visitor_id(ip: str | None, fallback: str) -> str:
    """Anonymous id: hash of the IP (or of the browser session if no IP is available)."""
    raw = (ip or "").split(",")[0].strip() or fallback
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _midnight_ist() -> str:
    t = (datetime.now(IST) + timedelta(days=1)).replace(hour=0, minute=0)
    return t.strftime("%-d %b, 12:00 AM IST")


def check(vid: str, now: float | None = None) -> str | None:
    now = now or time.time()
    with _lock:
        if _day["date"] != today_ist():
            _day.update(date=today_ist(), count=0)
        if _day["count"] >= MAX_RUNS_PER_DAY:
            return ("EquityLens has reached today's limit of new comparisons for this free demo. "
                    f"Saved comparisons still work. New ones open again after {_midnight_ist()}.")
        recent = [t for t in _visitor_runs.get(vid, []) if now - t < WINDOW]
        _visitor_runs[vid] = recent
        if len(recent) >= MAX_RUNS_PER_VISITOR_HOUR:
            wait = int((WINDOW - (now - min(recent))) / 60) + 1
            return (f"You've run {len(recent)} new comparisons in the last hour — the limit for "
                    f"this free demo. Please try again in about {wait} minutes. Comparisons "
                    "someone already ran today still open instantly.")
    return None


def record(vid: str, now: float | None = None) -> None:
    now = now or time.time()
    with _lock:
        if _day["date"] != today_ist():
            _day.update(date=today_ist(), count=0)
        _day["count"] += 1
        _visitor_runs.setdefault(vid, []).append(now)


def reset() -> None:
    with _lock:
        _visitor_runs.clear()
        _day.update(date="", count=0)
