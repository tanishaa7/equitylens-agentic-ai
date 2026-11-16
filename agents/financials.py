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
from agents.sector_notes import sector_note

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
- A quarter missing from the data is a gap in our data source, not proof the company hasn't \
published it. Never say results are "not available", "not published" or "not out" — say they \
are not in our data. If the coverage fact says results "were announced on NSE", say the \
company has reported that quarter but its figures are not in our data yet.
- Banks: judge asset quality by gross and net NPA (lower is better, and falling is better) and \
capital by the CET1 ratio (higher is a bigger buffer). Use the bank facts when present.
- A fact marked "from the company's results filing" is newer than our main source; prefer it \
for the latest quarter.
- Statements are consolidated (whole group, including subsidiaries) unless a fact says \
"standalone". Say which when it matters (e.g. receivables or debt that may sit in a \
subsidiary). Never compare a standalone figure with a consolidated one.
- "Sector norms" describe what is ordinary for the industry. Use them to avoid flagging normal \
features as problems (e.g. a sector that normally carries receivables). They are background, \
not evidence: never cite them as a source.
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
    ("receivables", "Receivables at year-end", fmt_cr),
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
        if key in r["not_applicable"] or all(y.get(key) is None for y in years):
            continue
        series = " → ".join(f"{y['period']} {_fmt(y.get(key), f)}" for y in years)
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
        no_yoy = q["same_quarter_last_year"] is None
        yoy = lambda v: "same quarter last year not in our data" if no_yoy else fmt_pct(v)
        sheet.add(t, f"Latest quarter {cur['period']}: revenue {fmt_cr(cur['revenue'])} "
                     f"(QoQ {fmt_pct(q['revenue_qoq'])}, YoY {yoy(q['revenue_yoy'])}); "
                     f"net profit {fmt_cr(cur['net_income'])} "
                     f"(QoQ {fmt_pct(q['net_income_qoq'])}, YoY {yoy(q['net_income_yoy'])})"
                     + margin,
                  f"{provider} quarterly statements")

    ex = r.get("exchange")
    if ex:
        _add_exchange_facts(sheet, t, ex)

    cov = r.get("coverage")
    if cov:
        sheet.add(t, coverage_text(cov), f"{provider} · EquityLens coverage check")

    for w in r["data_warnings"]:
        sheet.add(t, f"Data warning: {w}", "EquityLens data checks")

    header = [f"== {t} — {m['name']} ({m.get('industry') or 'industry unknown'})"]
    if cov and cov.get("fy_end_month") == 12:
        header.append("Financial year ends in December: annual periods are calendar years and "
                      "quarters are labelled Q1–Q4 CY (e.g. Q3 CY26 = Jul–Sep 2026).")
    if r["is_financial"]:
        labels = {k: lbl for k, lbl, _ in METRICS} | {"ev_ebitda": "EV/EBITDA"}
        header.append("Financial company: "
                      + ", ".join(labels.get(k, k) for k in r["not_applicable"])
                      + " are not applicable."
                      + (" Bank asset quality and capital come from the bank's own results "
                         "filing (see the facts); NIM and CASA are not in it."
                         if ex and ex.get("bank") else
                         " Bank metrics such as NIM, GNPA and CASA are not available in this data."))
    return header


EX_LABELS = [("total_income", "total income"), ("revenue", "revenue"),
             ("net_interest_income", "net interest income"), ("operating_profit", "operating profit"),
             ("provisions", "provisions"), ("net_profit", "net profit")]


def _add_exchange_facts(sheet: FactSheet, t: str, ex: dict) -> None:
    """Figures from the company's own XBRL results filings (uploaded in the app)."""
    lt = ex.get("latest")
    if lt and ex.get("latest_is_newer"):
        parts = []
        for key, label in EX_LABELS:
            v = lt["figures"].get(key)
            if v is None:
                continue
            yoy = lt["yoy"].get(key)
            parts.append(f"{label} {fmt_cr(v)}" + ("" if yoy is None else f" ({fmt_pct(yoy)} YoY)"))
        ago = (f"year-ago quarter {lt['year_ago_quarter']} also from the filings"
               if lt["year_ago_quarter"] else "year-ago quarter not on file")
        sheet.add(t, f"Latest quarter {lt['quarter']} ({lt['basis']}, from the company's results "
                     f"filing — newer than our main source): " + "; ".join(parts) + f"; {ago}",
                  f"Company XBRL results filing (NSE) · {lt['source_file']}")
    b = ex.get("bank")
    if b:
        prev = b.get("year_ago") or {}
        def pct(k, label):
            v = b.get(k)
            if v is None:
                return None
            was = prev.get(k)
            return f"{label} {v:.2%}" + ("" if was is None else
                                          f" ({b['year_ago_quarter']}: {was:.2%})")
        parts = [x for x in (pct("gross_npa_pct", "gross NPA"), pct("net_npa_pct", "net NPA"),
                             pct("cet1_ratio", "CET1 capital ratio"),
                             pct("roa_quarter", "return on assets for the quarter (not annualised)"))
                 if x]
        for k, label in (("deposits", "deposits"), ("advances", "advances")):
            if b.get(k) is not None:
                growth = (None if not prev.get(k) else b[k] / prev[k] - 1)
                parts.append(f"{label} {fmt_cr(b[k])}"
                             + ("" if growth is None else f" ({fmt_pct(growth)} YoY)"))
        sheet.add(t, f"Asset quality and capital, {b['quarter']} (standalone, as banks report "
                     f"them): " + "; ".join(parts),
                  "Company XBRL results filing (NSE) · RBI-format bank results")


def coverage_text(cov: dict) -> str:
    """One line on which periods we have and what's missing."""
    parts = [f"Data coverage ({cov.get('basis', 'consolidated')} statements): annual "
             + (", ".join(cov["annual_periods"]) or "none")
             + "; quarters " + (", ".join(cov["quarters_on_file"]) or "none")]
    if cov.get("from_filings"):
        parts.append("from the company's own results filings: " + ", ".join(
            f"{q} ({'/'.join(b)})" for q, b in cov["from_filings"].items()))
    if cov["missing_quarters"]:
        parts.append("missing from source: " + ", ".join(cov["missing_quarters"]))
    if cov["not_in_source_yet"]:
        parts.append("past the filing deadline but not in our source yet: "
                     + ", ".join(cov["not_in_source_yet"]))
    for f in cov.get("filed_not_in_source") or []:
        parts.append(f"{f['quarter']} results were announced on NSE ({f['filed']}) but are not "
                     "in our source yet, so those figures are missing here")
    if cov.get("pending_note"):
        parts.append(cov["pending_note"])
    return "; ".join(parts)


def _sector_block(companies: list[dict]) -> str:
    notes = dict.fromkeys(filter(None, (sector_note(c["market"].get("industry"),
                                                    c["market"].get("sector"))
                                        for c in companies)))
    return "" if not notes else "Sector norms (background, not evidence):\n" + "\n".join(
        f"- {n}" for n in notes) + "\n\n"


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
        "Statements are consolidated unless a fact says standalone. "
        "'n/a' = not available. '—' = could not be calculated.\n\n"
        + _sector_block(companies)
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
