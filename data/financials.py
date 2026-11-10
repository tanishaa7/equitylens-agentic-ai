"""Fetch annual + quarterly financial statements (Yahoo Finance via yfinance).

Returns plain numbers in rupees, newest period first. No ratios here —
see data/ratios.py for the deterministic calculations.
"""

from __future__ import annotations

import warnings

import pandas as pd
import yfinance as yf

from data.common import clean, fy_label, now_iso, quarter_label

warnings.filterwarnings("ignore", category=FutureWarning)

# Our field name -> Yahoo row names to try, in order.
INCOME_FIELDS = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
    "ebit": ["EBIT", "Operating Income"],
    "net_income": ["Net Income Common Stockholders", "Net Income"],
    "interest_expense": ["Interest Expense", "Interest Expense Non Operating"],
    "diluted_eps": ["Diluted EPS"],
}
BALANCE_FIELDS = {
    "total_assets": ["Total Assets"],
    "current_assets": ["Current Assets"],
    "current_liabilities": ["Current Liabilities"],
    "equity": ["Stockholders Equity", "Common Stock Equity"],
    "total_debt": ["Total Debt"],
    "cash_and_investments": ["Cash Cash Equivalents And Short Term Investments",
                             "Cash And Cash Equivalents"],
    "receivables": ["Accounts Receivable", "Receivables"],
    "inventory": ["Inventory"],
}
CASHFLOW_FIELDS = {
    "operating_cash_flow": ["Operating Cash Flow"],
    "capex": ["Capital Expenditure"],
    "dividends_paid": ["Cash Dividends Paid"],
}
ALL_FIELDS = {**INCOME_FIELDS, **BALANCE_FIELDS, **CASHFLOW_FIELDS}


def _extract(df: pd.DataFrame | None, fields: dict) -> dict:
    """{period_end: {field: value}} from one Yahoo statement table."""
    out: dict = {}
    if df is None or df.empty:
        return out
    for col in df.columns:
        row = {}
        for key, names in fields.items():
            row[key] = None
            for name in names:
                if name in df.index:
                    v = clean(df.at[name, col])
                    if v is not None:
                        row[key] = v
                        break
        out[pd.Timestamp(col)] = row
    return out


def _merge(*tables: dict, label_fn) -> list[dict]:
    periods = sorted({p for t in tables for p in t}, reverse=True)
    rows = []
    for p in periods:
        row = {k: None for k in ALL_FIELDS}
        for t in tables:
            row.update({k: v for k, v in t.get(p, {}).items() if v is not None})
        if all(v is None for v in row.values()):
            continue
        rows.append({"period": label_fn(p), "period_end": p.date().isoformat(), **row})
    return rows


def _convert_to_inr(rows: list[dict], currency: str) -> str:
    """Some Indian companies (e.g. Infosys) report to Yahoo in USD.
    Convert every amount at the FX rate on that period's end date."""
    fx = yf.Ticker(f"{currency}INR=X").history(period="10y", auto_adjust=True)
    if fx.empty:
        raise ValueError(f"No {currency}/INR rate available to convert statements")
    close = fx["Close"]
    close.index = close.index.tz_localize(None)
    for row in rows:
        rate = clean(close.asof(pd.Timestamp(row["period_end"])))
        if rate is None:  # period older than FX history
            rate = clean(close.iloc[0])
        for k in ALL_FIELDS:
            if row[k] is not None:
                row[k] *= rate
        row["fx_rate_used"] = rate
    return (f"Reported in {currency}; converted to INR at the {currency}/INR rate "
            f"on each period-end date (Yahoo Finance {currency}INR=X).")


def get_financials(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    reported_currency = (t.info or {}).get("financialCurrency") or "INR"
    annual = _merge(
        _extract(t.income_stmt, INCOME_FIELDS),
        _extract(t.balance_sheet, BALANCE_FIELDS),
        _extract(t.cashflow, CASHFLOW_FIELDS),
        label_fn=fy_label,
    )
    quarterly = _merge(
        _extract(t.quarterly_income_stmt, INCOME_FIELDS),
        _extract(t.quarterly_balance_sheet, BALANCE_FIELDS),
        _extract(t.quarterly_cashflow, CASHFLOW_FIELDS),
        label_fn=quarter_label,
    )
    # Drop quarters with no P&L (balance-sheet-only rows add noise).
    quarterly = [q for q in quarterly if q["revenue"] is not None]

    fx_note = None
    if reported_currency != "INR":
        fx_note = _convert_to_inr(annual + quarterly, reported_currency)

    return {
        "ticker": ticker,
        "currency": "INR",
        "reported_currency": reported_currency,
        "annual": annual,
        "quarterly": quarterly,
        "source": {
            "provider": "Yahoo Finance (yfinance)",
            "url": f"https://finance.yahoo.com/quote/{ticker}/financials",
            "fetched_at": now_iso(),
            "note": fx_note,
        },
    }
