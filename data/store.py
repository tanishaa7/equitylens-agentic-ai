"""Small on-disk JSON cache with expiry — saves Yahoo calls and, above all, the
free AI quota (a finished comparison is reused for the rest of the day).

    value = cached_call("market", "TCS.NS", 30 * MIN, get_market_data, "TCS.NS",
                        ok=lambda v: v["found"])

Files live in cache/store/<namespace>/<hash>.json. Writes are atomic, and a
broken or unreadable file is treated as a cache miss — the cache can never
break a comparison.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import config

log = logging.getLogger("equitylens.cache")

ROOT = config.CACHE_DIR / "store"
ENABLED = os.getenv("EQUITYLENS_CACHE", "1") != "0"   # set EQUITYLENS_CACHE=0 to switch off

MIN, HOUR = 60, 3600
IST = ZoneInfo("Asia/Kolkata")


def today_ist() -> str:
    return datetime.now(IST).date().isoformat()


def _path(ns: str, key: str) -> Path:
    return ROOT / ns / (hashlib.sha1(key.encode()).hexdigest()[:20] + ".json")


def get(ns: str, key: str, ttl: float) -> Any | None:
    """Cached value if present and younger than `ttl` seconds, else None."""
    if not ENABLED:
        return None
    p = _path(ns, key)
    try:
        entry = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if entry.get("key") != key or time.time() - entry.get("saved_at", 0) > ttl:
        return None
    return entry.get("value")


def put(ns: str, key: str, value: Any) -> None:
    if not ENABLED:
        return
    p = _path(ns, key)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"key": key, "saved_at": time.time(), "value": value}, default=str))
        tmp.replace(p)   # atomic: readers never see a half-written file
    except OSError:
        log.warning("couldn't write cache %s/%s", ns, key, exc_info=True)


def cached_call(ns: str, key: str, ttl: float, fn: Callable, *args,
                ok: Callable[[Any], bool] = lambda v: True, **kwargs):
    """Return fn(*args) from the cache if fresh; otherwise call it and cache the
    result — only if ok(result), so failures are never stored."""
    hit = get(ns, key, ttl)
    if hit is not None:
        log.debug("cache hit %s/%s", ns, key)
        return hit
    value = fn(*args, **kwargs)
    if value is not None and ok(value):
        put(ns, key, value)
    return value


def clear(ns: str | None = None) -> int:
    """Delete cached files (one namespace or all). Returns how many were removed."""
    base = ROOT / ns if ns else ROOT
    n = 0
    for f in base.rglob("*.json") if base.exists() else []:
        f.unlink(missing_ok=True)
        n += 1
    return n
