"""Gather everything the agents need for a set of companies (used by the
try-out scripts and the orchestrator). Every fetch goes through the disk
cache, with an expiry that matches how fast that data changes."""

from __future__ import annotations

from data.financials import get_financials
from data.macro import get_macro_data
from data.market import get_market_data
from data.news import get_news
from data.ratios import compute_ratios
from data.social import get_reddit_posts
from data.store import HOUR, MIN, cached_call

ALL_PARTS = frozenset({"financials", "news", "social"})

TTL = {
    "market": 30 * MIN,      # prices move all day
    "financials": 12 * HOUR,  # statements change once a quarter
    "news": 3 * HOUR,
    "social": 3 * HOUR,
    "macro": 3 * HOUR,
}


def _ok_status(v: dict) -> bool:
    return str(v.get("status", "")).startswith("ok")


def collect_company(ticker: str, parts=ALL_PARTS) -> dict:
    """Market data always; financials/ratios, news and Reddit only if asked for."""
    market = cached_call("market", ticker, TTL["market"], get_market_data, ticker,
                         ok=lambda v: v["found"])
    if not market["found"]:
        return {"ticker": ticker, "error": "Ticker not found on Yahoo Finance"}
    out = {"ticker": ticker, "market": market}
    if "financials" in parts:
        fin = cached_call("financials", ticker, TTL["financials"], get_financials, ticker,
                          ok=lambda v: bool(v["annual"]))
        out["financials"] = fin
        out["ratios"] = compute_ratios(fin, market)   # cheap, and depends on today's price
    if "news" in parts:
        out["news"] = cached_call("news", market["name"], TTL["news"], get_news, market["name"],
                                  ok=_ok_status)
    if "social" in parts:
        out["social"] = cached_call("social", market["name"], TTL["social"], get_reddit_posts,
                                    market["name"], ok=_ok_status)
    return out


def macro_for(sector: str | None, industry: str | None) -> dict:
    return cached_call("macro", f"{sector}|{industry}", TTL["macro"], get_macro_data,
                       sector, industry, ok=lambda v: bool(v["benchmark"]["returns"]))


def collect_macro(companies: list[dict]) -> dict:
    m = companies[0]["market"]
    return macro_for(m["sector"], m["industry"])
