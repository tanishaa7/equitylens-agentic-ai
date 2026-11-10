"""Turn what the user typed ('tcs', 'Infosys', 'M&M', 'kalyan jewellers')
into a listed Indian company on Yahoo Finance."""

from __future__ import annotations

import logging
import re
import time

import yfinance as yf

from data.common import clean, normalize_ticker
from data.store import HOUR, cached_call, get, put
from data.market import clean_company_name

INDIAN_EXCHANGES = {"NSI": "NSE", "BSE": "BSE"}

# A wrong symbol guess (e.g. "INFOSYS.NS") is normal here — don't print Yahoo's 404.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


LOOKUP_TTL = 24 * HOUR      # company names / sectors rarely change
MISS = {"missing": True}    # cached "no such symbol", so typos don't hit Yahoo every time


def lookup_symbol(symbol: str) -> dict | None:
    """Profile for an exact symbol, or None if it isn't an actively traded equity."""
    ticker = normalize_ticker(symbol)
    hit = get("lookup", ticker, LOOKUP_TTL)
    if hit is not None:
        return None if hit == MISS else hit
    profile = _lookup_symbol(ticker)
    put("lookup", ticker, profile or MISS)
    return profile


def _lookup_symbol(ticker: str) -> dict | None:
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return None
    name = info.get("longName") or info.get("shortName")
    price = clean(info.get("currentPrice")) or clean(info.get("regularMarketPrice"))
    if not name or info.get("quoteType") != "EQUITY" or price is None:
        return None
    return {
        "ticker": ticker,
        "name": clean_company_name(name),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "exchange": "BSE" if ticker.endswith(".BO") else "NSE",
        "market_cap": clean(info.get("marketCap")),
    }


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9&]+", (s or "").lower()))


class LookupUnavailable(Exception):
    """Yahoo search couldn't be reached — not the same as 'no such company'."""


def _search_quotes(query: str, limit: int) -> list[dict]:
    last = None
    for attempt in range(2):
        try:
            s = yf.Search(query, max_results=limit, news_count=0)
            if "quotes" in (s.response or {}):    # empty response = Yahoo hiccup, not "no match"
                return s.quotes
            last = "empty response from Yahoo search"
        except Exception as e:
            last = repr(e)
        if attempt == 0:
            time.sleep(1)
    raise LookupUnavailable(last)


def search_company(query: str, limit: int = 8) -> list[dict]:
    """Indian listed equities matching a name. NSE preferred; one entry per company.
    Each candidate has `exact` = every word typed appears as a whole word in its name.
    Raises LookupUnavailable if Yahoo can't be reached (after one retry)."""
    quotes = cached_call("search", f"{query.lower().strip()}|{limit}", LOOKUP_TTL,
                         _search_quotes, query, limit)
    typed = _words(query)
    out, seen_names = [], set()
    for q in sorted(quotes, key=lambda q: q.get("exchange") != "NSI"):   # NSE first
        if (q.get("exchange") not in INDIAN_EXCHANGES or q.get("quoteType") != "EQUITY"
                or not q.get("sector")):
            continue
        name = clean_company_name(q.get("longname") or q.get("shortname") or "")
        root = q["symbol"].split(".")[0]
        name_key = re.sub(r"\b(ltd|limited|ind|india)\b|[^a-z ]", "", name.lower()).replace(" ", "")
        if root in seen_names or name_key in seen_names:
            continue   # same company listed on BSE too
        seen_names.update({root, name_key})
        out.append({"ticker": q["symbol"], "name": name, "sector": q.get("sector"),
                    "industry": q.get("industry"), "exchange": INDIAN_EXCHANGES[q["exchange"]],
                    "exact": typed <= _words(q.get("longname") or q.get("shortname") or "")})
    return out
