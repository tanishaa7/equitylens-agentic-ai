"""News headlines from Google News RSS (India edition). No API key needed."""

from __future__ import annotations

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
