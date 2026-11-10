"""Company profile + price behaviour (Yahoo Finance via yfinance)."""

from __future__ import annotations

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


def get_market_data(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = t.info or {}
    hist = t.history(period="3y", auto_adjust=True)
    close = hist["Close"] if not hist.empty else None

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
        "source": {
            "provider": "Yahoo Finance (yfinance)",
            "url": f"https://finance.yahoo.com/quote/{ticker}",
            "fetched_at": now_iso(),
        },
    }
