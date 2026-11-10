"""Sector + macro context: sector index vs Nifty 50, USD/INR, crude, policy headlines."""

from __future__ import annotations

import yfinance as yf

from data.common import clean, now_iso, price_returns
from data.news import get_news

BENCHMARK = ("^NSEI", "Nifty 50")

# Checked by industry keyword first (more precise), then by Yahoo sector.
INDUSTRY_INDEX = [
    (("bank",), ("^NSEBANK", "Nifty Bank")),
    (("auto",), ("^CNXAUTO", "Nifty Auto")),
    (("drug", "pharma", "biotech"), ("^CNXPHARMA", "Nifty Pharma")),
    (("steel", "metal", "aluminum", "copper", "mining"), ("^CNXMETAL", "Nifty Metal")),
    (("real estate",), ("^CNXREALTY", "Nifty Realty")),
    (("oil", "gas", "utilities", "power"), ("^CNXENERGY", "Nifty Energy")),
]
SECTOR_INDEX = {
    "Technology": ("^CNXIT", "Nifty IT"),
    "Financial Services": ("^NSEBANK", "Nifty Bank"),
    "Consumer Defensive": ("^CNXFMCG", "Nifty FMCG"),
    "Healthcare": ("^CNXPHARMA", "Nifty Pharma"),
    "Consumer Cyclical": ("^CNXCONSUM", "Nifty India Consumption"),
    "Basic Materials": ("^CNXMETAL", "Nifty Metal"),
    "Energy": ("^CNXENERGY", "Nifty Energy"),
    "Utilities": ("^CNXENERGY", "Nifty Energy"),
    "Real Estate": ("^CNXREALTY", "Nifty Realty"),
    "Industrials": ("^CNXINFRA", "Nifty Infrastructure"),
    "Communication Services": ("^CNXINFRA", "Nifty Infrastructure"),
}
MACRO_SERIES = {"usd_inr": ("INR=X", "USD/INR"), "brent_usd": ("BZ=F", "Brent crude (USD/bbl)")}


def pick_sector_index(sector: str | None, industry: str | None):
    ind = (industry or "").lower()
    for keywords, index in INDUSTRY_INDEX:
        if any(k in ind for k in keywords):
            return index
    return SECTOR_INDEX.get(sector or "")


def _series(ticker: str, period: str = "3y"):
    h = yf.Ticker(ticker).history(period=period, auto_adjust=True)
    return None if h.empty else h["Close"]


def get_macro_data(sector: str | None, industry: str | None = None) -> dict:
    index = pick_sector_index(sector, industry)

    bench_close = _series(BENCHMARK[0])
    bench = {"ticker": BENCHMARK[0], "name": BENCHMARK[1],
             "returns": price_returns(bench_close) if bench_close is not None else {}}

    sector_index = None
    if index:
        close = _series(index[0])
        r = price_returns(close) if close is not None else {}
        sector_index = {
            "ticker": index[0], "name": index[1], "returns": r,
            "vs_nifty_1y_pts": (None if r.get("1y") is None or bench["returns"].get("1y") is None
                                else (r["1y"] - bench["returns"]["1y"]) * 100),
        }

    series = {}
    for key, (ticker, name) in MACRO_SERIES.items():
        close = _series(ticker, "1y")
        series[key] = {"name": name, "ticker": ticker,
                       "latest": clean(close.iloc[-1]) if close is not None else None,
                       "change_1y": (clean(close.iloc[-1] / close.iloc[0] - 1)
                                     if close is not None else None)}

    # Queries tuned by trial: "<index> sector outlook" and "RBI repo rate policy" return
    # sector/rate analysis rather than exam-prep pages and unrelated "technology" news.
    sector_q = f"{index[1]} sector outlook" if index else f"{industry or sector} India sector outlook"
    sector_news = get_news(sector_q, days=90, limit=10, exact=False)
    policy_news = get_news("RBI repo rate policy", days=180, limit=8, exact=False)

    return {
        "sector": sector,
        "industry": industry,
        "benchmark": bench,
        "sector_index": sector_index,
        "macro_series": series,
        "sector_headlines": sector_news["articles"],
        "policy_headlines": policy_news["articles"],
        "source": {"provider": "Yahoo Finance (indices, FX, crude) + Google News RSS",
                   "fetched_at": now_iso()},
    }
