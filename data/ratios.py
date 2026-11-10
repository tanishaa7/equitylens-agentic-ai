"""Deterministic ratio calculations — the calculator does the maths, not the AI.

Input: output of get_financials() and get_market_data().
Every ratio has its formula listed in FORMULAS so the report can cite it.
"""

from __future__ import annotations

from datetime import date

from data.common import cagr, safe_div

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
                      "free_cash_flow", "cash_conversion", "receivable_days", "ev_ebitda"]
FINANCIAL_KEYWORDS = ("bank", "insurance", "credit services", "capital markets",
                      "asset management", "mortgage", "financial conglomerates")


def is_financial(market: dict) -> bool:
    industry = (market.get("industry") or "").lower()
    return any(k in industry for k in FINANCIAL_KEYWORDS)


def _relative_gap(a, b):
    if a is None or b is None or b == 0:
        return None
    return abs(a / b - 1)


def _data_warnings(fin, market, by_year, valuation, latest_q) -> list[str]:
    warnings = []
    today = date.today()
    if fin["annual"]:
        age = (today - date.fromisoformat(fin["annual"][0]["period_end"])).days
        if age > 450:
            warnings.append(f"Latest annual data is {fin['annual'][0]['period']} "
                            f"({age} days old) — newer results may be missing.")
    if latest_q and latest_q["current"]:
        q = next(r for r in fin["quarterly"] if r["period"] == latest_q["current"]["period"])
        age = (today - date.fromisoformat(q["period_end"])).days
        if age > 200:
            warnings.append(f"Latest quarter on file is {q['period']} ({age} days old) — "
                            "'what changed' may be stale.")
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

    return {
        "ticker": fin["ticker"],
        "is_financial": financial,
        "not_applicable": NOT_FOR_FINANCIALS if financial else [],
        "by_year": by_year,
        "growth": growth,
        "valuation": valuation,
        "latest_quarter": latest_q,
        "data_warnings": _data_warnings(fin, market, by_year, valuation, latest_q),
        "formulas": FORMULAS,
        "source": fin["source"],
    }
