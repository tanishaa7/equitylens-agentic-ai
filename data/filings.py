"""Official NSE filings for one company, from the history collected by
scripts/collect_nse_filings.py (run every few hours by a GitHub Action and
stored on the repo's `data` branch).

    get_filings("ICRA") -> {"status": "ok", "items": [...newest first...], "history_starts": ...}

Titles, subjects and links only — the attached PDFs are not read. The history
only goes back to when collection started (see `history_starts`).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

import config
from data.common import USER_AGENT, now_iso

IST = timezone(timedelta(hours=5, minutes=30))

# Routine filings that say nothing about the business.
NOISE = ("newspaper", "advertisement", "publication", "trading window", "duplicate share",
         "loss of share", "certificate under reg", "compliance certificate", "74(5)",
         "investor complaints", "copy of newspaper", "spurious", "esop", "esos", "allotment of")


def _read(name: str) -> str | None:
    """Text of a file in the collected store, local folder first, else the data branch.
    None if it doesn't exist (no filings collected for that company yet)."""
    if config.FILINGS_DIR:
        p = Path(config.FILINGS_DIR) / name
        return p.read_text() if p.exists() else None
    url = f"{config.FILINGS_BASE_URL}/{quote(name)}"
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.text


def name_slug(company: str) -> str:
    """Same rule as scripts/collect_nse_filings.py: filings without a symbol are
    stored under the company's name."""
    s = re.sub(r"[^A-Z0-9]+", "_", company.upper()).strip("_")
    s = re.sub(r"_(LIMITED|LTD)$", "", s)
    return f"_name_{s[:80]}" if s else "_unknown"


def is_noise(item: dict) -> bool:
    text = f"{item.get('subject', '')} {item.get('detail', '')}".lower()
    return any(n in text for n in NOISE)


def get_filings(symbol: str, name: str | None = None, days: int = 90, limit: int = 25) -> dict:
    symbol = symbol.upper().split(".")[0]
    source = {"provider": "NSE corporate filings (official RSS, collected by EquityLens)",
              "url": f"https://www.nseindia.com/get-quotes/equity?symbol={quote(symbol)}",
              "fetched_at": now_iso()}
    try:
        meta_text = _read("meta.json")
        if meta_text is None:
            return {"symbol": symbol, "status": "unavailable: filing collection not running yet",
                    "items": [], "history_starts": None, "source": source}
        meta = json.loads(meta_text)
        text = _read(f"by_symbol/{symbol}.jsonl") or ""
        if name:
            text += "\n" + (_read(f"by_symbol/{name_slug(name)}.jsonl") or "")
    except Exception as ex:   # network / parse problems shouldn't crash the pipeline
        return {"symbol": symbol, "status": f"unavailable: {ex}"[:120], "items": [],
                "history_starts": None, "source": source}

    since = (datetime.now(IST) - timedelta(days=days)).isoformat()
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    rows = [r for r in rows if (r.get("published") or "") >= since]
    rows = list({(r.get("kind"), r.get("link"), r.get("published"), r.get("subject")): r
                 for r in rows}.values())
    rows.sort(key=lambda r: r.get("published") or "", reverse=True)
    useful = [r for r in rows if not is_noise(r)]
    return {
        "symbol": symbol,
        "status": "ok",
        "items": useful[:limit],
        "total_found": len(useful),
        "routine_skipped": len(rows) - len(useful),
        "days": days,
        "history_starts": meta.get("history_starts"),
        "last_collected": meta.get("last_success") or meta.get("last_run"),
        "source": source,
    }
