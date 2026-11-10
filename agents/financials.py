"""Financials agent — judges reported statements and valuation multiples.

Python does all the maths (data/ratios.py). This agent turns those numbers
into numbered facts, asks the AI to compare the companies on six lenses,
then verifies every number the AI wrote against the facts.

    report = await run_financials(companies)   # companies: [{ticker, market, ratios}, ...]
"""

from __future__ import annotations

from data.common import fmt_cr, fmt_pct
from data.ratios import FORMULAS

from agents.common import FactSheet, run_specialist
from agents.schemas import AgentReport

SYSTEM = """You are the Financials analyst in EquityLens, a research tool that compares 2–3 \
Indian listed companies in the same sector. You judge ONLY the reported financial statements \
and valuation multiples given to you. Other analysts cover news, social media, share-price \
behaviour and the economy — leave those topics out.

Judge each company on these lenses, relative to the others in this comparison:
1. Growth — revenue and profit growth, 3-year CAGR, consistency over the years.
2. Profitability — margins, ROE, ROCE, and whether they are rising or falling.
3. Balance sheet — debt/equity, interest coverage.
4. Cash quality — free cash flow, cash conversion (operating cash flow / net profit), receivable days.
5. Valuation — P/E, P/B, EV/EBITDA, weighed against each company's growth and quality.
6. Latest quarter — what changed versus the previous quarter and the same quarter last year.

Rules:
- Use only the numbered facts. Do not use outside knowledge about these companies.
- Every evidence item must cite the fact ID(s) it uses in `source`, e.g. "F12" or "F3, F27".
- Copy numbers exactly as written in the facts (e.g. "58.5%", "₹267,021 cr"). Never calculate \
new numbers such as differences, averages or percentages of change.
- Metrics marked "not applicable" (banks, insurers, NBFCs) must not count against a company. \
Compare such companies on ROE, net margin, growth and valuation.
- Data warnings are reasons for caution: mention them in red_flags or data_gaps and lower confidence.
- Score 0–10 on this lens only, relative to the peers here; 5 = in line with peers. Spread the \
scores when the numbers clearly differ; keep them close when they don't.
- This is research, not advice: never say buy, sell, invest, accumulate or avoid.
- Write for a retail investor: short, plain sentences; explain a term briefly if it is technical.
"""

N_YEARS = 4  # how many fiscal years to show the AI


def _x(v):
    return f"{v:.1f}x"


def _r2(v):
    return f"{v:.2f}"


def _days(v):
    return f"{v:.0f} days"


# (key in ratios.by_year, label, formatter)
METRICS = [
    ("revenue", "Revenue", fmt_cr),
    ("net_income", "Net profit", fmt_cr),
    ("revenue_growth", "Revenue growth YoY", fmt_pct),
    ("profit_growth", "Net profit growth YoY", fmt_pct),
    ("ebitda_margin", "EBITDA margin", fmt_pct),
    ("operating_margin", "Operating margin", fmt_pct),
    ("net_margin", "Net margin", fmt_pct),
    ("roe", "ROE", fmt_pct),
    ("roce", "ROCE", fmt_pct),
    ("debt_to_equity", "Debt / equity", _r2),
    ("interest_coverage", "Interest coverage", _x),
    ("free_cash_flow", "Free cash flow", fmt_cr),
    ("cash_conversion", "Cash conversion (OCF / net profit)", _r2),
    ("receivable_days", "Receivable days", _days),
]


def _fmt(v, f):
    return "n/a" if v is None else f(v)


def _add_company(sheet: FactSheet, c: dict) -> list[str]:
    """Add one company's facts; return context lines (header) for the prompt."""
    t, m, r = c["ticker"], c["market"], c["ratios"]
    provider = r["source"].get("provider", "Yahoo Finance")
    years = list(reversed(r["by_year"][:N_YEARS]))   # oldest → newest
    span = f"{years[0]['period']}–{years[-1]['period']}" if years else "n/a"

    for key, label, f in METRICS:
        if key in r["not_applicable"] or all(y[key] is None for y in years):
            continue
        series = " → ".join(f"{y['period']} {_fmt(y[key], f)}" for y in years)
        formula = f" · {label} = {FORMULAS[key]}" if key in FORMULAS else ""
        sheet.add(t, f"{label}: {series}", f"{provider} annual statements ({span}){formula}")

    g = r["growth"]
    if g["cagr_period"]:
        sheet.add(t, f"3-year CAGR ({g['cagr_period']}): revenue {fmt_pct(g['revenue_cagr_3y'])}, "
                     f"net profit {fmt_pct(g['profit_cagr_3y'])}",
                  f"{provider} annual statements · CAGR = (now / 3 years ago)^(1/3) − 1")

    v = r["valuation"]
    if v["market_cap"] is not None:
        sheet.add(t, f"Valuation: market cap {fmt_cr(v['market_cap'])}; "
                     f"P/E {_fmt(v['pe'], lambda x: f'{x:.1f}')} "
                     f"(on {v['pe_earnings_basis'] or 'n/a'} net profit); "
                     f"P/B {_fmt(v['pb'], lambda x: f'{x:.1f}')}"
                     + ("" if "ev_ebitda" in r["not_applicable"]
                        else f"; EV/EBITDA {_fmt(v['ev_ebitda'], lambda x: f'{x:.1f}')}"),
                  f"{m['source'].get('provider', 'Yahoo Finance')} quote (market cap, "
                  f"{(m['source'].get('fetched_at') or '')[:10]}) + statements · "
                  f"P/E = {FORMULAS['pe']}; P/B = {FORMULAS['pb']}")

    q = r["latest_quarter"]
    if q and q["current"]:
        cur = q["current"]
        pts = q["ebitda_margin_change_qoq_pts"]
        margin = ("" if "ebitda_margin" in r["not_applicable"] or cur["ebitda_margin"] is None
                  else f"; EBITDA margin {fmt_pct(cur['ebitda_margin'])}"
                       + ("" if pts is None else f" ({pts:+.1f} pts vs previous quarter)"))
        sheet.add(t, f"Latest quarter {cur['period']}: revenue {fmt_cr(cur['revenue'])} "
                     f"(QoQ {fmt_pct(q['revenue_qoq'])}, YoY {fmt_pct(q['revenue_yoy'])}); "
                     f"net profit {fmt_cr(cur['net_income'])} "
                     f"(QoQ {fmt_pct(q['net_income_qoq'])}, YoY {fmt_pct(q['net_income_yoy'])})"
                     + margin,
                  f"{provider} quarterly statements")

    for w in r["data_warnings"]:
        sheet.add(t, f"Data warning: {w}", "EquityLens data checks")

    header = [f"== {t} — {m['name']} ({m.get('industry') or 'industry unknown'})"]
    if r["is_financial"]:
        labels = {k: lbl for k, lbl, _ in METRICS} | {"ev_ebitda": "EV/EBITDA"}
        header.append("Financial company: "
                      + ", ".join(labels.get(k, k) for k in r["not_applicable"])
                      + " are not applicable. Bank metrics such as NIM, GNPA and CASA are "
                        "not available in this data.")
    return header


def build_prompt(companies: list[dict], macro: dict | None = None) -> tuple[FactSheet, str]:
    sheet = FactSheet("F")
    blocks = []
    for c in companies:
        header = _add_company(sheet, c)
        blocks.append("\n".join(header + [sheet.render(c["ticker"])]))
    names = ", ".join(f"{c['ticker']} ({c['market']['name']})" for c in companies)
    prompt = (
        f"Compare the financial strength of: {names}.\n"
        "All money is in Indian rupees; ₹ cr = crore. FY26 = fiscal year April 2025 – March 2026. "
        "'n/a' = not available. '—' = could not be calculated.\n\n"
        + "\n\n".join(blocks)
        + "\n\nReturn one assessment per company, using the tickers exactly as written above."
    )
    return sheet, prompt


def confidence_cap(companies: list[dict]) -> tuple[float, str]:
    """Code decides the ceiling on confidence from data quality — the AI can't override it."""
    cap, reasons = 1.0, []
    n_warn = sum(len(c["ratios"]["data_warnings"]) for c in companies)
    if n_warn:
        cap -= 0.1 * n_warn
        reasons.append(f"{n_warn} data warning(s)")
    if any(c["ratios"]["is_financial"] for c in companies):
        cap -= 0.1
        reasons.append("bank-specific metrics unavailable")
    short = [c["ticker"] for c in companies if len(c["ratios"]["by_year"]) < 3]
    if short:
        cap -= 0.15
        reasons.append(f"less than 3 years of data for {', '.join(short)}")
    return max(round(cap, 2), 0.3), ", ".join(reasons)


async def run_financials(companies: list[dict], macro: dict | None = None) -> AgentReport:
    sheet, prompt = build_prompt(companies)
    cap, reason = confidence_cap(companies)
    return await run_specialist("financials", SYSTEM, prompt, sheet,
                                [c["ticker"] for c in companies], cap, reason)
