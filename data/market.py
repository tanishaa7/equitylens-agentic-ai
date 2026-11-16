"""Company profile + price behaviour (Yahoo Finance via yfinance)."""

from __future__ import annotations

import time

import pandas as pd
import yfinance as yf

from data.common import clean, now_iso, price_returns


def clean_company_name(name: str | None) -> str | None:
    """'Tata Consultancy Services Limited' -> 'Tata Consultancy Services'."""
    if not name:
        return name
    for suffix in [" Limited", " Ltd.", " Ltd", " LIMITED", " LTD"]:
        if name.endswith(suffix):
            return name[: -len(suffix)].strip()
    return name.strip()


YAHOO_RETRY_WAITS = (2, 5)    # seconds; shared cloud servers get short rate limits from Yahoo


def yahoo_info(t: "yf.Ticker") -> dict:
    """Ticker.info with a couple of short retries. Raises the last error if Yahoo keeps
    refusing; returns Yahoo's (possibly near-empty) answer otherwise."""
    for wait in (*YAHOO_RETRY_WAITS, None):
        try:
            return t.info or {}
        except Exception:
            if wait is None:
                raise
            time.sleep(wait)
    return {}


def _fast_info(t: "yf.Ticker") -> dict:
    """Price, market cap and 52-week range from Yahoo's chart endpoint, which keeps working
    when the company-details endpoint refuses cloud servers."""
    try:
        f = t.fast_info
        return {"currentPrice": f.get("lastPrice"), "marketCap": f.get("marketCap"),
                "fiftyTwoWeekHigh": f.get("yearHigh"), "fiftyTwoWeekLow": f.get("yearLow"),
                "currency": f.get("currency")}
    except Exception:
        return {}


def get_market_data(ticker: str, profile: dict | None = None) -> dict:
    """`profile` (name, sector, industry from the company lookup) is used when Yahoo's
    company-details call fails, as it often does from shared cloud servers."""
    t = yf.Ticker(ticker)
    try:
        info = yahoo_info(t)
    except Exception:
        info = {}
    hist = t.history(period="3y", auto_adjust=True)
    close = hist["Close"] if not hist.empty else None
    details_from = "yahoo"
    if not (info.get("longName") or info.get("shortName")) and profile and profile.get("name") \
            and close is not None:
        info = {**_fast_info(t), "longName": profile["name"], "sector": profile.get("sector"),
                "industry": profile.get("industry")}
        details_from = "lookup"

    volatility_1y = max_drawdown_1y = None
    if close is not None and len(close) > 30:
        last_year = close[close.index >= close.index[-1] - pd.DateOffset(years=1)]
        daily = last_year.pct_change().dropna()
        volatility_1y = clean(daily.std() * (252 ** 0.5))
        running_max = last_year.cummax()
        max_drawdown_1y = clean((last_year / running_max - 1).min())

    price = clean(info.get("currentPrice")) or (clean(close.iloc[-1]) if close is not None else None)
    high_52w = clean(info.get("fiftyTwoWeekHigh"))

    return {
        "ticker": ticker,
        "found": bool(info.get("longName") or info.get("shortName")),
        "name": clean_company_name(info.get("longName") or info.get("shortName")),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "currency": info.get("currency"),
        "price": price,
        "market_cap": clean(info.get("marketCap")),
        "high_52w": high_52w,
        "low_52w": clean(info.get("fiftyTwoWeekLow")),
        "below_52w_high": (price / high_52w - 1) if price and high_52w else None,
        "returns": price_returns(close) if close is not None else {},
        "volatility_1y": volatility_1y,
        "max_drawdown_1y": max_drawdown_1y,
        "yahoo_trailing_pe": clean(info.get("trailingPE")),  # cross-checks only
        "yahoo_roe": clean(info.get("returnOnEquity")),
        "details_from": details_from,    # "lookup": Yahoo's details call failed; prices still Yahoo
        "source": {
            "provider": "Yahoo Finance (yfinance)",
            "url": f"https://finance.yahoo.com/quote/{ticker}",
            "fetched_at": now_iso(),
        },
    }
