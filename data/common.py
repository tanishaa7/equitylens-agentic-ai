"""Small shared helpers for the data layer."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pandas as pd

from config import DEFAULT_EXCHANGE_SUFFIX

USER_AGENT = "Mozilla/5.0 (compatible; EquityLens-prototype/0.1)"
CRORE = 1e7


def normalize_ticker(ticker: str) -> str:
    """'tcs' -> 'TCS.NS'. Leaves 'TCS.BO' and index tickers like '^NSEI' alone."""
    t = ticker.strip().upper()
    if "." in t or t.startswith("^"):
        return t
    return t + DEFAULT_EXCHANGE_SUFFIX


def clean(x) -> float | None:
    """Turn NaN / None / numpy types into a plain float or None."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def safe_div(a, b) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def cagr(end, start, years) -> float | None:
    if end is None or start is None or start <= 0 or end <= 0 or years <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def fy_label(ts: pd.Timestamp) -> str:
    """Indian fiscal year label: period ending 31 Mar 2026 -> 'FY26'."""
    if ts.month == 3:
        return f"FY{str(ts.year)[-2:]}"
    return ts.strftime("%b %Y")


def quarter_label(ts: pd.Timestamp) -> str:
    """Indian fiscal quarter: Sep 2026 -> 'Q2 FY27'."""
    q = {6: 1, 9: 2, 12: 3, 3: 4}.get(ts.month)
    if q is None:
        return ts.strftime("%b %Y")
    fy = ts.year + 1 if ts.month > 3 else ts.year
    return f"Q{q} FY{str(fy)[-2:]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def price_returns(close: pd.Series) -> dict:
    """Total returns over standard windows from a daily close series."""
    close = close.dropna()
    if close.empty:
        return {}
    last_date, last = close.index[-1], close.iloc[-1]
    out = {}
    for label, months in [("1m", 1), ("6m", 6), ("1y", 12), ("3y", 36)]:
        start_date = last_date - pd.DateOffset(months=months)
        if close.index[0] > start_date + pd.Timedelta(days=7):
            out[label] = None  # not enough history
            continue
        start = close.asof(start_date)
        out[label] = clean(last / start - 1)
    return out


def fmt_cr(x) -> str:
    """Format rupees as crores for display: 2.55e12 -> '2,55,000 cr' style (simple)."""
    if x is None:
        return "—"
    return f"₹{x / CRORE:,.0f} cr"


def fmt_pct(x) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"
