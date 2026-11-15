"""Collect NSE's official public RSS feeds into a per-company filing history.

NSE's feeds only hold the latest few hundred items (about a day of
announcements), so this runs every few hours (GitHub Actions:
.github/workflows/collect-filings.yml) and appends anything new. Over time
that builds up the per-company history the app reads.

    python scripts/collect_nse_filings.py --out filings_local

Writes, under <out>/nse/:
    by_symbol/<SYMBOL>.jsonl   one filing per line, newest last, last 400 days kept
    meta.json                  when collection started, last run, counts

Only needs `requests` and `feedparser`, so the Action installs just those.
One request per feed per run — far lighter than a person reading the site.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests

FEEDS = {
    "announcement": "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml",
    "board_meeting": "https://nsearchives.nseindia.com/content/RSS/Board_Meetings.xml",
    "corporate_action": "https://nsearchives.nseindia.com/content/RSS/Corporate_action.xml",
}
USER_AGENT = "EquityLens research prototype (feed reader)"
IST = timezone(timedelta(hours=5, minutes=30))
KEEP_DAYS = 400

# Attachments are named SYMBOL_ddmmyyyyHHMMSS_… ; a few use SYMBOL_<word>_…
_SYMBOL_TS = re.compile(r"^([A-Z0-9&\-]+)_\d{14}_")
_SYMBOL_ANY = re.compile(r"^([A-Z0-9&\-]+)_")


def symbol_from_link(link: str) -> str | None:
    path = urlparse(link or "").path
    if "/corporate/" not in path or "/xbrl/" in path:
        return None
    name = path.rsplit("/", 1)[-1]
    m = _SYMBOL_TS.match(name) or _SYMBOL_ANY.match(name)
    return m.group(1) if m else None


def _field(description: str, key: str) -> str | None:
    for part in (description or "").split("|"):
        if part.strip().upper().startswith(key.upper() + ":"):
            return part.split(":", 1)[1].strip() or None
    return None


def parse_time(raw: str) -> str | None:
    """'15-Nov-2026 20:48:06' (IST) -> ISO with +05:30."""
    raw = (raw or "").strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=IST).isoformat()
        except ValueError:
            continue
    return None


_FUND = re.compile(r"\b(ETF|MUTUAL FUND|MF|FMP|FTP|IDCW|EXCHANGE TRADED FUND)\b", re.IGNORECASE)


def parse_feed(kind: str, content: bytes) -> list[dict]:
    items = []
    for e in feedparser.parse(content).entries:
        company = re.sub(r"\s+", " ", e.get("title", "")).strip()
        company = re.sub(r"\s+-\s+Ex-Date:.*$", "", company)       # corporate actions
        if _FUND.search(company):
            continue                                                # mutual funds / ETFs
        desc = re.sub(r"\s+", " ", e.get("description", "") or e.get("summary", "")).strip()
        link = (e.get("link") or "").strip()
        subject = (_field(desc, "SUBJECT") or _field(desc, "PURPOSE")
                   or desc.split("|")[0].strip())
        if kind == "announcement" and "|SUBJECT:" in desc:
            desc = desc.split("|SUBJECT:")[0].strip()
        items.append({
            "kind": kind,
            "symbol": symbol_from_link(link),
            "company": company,
            "subject": subject[:200],
            "detail": desc[:400],
            "meeting_date": _field(desc, "Meeting Date"),
            "published": parse_time(e.get("published", "")),
            "link": link,
        })
    return items


def _key(item: dict) -> str:
    return "|".join(str(item.get(k)) for k in ("kind", "link", "published", "subject"))


def name_slug(company: str) -> str:
    """File name for filings we couldn't tie to a symbol (matched by company name).
    'Zensar Technologies Limited' and 'Zensar Technologies' -> '_name_ZENSAR_TECHNOLOGIES'.
    Keep in step with data/filings.py."""
    s = re.sub(r"[^A-Z0-9]+", "_", company.upper()).strip("_")
    s = re.sub(r"_(LIMITED|LTD)$", "", s)
    return f"_name_{s[:80]}" if s else "_unknown"


def store(items: list[dict], out: Path) -> dict:
    base = out / "nse" / "by_symbol"
    base.mkdir(parents=True, exist_ok=True)
    cutoff = (datetime.now(IST) - timedelta(days=KEEP_DAYS)).isoformat()
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(it["symbol"] or name_slug(it["company"]), []).append(it)

    added = 0
    for name, new in groups.items():
        f = base / f"{name}.jsonl"
        old = []
        if f.exists():
            old = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
        seen = {_key(o) for o in old}
        fresh = [n for n in new if _key(n) not in seen]
        if not fresh:
            continue
        added += len(fresh)
        rows = [r for r in old + fresh if (r.get("published") or "9999") >= cutoff]
        rows.sort(key=lambda r: r.get("published") or "")
        tmp = f.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        tmp.replace(f)
    return {"added": added, "companies": len(groups)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="filings_local", help="folder to write into")
    args = ap.parse_args()
    out = Path(args.out)

    items, errors = [], {}
    for kind, url in FEEDS.items():
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            r.raise_for_status()
            items += parse_feed(kind, r.content)
        except Exception as ex:   # one feed failing shouldn't lose the others
            errors[kind] = f"{type(ex).__name__}: {ex}"[:200]
        time.sleep(2)

    result = store(items, out)
    meta_path = out / "nse" / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    now = datetime.now(IST).isoformat(timespec="seconds")
    meta.setdefault("history_starts", now)
    meta.update({"last_run": now, "last_run_items": len(items), "last_run_added": result["added"],
                 "last_run_errors": errors, "source": "NSE official RSS feeds",
                 "feeds": FEEDS})
    if not errors:
        meta["last_success"] = now
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"{len(items)} items read, {result['added']} new, "
          f"{result['companies']} companies; errors: {errors or 'none'}")
    return 1 if len(errors) == len(FEEDS) else 0


if __name__ == "__main__":
    sys.exit(main())
