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


def quarter_label(ts: pd.Timestamp, fy_end_month: int = 3) -> str:
    """Quarter of the company's own financial year.

    March year-end (most Indian companies): Sep 2026 -> 'Q2 FY27'.
    December year-end (e.g. CRISIL):        Sep 2026 -> 'Q3 CY26'.
    Any other year-end:                      'Q2 FY27 (Jun year-end)'.
    """
    offset = (ts.month - fy_end_month) % 12          # months after the year-end
    if offset % 3:
        return ts.strftime("%b %Y")                  # not a quarter end
    q = 4 if offset == 0 else offset // 3
    fy = ts.year if ts.month <= fy_end_month else ts.year + 1
    yy = str(fy)[-2:]
    if fy_end_month == 3:
        return f"Q{q} FY{yy}"
    if fy_end_month == 12:
        return f"Q{q} CY{yy}"
    return f"Q{q} FY{yy} ({pd.Timestamp(2000, fy_end_month, 1).strftime('%b')} year-end)"


def quarter_ends_between(newest: pd.Timestamp, oldest: pd.Timestamp) -> list[pd.Timestamp]:
    """Every calendar quarter-end from newest back to oldest (inclusive)."""
    out, ts = [], newest
    while ts >= oldest:
        out.append(ts)
        ts = (ts - pd.DateOffset(months=3)) + pd.offsets.MonthEnd(0)
    return out


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
