"""News headlines from Google News RSS (India edition). No API key needed."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import feedparser
import requests

from data.common import USER_AGENT, now_iso

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


def get_news(query: str, days: int = 365, limit: int = 30, exact: bool = True) -> dict:
    since = (date.today() - timedelta(days=days)).isoformat()
    q = f'"{query}"' if exact else query
    params = {"q": f"{q} after:{since}", "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
    url = f"{GOOGLE_NEWS_RSS}?{urlencode(params)}"

    articles, status = [], "ok"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        seen = set()
        for e in feed.entries:
            publisher = getattr(getattr(e, "source", None), "title", None) or ""
            title = e.title
            if publisher and title.endswith(f" - {publisher}"):
                title = title[: -len(publisher) - 3]
            if title.lower() in seen:
                continue
            seen.add(title.lower())
            published = None
            if getattr(e, "published_parsed", None):
                published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc).isoformat()
            articles.append({"title": title, "publisher": publisher,
                             "published": published, "link": e.link})
    except Exception as ex:  # network / parse problems shouldn't crash the pipeline
        status = f"unavailable: {ex}"

    articles.sort(key=lambda a: a["published"] or "", reverse=True)
    return {
        "query": query,
        "days": days,
        "articles": articles[:limit],
        "total_found": len(articles),
        "status": status,
        "source": {"provider": "Google News RSS (India)", "url": url, "fetched_at": now_iso()},
    }


# ---------------------------------------------------------------- company news (3 feeds)

# Indian business papers. Headlines and links only — article text is never fetched.
PRESS_SITES = ["economictimes.indiatimes.com", "business-standard.com", "livemint.com",
               "moneycontrol.com", "financialexpress.com", "thehindubusinessline.com"]
GOVERNANCE_TERMS = ["CEO", "MD", "chairman", "board", "director", "resigns", "resignation",
                    "RBI", "SEBI", "auditor", "probe", "penalty"]
_GOVERNANCE_RE = re.compile(
    r"\b(ceo|md|managing director|chair(?:man|person)?|board|directors?|resign\w*|steps? down|"
    r"appoint\w*|exit\w*|rbi|sebi|auditors?|probe|penalt\w*|fine[ds]?|raid|fraud|whistle\w*|"
    r"governance|succession|tenure|kmp|ed\b|cfo|coo|lawsuit|court|tribunal|nclt)\b", re.IGNORECASE)

FEED_LABELS = {"general": "Google News", "press": "Indian business press",
               "governance": "Leadership & governance"}


def _mentions(title: str, name: str, symbol: str | None) -> bool:
    """Headline names the company (full name, or its NSE symbol as a word)."""
    t = title.lower()
    if name.lower() in t:
        return True
    return bool(symbol) and re.search(rf"\b{re.escape(symbol.lower())}\b", t) is not None


def get_company_news(name: str, symbol: str | None = None, days: int = 365) -> dict:
    """Three Google News searches merged into one list, each headline tagged with its feed:
      general    — any outlet mentioning the company
      press      — ET, Business Standard, Mint, Moneycontrol, FE, BusinessLine only
      governance — leadership, board, regulator and auditor stories
    Press and governance headlines are kept only if they name the company, and governance
    ones only if the headline itself is about leadership/regulation."""
    sites = " OR ".join(f"site:{s}" for s in PRESS_SITES)
    terms = " OR ".join(GOVERNANCE_TERMS)
    feeds = {
        "general": get_news(name, days=days, limit=30),
        "press": get_news(f'"{name}" ({sites})', days=days, limit=60, exact=False),
        "governance": get_news(f'"{name}" ({terms})', days=days, limit=60, exact=False),
    }
    keep = {
        "general": lambda a: True,
        "press": lambda a: _mentions(a["title"], name, symbol),
        "governance": lambda a: (_mentions(a["title"], name, symbol)
                                 and _GOVERNANCE_RE.search(a["title"]) is not None),
    }
    merged, seen, counts = [], {}, {}
    for feed, res in feeds.items():
        counts[feed] = 0
        for a in res["articles"]:
            if not keep[feed](a):
                continue
            key = a["title"].lower()
            if key in seen:                       # same headline in two feeds: keep both tags
                seen[key]["feeds"].append(feed)
                continue
            item = {**a, "feed": feed, "feeds": [feed]}
            seen[key] = item
            merged.append(item)
            counts[feed] += 1
    merged.sort(key=lambda a: a["published"] or "", reverse=True)

    statuses = {f: r["status"] for f, r in feeds.items()}
    ok = [f for f, st in statuses.items() if st.startswith("ok")]
    return {
        "query": name,
        "days": days,
        "articles": merged,
        "total_found": len(merged),
        "feed_counts": counts,
        "feed_status": statuses,
        "status": "ok" if len(ok) == len(feeds) else (
            f"ok (partial: {', '.join(f for f in feeds if f not in ok)} unavailable)" if ok
            else statuses["general"]),
        "source": {"provider": "Google News RSS (India): all outlets + Indian business press + "
                               "leadership/governance search",
                   "url": feeds["general"]["source"]["url"], "fetched_at": now_iso()},
    }
