"""Gather everything the agents need for a set of companies (used by the
try-out scripts and the orchestrator). Every fetch goes through the disk
cache, with an expiry that matches how fast that data changes."""

from __future__ import annotations

from datetime import date

from data.filings import get_filings
from data.financials import get_financials
from data.macro import get_macro_data
from data.market import get_market_data
from data.news import get_company_news, is_commentator
from data.exchange_results import get_exchange_results
from data.ratios import add_exchange_filings, add_exchange_results, compute_ratios
from data.social import get_reddit_posts
from data.store import HOUR, MIN, cached_call

ALL_PARTS = frozenset({"financials", "news", "social"})

TTL = {
    "market": 30 * MIN,      # prices move all day
    "financials": 12 * HOUR,  # statements change once a quarter
    "financials_results_season": 2 * HOUR,   # new quarterly results land every day
    "news": 3 * HOUR,
    "filings": 3 * HOUR,
    "exchange_results": 1 * HOUR,     # cleared as soon as someone uploads a file
    "social": 3 * HOUR,
    "macro": 3 * HOUR,
}


def in_results_season(today: date | None = None) -> bool:
    """10th of the month after a quarter end through the 60-day filing deadline:
    mid-Jan–Feb, mid-Apr–May, mid-Jul–Aug, mid-Oct–Nov."""
    today = today or date.today()
    months_after = today.month % 3          # Jan/Apr/Jul/Oct → 1, Feb/May/Aug/Nov → 2, quarter-end months → 0
    return months_after == 2 or (months_after == 1 and today.day >= 10)


def financials_ttl(today: date | None = None) -> int:
    return TTL["financials_results_season"] if in_results_season(today) else TTL["financials"]


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
        fin = cached_call("financials", ticker, financials_ttl(), get_financials, ticker,
                          ok=lambda v: bool(v["annual"]))
        out["financials"] = fin
        out["ratios"] = compute_ratios(fin, market)   # cheap, and depends on today's price
        symbol = ticker.split(".")[0]
        out["exchange_results"] = cached_call("exchange_results", symbol, TTL["exchange_results"],
                                              get_exchange_results, symbol, ok=_ok_status)
        add_exchange_results(out["ratios"], out["exchange_results"])
    if "news" in parts:
        symbol = ticker.split(".")[0]
        commentator = is_commentator(market.get("industry"), market["name"])
        out["news"] = cached_call("news", f"v3|{market['name']}|{symbol}|{commentator}", TTL["news"],
                                  get_company_news, market["name"], symbol,
                                  commentator=commentator, ok=_ok_status)
        out["filings"] = cached_call("filings", f"{symbol}|{market['name']}", TTL["filings"],
                                     get_filings, symbol, market["name"], ok=_ok_status)
        if "ratios" in out:
            add_exchange_filings(out["ratios"], out["filings"])
    if "social" in parts:
        out["social"] = cached_call("social", market["name"], TTL["social"], get_reddit_posts,
                                    market["name"], ok=_ok_status)
    return out


def macro_for(sector: str | None, industry: str | None) -> dict:
    return cached_call("macro", f"v2|{sector}|{industry}", TTL["macro"], get_macro_data,
                       sector, industry, ok=lambda v: bool(v["benchmark"]["returns"]))


def collect_macro(companies: list[dict]) -> dict:
    m = companies[0]["market"]
    return macro_for(m["sector"], m["industry"])
