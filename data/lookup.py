"""Turn what the user typed ('tcs', 'Infosys', 'M&M', 'kalyan jewellers')
into a listed Indian company on Yahoo Finance."""

from __future__ import annotations

import logging
import re
import time
import unicodedata

import yfinance as yf

from data.common import clean, normalize_ticker
from data.store import HOUR, cached_call, get, put
from data.market import clean_company_name, yahoo_info

INDIAN_EXCHANGES = {"NSI": "NSE", "BSE": "BSE"}

# A wrong symbol guess (e.g. "INFOSYS.NS") is normal here — don't print Yahoo's 404.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


LOOKUP_TTL = 24 * HOUR      # company names / sectors rarely change
UNSURE_TTL = 1 * HOUR       # Yahoo answered with almost nothing: probably a typo, maybe a hiccup
MISS = {"missing": True}    # cached "no such symbol", so typos don't hit Yahoo every time
_NS, _NS_UNSURE = "lookup2", "lookup2_unsure"   # v2: earlier versions cached Yahoo errors as misses
log = logging.getLogger("equitylens.lookup")


class _Unsure(Exception):
    """Yahoo gave no usable answer (error or near-empty reply) — not proof the symbol is wrong."""


def lookup_symbol(symbol: str) -> dict | None:
    """Profile for an exact symbol, or None if it isn't an actively traded equity (or Yahoo
    couldn't say just now). Only definite answers are remembered for a day; Yahoo errors
    (rate limits on shared servers) are never remembered, so they can't stick."""
    ticker = normalize_ticker(symbol)
    for ns, ttl in ((_NS, LOOKUP_TTL), (_NS_UNSURE, UNSURE_TTL)):
        hit = get(ns, ticker, ttl)
        if hit is not None:
            return None if hit == MISS else hit
    try:
        profile = _lookup_symbol(ticker)
    except _Unsure as ex:
        if str(ex) == "empty":
            put(_NS_UNSURE, ticker, MISS)
        else:
            log.warning("Yahoo lookup for %s failed: %s", ticker, ex)
        return None
    put(_NS, ticker, profile or MISS)
    return profile


def _lookup_symbol(ticker: str) -> dict | None:
    try:
        info = yahoo_info(yf.Ticker(ticker))
    except Exception as ex:                       # rate limit, timeout, network (after retries)
        raise _Unsure(f"{type(ex).__name__}: {ex}"[:200])
    if not info.get("quoteType"):                 # unknown symbols come back like this
        raise _Unsure("empty")
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


# Short names people type that Yahoo's search doesn't map to the main company
# ("SBI" finds SBI Life first; "HUL" and "L&T" find nothing Indian at all).
ALIASES = {
    "SBI": "SBIN", "HUL": "HINDUNILVR", "L&T": "LT", "L & T": "LT",
    "RIL": "RELIANCE", "KOTAK BANK": "KOTAKBANK",
}
_FILLER = {"ltd", "limited", "the"}


def alias_for(text: str) -> str | None:
    """NSE symbol for a well-known short name ('SBI' -> 'SBIN'), else None."""
    return ALIASES.get(re.sub(r"\s+", " ", (text or "").strip().upper()))


def _words(s: str) -> set[str]:
    """Comparable words: accents, apostrophes and dots dropped ('Nestlé' -> 'nestle',
    "Dr. Reddy's" -> 'dr', 'reddys'), 'and' read as '&', 'Ltd'/'Limited'/'The' ignored."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"['\u2019.]", "", s)
    words = {"&" if w == "and" else w for w in re.findall(r"[a-z0-9&]+", s)}
    return words - _FILLER


def _word_hits(typed: str, word: str) -> bool:
    """A typed word matches a name word it equals or starts ('pharma' -> 'pharmaceutical')."""
    return typed == word or (len(typed) >= 3 and word.startswith(typed))


def _match(typed: set[str], name: str) -> tuple[bool, int]:
    """(every typed word found in the name, how many name words the user didn't type)."""
    words = _words(name)
    exact = bool(typed) and all(any(_word_hits(t, w) for w in words) for t in typed)
    extra = sum(not any(_word_hits(t, w) for t in typed) for w in words)
    return exact, extra


def _query_variants(query: str) -> list[str]:
    """The query, then retries Yahoo needs for names it only knows spelled one way:
    'and' as '&' ('Larsen and Toubro'), and a possessive typed without its apostrophe
    (nothing for 'Dr Reddys', but 'Dr Reddy' works)."""
    words = query.split()
    out = [query]
    if any(w.lower() == "and" for w in words):
        out.append(" ".join("&" if w.lower() == "and" else w for w in words))
    for i, w in enumerate(words):
        if len(w) >= 4 and w.lower().endswith("s") and not w.lower().endswith("ss"):
            out.append(" ".join(words[:i] + [w[:-1]] + words[i + 1:]))
    return out[:3]


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
    Each candidate has `exact` = every word typed appears in its name (ignoring accents,
    apostrophes and dots; a typed word may be the start of a name word, 'pharma' ->
    'Pharmaceutical'), and `extra` = how many name words weren't typed.
    Raises LookupUnavailable if Yahoo can't be reached (after one retry)."""
    typed = _words(query)
    for q in _query_variants(query):
        quotes = cached_call("search", f"{q.lower().strip()}|{limit}", LOOKUP_TTL,
                             _search_quotes, q, limit)
        out = _indian_equities(quotes, typed)
        if out:
            return out
    return []


def _indian_equities(quotes: list[dict], typed: set[str]) -> list[dict]:
    out, seen_names = [], set()
    for q in sorted(quotes, key=lambda q: q.get("exchange") != "NSI"):   # NSE first
        if (q.get("exchange") not in INDIAN_EXCHANGES or q.get("quoteType") != "EQUITY"
                or not q.get("sector")):
            continue
        full_name = q.get("longname") or q.get("shortname") or ""
        name = clean_company_name(full_name)
        root = q["symbol"].split(".")[0]
        name_key = re.sub(r"\b(ltd|limited|ind|india)\b|[^a-z ]", "", name.lower()).replace(" ", "")
        if root in seen_names or name_key in seen_names:
            continue   # same company listed on BSE too
        seen_names.update({root, name_key})
        exact, extra = _match(typed, full_name)
        out.append({"ticker": q["symbol"], "name": name, "sector": q.get("sector"),
                    "industry": q.get("industry"), "exchange": INDIAN_EXCHANGES[q["exchange"]],
                    "exact": exact, "extra": extra})
    return out
