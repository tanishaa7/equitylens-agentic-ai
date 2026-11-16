"""Deterministic ratio calculations — the calculator does the maths, not the AI.

Input: output of get_financials() and get_market_data().
Every ratio has its formula listed in FORMULAS so the report can cite it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from data.common import cagr, quarter_ends_between, quarter_label, safe_div

FORMULAS = {
    "ebitda_margin": "EBITDA / Revenue",
    "operating_margin": "EBIT / Revenue",
    "net_margin": "Net income / Revenue",
    "roe": "Net income / average shareholders' equity",
    "roce": "EBIT / average capital employed (Total assets − Current liabilities)",
    "debt_to_equity": "Total debt (incl. leases) / Shareholders' equity",
    "interest_coverage": "EBIT / Interest expense",
    "free_cash_flow": "Operating cash flow + Capital expenditure (capex is negative)",
    "cash_conversion": "Operating cash flow / Net income",
    "receivables": "Trade receivables on the year-end balance sheet",
    "receivable_days": "Receivables / Revenue × 365",
    "revenue_growth": "Revenue this year / last year − 1",
    "profit_growth": "Net income this year / last year − 1",
    "revenue_cagr_3y": "(Revenue now / Revenue 3 years ago)^(1/3) − 1",
    "profit_cagr_3y": "(Net income now / Net income 3 years ago)^(1/3) − 1",
    "pe": "Market cap / trailing-12-month net income",
    "pb": "Market cap / latest shareholders' equity",
    "ev_ebitda": "(Market cap + Total debt − Cash & investments) / latest annual EBITDA",
}


def _avg(a, b):
    if a is None:
        return None
    return a if b is None else (a + b) / 2


def _capital_employed(row):
    if row is None or row["total_assets"] is None or row["current_liabilities"] is None:
        return None
    return row["total_assets"] - row["current_liabilities"]


def _year_ratios(cur: dict, prev: dict | None) -> dict:
    rev, ni, ebit = cur["revenue"], cur["net_income"], cur["ebit"]
    ocf, capex = cur["operating_cash_flow"], cur["capex"]
    fcf = None if ocf is None else ocf + (capex or 0)
    return {
        "period": cur["period"],
        "revenue": rev,
        "net_income": ni,
        "ebitda_margin": safe_div(cur["ebitda"], rev),
        "operating_margin": safe_div(ebit, rev),
        "net_margin": safe_div(ni, rev),
        "roe": safe_div(ni, _avg(cur["equity"], prev and prev["equity"])),
        "roce": safe_div(ebit, _avg(_capital_employed(cur), _capital_employed(prev))),
        "debt_to_equity": safe_div(cur["total_debt"], cur["equity"]),
        "interest_coverage": safe_div(ebit, cur["interest_expense"]),
        "free_cash_flow": fcf,
        "cash_conversion": safe_div(ocf, ni),
        "receivables": cur["receivables"],
        "receivable_days": (None if safe_div(cur["receivables"], rev) is None
                            else cur["receivables"] / rev * 365),
        "revenue_growth": (safe_div(rev, prev["revenue"]) - 1
                           if prev and safe_div(rev, prev["revenue"]) is not None else None),
        "profit_growth": (safe_div(ni, prev["net_income"]) - 1
                          if prev and prev["net_income"] and prev["net_income"] > 0
                          and ni is not None else None),
    }


def _ttm_net_income(quarterly: list[dict]) -> tuple[float | None, str]:
    """Sum the last 4 quarters only if they are consecutive."""
    q = [r for r in quarterly if r["net_income"] is not None][:4]
    if len(q) < 4:
        return None, ""
    ends = [date.fromisoformat(r["period_end"]) for r in q]
    gaps = [(ends[i] - ends[i + 1]).days for i in range(3)]
    if not all(80 <= g <= 100 for g in gaps):
        return None, ""
    return sum(r["net_income"] for r in q), f"TTM {q[-1]['period']}–{q[0]['period']}"


def _change(new, old):
    if new is None or old is None or old == 0:
        return None
    return new / abs(old) - 1 if old < 0 else new / old - 1


def _latest_quarter(quarterly: list[dict]) -> dict | None:
    """What changed: latest quarter vs previous quarter and vs same quarter last year."""
    if not quarterly:
        return None
    cur = quarterly[0]
    cur_end = date.fromisoformat(cur["period_end"])
    prev_q = quarterly[1] if len(quarterly) > 1 else None
    yoy = next((r for r in quarterly[1:]
                if 350 <= (cur_end - date.fromisoformat(r["period_end"])).days <= 380), None)

    def snap(r):
        return None if r is None else {
            "period": r["period"],
            "revenue": r["revenue"],
            "net_income": r["net_income"],
            "ebitda_margin": safe_div(r["ebitda"], r["revenue"]),
        }

    c, p, y = snap(cur), snap(prev_q), snap(yoy)
    return {
        "current": c,
        "previous_quarter": p,
        "same_quarter_last_year": y,
        "revenue_qoq": _change(c["revenue"], p and p["revenue"]),
        "revenue_yoy": _change(c["revenue"], y and y["revenue"]),
        "net_income_qoq": _change(c["net_income"], p and p["net_income"]),
        "net_income_yoy": _change(c["net_income"], y and y["net_income"]),
        "ebitda_margin_change_qoq_pts": (
            None if p is None or c["ebitda_margin"] is None or p["ebitda_margin"] is None
            else (c["ebitda_margin"] - p["ebitda_margin"]) * 100),
    }


# Ratios that mean nothing for banks/NBFCs/insurers (deposits are not "debt",
# cash flow is driven by lending, there is no EBITDA).
NOT_FOR_FINANCIALS = ["ebitda_margin", "roce", "debt_to_equity", "interest_coverage",
                      "free_cash_flow", "cash_conversion", "receivables", "receivable_days",
                      "ev_ebitda"]
FINANCIAL_KEYWORDS = ("bank", "insurance", "credit services", "capital markets",
                      "asset management", "mortgage", "financial conglomerates")


def is_financial(market: dict) -> bool:
    industry = (market.get("industry") or "").lower()
    return any(k in industry for k in FINANCIAL_KEYWORDS)


# SEBI LODR Reg. 33: quarterly results within 45 days of the quarter end,
# results for the last quarter of the year (with the audited annual) within 60.
QUARTER_DEADLINE_DAYS, YEAR_END_DEADLINE_DAYS = 45, 60


def results_deadline(quarter_end: date, fy_end_month: int = 3) -> date:
    days = YEAR_END_DEADLINE_DAYS if quarter_end.month == fy_end_month else QUARTER_DEADLINE_DAYS
    return quarter_end + timedelta(days=days)


def _last_quarter_end(today: date) -> date:
    ts = (pd.Timestamp(today) - pd.offsets.QuarterEnd(1)) if not pd.Timestamp(today).is_quarter_end \
        else pd.Timestamp(today)
    return ts.date()


def coverage(fin: dict, today: date | None = None) -> dict:
    """Which periods we have, which are missing, and whether the newest results
    that should be public by now are in the data."""
    today = today or date.today()
    fy_end = fin.get("fy_end_month") or 3
    label = lambda d: quarter_label(pd.Timestamp(d), fy_end)
    ends = sorted({date.fromisoformat(r["period_end"]) for r in fin["quarterly"]}, reverse=True)

    gaps = []
    if len(ends) > 1:
        have = set(ends)
        gaps = [label(e) for e in quarter_ends_between(pd.Timestamp(ends[0]), pd.Timestamp(ends[-1]))
                if e.date() not in have]

    # Newest quarter whose results deadline has passed, and one still inside its window.
    last_end = _last_quarter_end(today)
    due = pending = None
    for e in quarter_ends_between(pd.Timestamp(last_end), pd.Timestamp(last_end) - pd.DateOffset(months=9)):
        d = e.date()
        if results_deadline(d, fy_end) < today:
            due = d
            break
        if pending is None:
            pending = d
    newest = ends[0] if ends else None
    behind = []
    if due and (newest is None or newest < due):
        start = pd.Timestamp(newest) + pd.offsets.QuarterEnd(1) if newest else pd.Timestamp(due)
        behind = [label(e) for e in reversed(quarter_ends_between(pd.Timestamp(due), start))]
    pending_note = None
    if pending and (newest is None or newest < pending):
        pending_note = (f"{label(pending)} results may not be published yet "
                        f"(deadline {results_deadline(pending, fy_end):%d %b %Y}).")

    return {
        "basis": fin.get("basis", "consolidated"),
        "fy_end_month": fy_end,
        "annual_periods": [r["period"] for r in fin["annual"] if r["revenue"] is not None],
        "quarters_on_file": [label(e) for e in ends],
        "newest_quarter_end": newest.isoformat() if newest else None,
        "missing_quarters": gaps,
        "latest_due_quarter": label(due) if due else None,
        "latest_due_deadline": results_deadline(due, fy_end).isoformat() if due else None,
        "not_in_source_yet": behind,     # deadline passed, source doesn't have them
        "pending_note": pending_note,    # deadline not passed yet — not a problem
    }


def add_exchange_filings(ratios: dict, filings: dict | None) -> dict:
    """Use the company's NSE filings to tell "results are out but our data source doesn't
    have them yet" apart from "results may not be out yet". Adds coverage["filed_not_in_source"]
    and rewrites the data-source warning to say what the exchange shows. Returns `ratios`."""
    cov = ratios.get("coverage")
    if not cov or not filings:
        return ratios
    fy_end = cov.get("fy_end_month") or 3
    newest = cov.get("newest_quarter_end")
    filed = []
    for f in filings.get("results_filed") or []:
        q_end = date.fromisoformat(f["quarter_end"])
        if newest and q_end <= date.fromisoformat(newest):
            continue
        filed.append({"quarter": quarter_label(pd.Timestamp(q_end), fy_end),
                      "quarter_end": f["quarter_end"], "filed": (f.get("filed") or "")[:10],
                      "link": f.get("link")})
    cov["filed_not_in_source"] = filed
    if not filed:
        return ratios

    labels = [f["quarter"] for f in filed]
    if cov.get("pending_note") and any(l in cov["pending_note"] for l in labels):
        cov["pending_note"] = None           # not "may not be published yet": it's out
    behind = list(dict.fromkeys(cov["not_in_source_yet"] + list(reversed(labels))))
    on_file = cov["quarters_on_file"][0] if cov["quarters_on_file"] else "none"
    filed_txt = "; ".join(f"{f['quarter']} on {_day(f['filed'])}" for f in filed)
    warning = (f"Our data source (Yahoo Finance) does not have {', '.join(behind)} results yet. "
               f"The company's NSE filings show results were announced ({filed_txt}), so "
               f"those figures exist but are missing from this comparison. "
               f"Newest quarter in our data: {on_file}.")
    ratios["data_warnings"] = [warning] + [
        w for w in ratios["data_warnings"]
        if not w.startswith("Our data source (Yahoo Finance) does not have")]
    return ratios


def _day(iso: str) -> str:
    try:
        return date.fromisoformat(iso[:10]).strftime("%d %b %Y").lstrip("0")
    except ValueError:
        return iso or "a recent date"


def _relative_gap(a, b):
    if a is None or b is None or b == 0:
        return None
    return abs(a / b - 1)


def _data_warnings(fin, market, by_year, valuation, latest_q, cov, today) -> list[str]:
    warnings = []
    if fin["annual"]:
        age = (today - date.fromisoformat(fin["annual"][0]["period_end"])).days
        if age > 450:
            warnings.append(f"Latest annual data is {fin['annual'][0]['period']} "
                            f"({age} days old) — newer results may be missing.")
    if cov["not_in_source_yet"]:
        q = cov["not_in_source_yet"]
        newest = cov["quarters_on_file"][0] if cov["quarters_on_file"] else "none"
        warnings.append(
            f"Our data source (Yahoo Finance) does not have {', '.join(q)} results yet, although "
            f"the filing deadline has passed — the company has most likely published them on "
            f"NSE/BSE. Newest quarter in our data: {newest}.")
    if cov["missing_quarters"]:
        warnings.append(
            f"Quarter(s) missing from the data source: {', '.join(cov['missing_quarters'])}. "
            "Comparisons that need them (same quarter last year, last-12-months totals) "
            "are not available — this is a data gap, not a sign the results were not published.")
    gap = _relative_gap(valuation["pe"], market.get("yahoo_trailing_pe"))
    if gap is not None and gap > 0.15:
        warnings.append(f"Our P/E ({valuation['pe']:.1f}) differs from Yahoo's "
                        f"({market['yahoo_trailing_pe']:.1f}) by {gap:.0%}.")
    if by_year:
        gap = _relative_gap(by_year[0]["roe"], market.get("yahoo_roe"))
        if gap is not None and gap > 0.25:
            warnings.append(f"Our ROE ({by_year[0]['roe']:.1%}) differs from Yahoo's "
                            f"({market['yahoo_roe']:.1%}) by {gap:.0%} — equity figure "
                            "may be unreliable; verify against the annual report.")
    for row in by_year:
        g = row["revenue_growth"]
        if g is not None and abs(g) > 0.5:
            warnings.append(f"Revenue changed {g:+.0%} in {row['period']} — may reflect a merger, "
                            "demerger or accounting change, so growth across that year isn't "
                            "like-for-like.")
    if fin.get("source", {}).get("note"):
        warnings.append(fin["source"]["note"])
    return warnings


def compute_ratios(fin: dict, market: dict) -> dict:
    annual = [r for r in fin["annual"] if r["revenue"] is not None]
    by_year = [_year_ratios(r, annual[i + 1] if i + 1 < len(annual) else None)
               for i, r in enumerate(annual)]
    financial = is_financial(market)
    if financial:
        for row in by_year:
            for k in NOT_FOR_FINANCIALS:
                row[k] = None

    growth = {"revenue_cagr_3y": None, "profit_cagr_3y": None, "cagr_period": None}
    if len(annual) >= 4:
        now, then = annual[0], annual[3]
        growth = {
            "revenue_cagr_3y": cagr(now["revenue"], then["revenue"], 3),
            "profit_cagr_3y": cagr(now["net_income"], then["net_income"], 3),
            "cagr_period": f"{then['period']}–{now['period']}",
        }

    mcap = market.get("market_cap")
    ttm_ni, ttm_label = _ttm_net_income(fin["quarterly"])
    if ttm_ni is None and annual:
        ttm_ni, ttm_label = annual[0]["net_income"], f"annual {annual[0]['period']}"
    latest = annual[0] if annual else None
    ev = None
    if mcap is not None and latest and latest["total_debt"] is not None:
        ev = mcap + latest["total_debt"] - (latest["cash_and_investments"] or 0)

    valuation = {
        "market_cap": mcap,
        "pe": safe_div(mcap, ttm_ni) if ttm_ni and ttm_ni > 0 else None,
        "pe_earnings_basis": ttm_label,
        "pb": safe_div(mcap, latest and latest["equity"]),
        "ev_ebitda": None if financial else safe_div(ev, latest and latest["ebitda"]),
    }
    latest_q = _latest_quarter(fin["quarterly"])
    today = date.today()
    cov = coverage(fin, today)

    return {
        "ticker": fin["ticker"],
        "is_financial": financial,
        "not_applicable": NOT_FOR_FINANCIALS if financial else [],
        "by_year": by_year,
        "growth": growth,
        "valuation": valuation,
        "latest_quarter": latest_q,
        "coverage": cov,
        "data_warnings": _data_warnings(fin, market, by_year, valuation, latest_q, cov, today),
        "formulas": FORMULAS,
        "source": fin["source"],
    }
