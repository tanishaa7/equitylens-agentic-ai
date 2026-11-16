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
from datetime import date, datetime, timedelta, timezone
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


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_M = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DATE_PATTERNS = [
    re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[\s\-]+{_M}[\s,\-]+(\d{{4}})\b", re.I),   # 30th September, 2026
    re.compile(rf"\b{_M}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I),          # September 30, 2026
    re.compile(r"\b(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})\b"),                             # 30.09.2026
    re.compile(rf"\bend(?:ed|ing)\s+(?:on\s+)?{_M},?\s+(\d{{4}})\b", re.I),                  # ended September 2026
]
# A filing that says results were *announced* (not a notice of a future meeting).
_RESULTS = re.compile(r"financial results?|\bresults? for the (quarter|period|half)", re.I)
_NOT_YET = re.compile(r"non[\s-]?submission|delay|to consider|will be held|is scheduled|"
                      r"intimation of (the )?board meeting|prior intimation|trading window", re.I)


def _quarter_ends_in(text: str) -> list[date]:
    """Quarter-end dates written in a filing ('30th September, 2026', '30.09.2026' …)."""
    found = []
    for i, pat in enumerate(_DATE_PATTERNS):
        for m in pat.finditer(text):
            try:
                if i == 0:
                    d, mon, y = int(m.group(1)), _MONTHS[m.group(2)[:3].lower()], int(m.group(3))
                elif i == 1:
                    mon, d, y = _MONTHS[m.group(1)[:3].lower()], int(m.group(2)), int(m.group(3))
                elif i == 2:
                    d, mon, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                else:                                          # month only: its last day
                    mon, y = _MONTHS[m.group(1)[:3].lower()], int(m.group(2))
                    d = (date(y + mon // 12, mon % 12 + 1, 1) - timedelta(days=1)).day
                dt = date(y, mon, d)
            except (ValueError, KeyError):
                continue
            if mon in (3, 6, 9, 12) and (dt + timedelta(days=1)).day == 1:
                found.append(dt)
    return found


def results_filed(rows: list[dict]) -> list[dict]:
    """Quarters the company has announced results for, according to its NSE filings —
    one entry per quarter (earliest filing), newest quarter first. Uses all filings,
    routine ones included: a newspaper ad of the results proves they're out."""
    by_quarter: dict[date, dict] = {}
    for r in rows:
        if r.get("kind") == "board_meeting":
            continue                      # notices of meetings still to come
        text = f"{r.get('subject', '')} {r.get('detail', '')}"
        if not _RESULTS.search(text) or _NOT_YET.search(text):
            continue
        for q_end in set(_quarter_ends_in(text)):
            prev = by_quarter.get(q_end)
            if prev is None or (r.get("published") or "") < prev["filed"]:
                by_quarter[q_end] = {"quarter_end": q_end.isoformat(),
                                     "filed": r.get("published") or "",
                                     "link": r.get("link"), "subject": r.get("subject")}
    return [by_quarter[k] for k in sorted(by_quarter, reverse=True)]


def get_filings(symbol: str, name: str | None = None, days: int = 90, limit: int = 25) -> dict:
    symbol = symbol.upper().split(".")[0]
    source = {"provider": "NSE corporate filings (official RSS, collected by EquityLens)",
              "url": f"https://www.nseindia.com/get-quotes/equity?symbol={quote(symbol)}",
              "fetched_at": now_iso()}
    try:
        meta_text = _read("meta.json")
        if meta_text is None:
            return {"symbol": symbol, "status": "unavailable: filing collection not running yet",
                    "items": [], "results_filed": [], "history_starts": None, "source": source}
        meta = json.loads(meta_text)
        text = _read(f"by_symbol/{symbol}.jsonl") or ""
        if name:
            text += "\n" + (_read(f"by_symbol/{name_slug(name)}.jsonl") or "")
    except Exception as ex:   # network / parse problems shouldn't crash the pipeline
        return {"symbol": symbol, "status": f"unavailable: {ex}"[:120], "items": [],
                "results_filed": [], "history_starts": None, "source": source}

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
        "results_filed": results_filed(rows),
        "days": days,
        "history_starts": meta.get("history_starts"),
        "last_collected": meta.get("last_success") or meta.get("last_run"),
        "source": source,
    }
