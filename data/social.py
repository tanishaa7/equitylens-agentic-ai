"""Reddit discussion from Indian investing subreddits.

Uses the official Reddit API when REDDIT_CLIENT_ID/SECRET are set in .env;
otherwise tries Reddit's public JSON search, which may be rate-limited.
Social data is low-weight context, never a primary signal.
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests

import config
from data.common import USER_AGENT, now_iso

SUBREDDITS = ["IndianStockMarket", "IndiaInvestments", "DalalStreetTalks", "StockMarketIndia"]


def _post(title, subreddit, score, comments, created_utc, permalink, text):
    return {
        "title": title,
        "subreddit": subreddit,
        "score": score,
        "num_comments": comments,
        "created": datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat(),
        "link": f"https://www.reddit.com{permalink}",
        "text": (text or "")[:400],
    }


def _via_api(query: str, limit: int) -> list[dict]:
    import praw

    reddit = praw.Reddit(client_id=config.REDDIT_CLIENT_ID,
                         client_secret=config.REDDIT_CLIENT_SECRET,
                         user_agent=config.REDDIT_USER_AGENT,
                         check_for_async=False)
    sub = reddit.subreddit("+".join(SUBREDDITS))
    return [_post(s.title, str(s.subreddit), s.score, s.num_comments, s.created_utc,
                  s.permalink, s.selftext)
            for s in sub.search(f'"{query}"', sort="relevance", time_filter="year", limit=limit)]


def _via_public_json(query: str, limit: int) -> list[dict]:
    url = f"https://www.reddit.com/r/{'+'.join(SUBREDDITS)}/search.json"
    params = {"q": f'"{query}"', "restrict_sr": 1, "sort": "relevance", "t": "year", "limit": limit}
    resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=15)
    resp.raise_for_status()
    children = resp.json().get("data", {}).get("children", [])
    return [_post(d["title"], d["subreddit"], d["score"], d["num_comments"], d["created_utc"],
                  d["permalink"], d.get("selftext"))
            for d in (c["data"] for c in children)]


def get_reddit_posts(query: str, limit: int = 25) -> dict:
    posts, status = [], "ok"
    try:
        if config.REDDIT_CLIENT_ID and config.REDDIT_CLIENT_SECRET:
            posts, status = _via_api(query, limit), "ok (Reddit API)"
        else:
            posts, status = _via_public_json(query, limit), "ok (public JSON)"
    except Exception as ex:
        status = f"unavailable: {ex}"

    return {
        "query": query,
        "subreddits": SUBREDDITS,
        "posts": posts,
        "status": status,
        "source": {"provider": "Reddit", "fetched_at": now_iso()},
    }
