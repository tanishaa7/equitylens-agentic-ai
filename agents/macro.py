"""Macro agent — sector and economy backdrop: sector index vs Nifty 50,
USD/INR, crude oil, sector and RBI policy headlines, plus each company's
exposure (debt load, foreign-currency reporting).

    report = await run_macro(companies, macro)
"""

from __future__ import annotations

from data.common import fmt_pct

from agents.common import UNTRUSTED_NOTE, FactSheet, add_headlines, run_specialist
from agents.schemas import AgentReport

SECTOR_HEADLINES = 8
POLICY_HEADLINES = 6

SYSTEM = f"""You are the Macro & Sector analyst in EquityLens, a research tool that compares \
2–3 Indian listed companies in the same sector. You judge the economic and sector backdrop from \
the facts given: sector index versus the Nifty 50, the rupee, crude oil, sector news and RBI \
policy news — and how exposed each company is to them. Company financial performance, share \
prices, company news and social media are covered by other analysts — leave them out.

How to judge:
- First describe the backdrop for the sector: tailwinds and headwinds.
- Then score each company on how well placed it is for that backdrop, using its exposure facts \
(debt load → interest-rate sensitivity; reporting in USD → large overseas business, so a weaker \
rupee helps rupee earnings).
- You may use widely known economic relationships (e.g. higher rates hurt heavily indebted \
companies; costlier crude hurts fuel-heavy businesses; a weaker rupee helps exporters). Do not \
use outside knowledge about these specific companies.
- Peers in one sector share most of the backdrop, so keep scores close unless their exposure \
facts clearly differ.

Rules:
- {UNTRUSTED_NOTE}
- Every evidence item cites fact ID(s) in `source`, e.g. "F2" or "F1, F6".
- Copy numbers exactly as written in the facts. Never calculate new numbers.
- Score 0–10 on this lens only; 5 = neutral backdrop / average exposure.
- This is research, not advice: never say buy, sell, invest, accumulate or avoid.
- Short, plain sentences for a retail investor.
"""

YAHOO = "Yahoo Finance daily prices"


def _num(x, d=2):
    return "n/a" if x is None else f"{x:,.{d}f}"


def _add_backdrop(sheet: FactSheet, macro: dict):
    bench, si = macro.get("benchmark") or {}, macro.get("sector_index")
    b1y = (bench.get("returns") or {}).get("1y")
    if si:
        pts = si.get("vs_nifty_1y_pts")
        sheet.add(None, f"{si['name']} (sector index) 1-year return {fmt_pct(si['returns'].get('1y'))} "
                        f"vs {bench.get('name', 'Nifty 50')} {fmt_pct(b1y)} "
                        f"({'n/a' if pts is None else f'{pts:+.1f} pts'})",
                  f"{YAHOO} · pts = sector 1y return − Nifty 50 1y return")
    series = macro.get("macro_series") or {}
    if (fx := series.get("usd_inr")) and fx.get("latest") is not None:
        sheet.add(None, f"USD/INR {_num(fx['latest'])}, 1-year change {fmt_pct(fx['change_1y'])} "
                        "(a rise means the rupee weakened against the dollar)", f"{YAHOO} (INR=X)")
    if (oil := series.get("brent_usd")) and oil.get("latest") is not None:
        sheet.add(None, f"Brent crude ${_num(oil['latest'])} per barrel, 1-year change "
                        f"{fmt_pct(oil['change_1y'])}", f"{YAHOO} (BZ=F)")
    add_headlines(sheet, None, macro.get("sector_headlines") or [], SECTOR_HEADLINES)
    add_headlines(sheet, None, macro.get("policy_headlines") or [], POLICY_HEADLINES)


def _add_exposure(sheet: FactSheet, c: dict):
    t, r = c["ticker"], c.get("ratios") or {}
    y = (r.get("by_year") or [None])[0]
    if r.get("is_financial"):
        sheet.add(t, "Financial company (bank/NBFC/insurer): interest-rate changes move its "
                     "lending margins and loan growth directly", "EquityLens classification by industry")
    elif y:
        de, ic = y.get("debt_to_equity"), y.get("interest_coverage")
        sheet.add(t, f"Debt / equity {_num(de)} and interest coverage "
                     f"{'n/a' if ic is None else f'{ic:.1f}x'} in {y['period']}",
                  "Yahoo Finance annual statements · D/E = total debt / equity; "
                  "coverage = EBIT / interest expense")
    cur = (c.get("financials") or {}).get("reported_currency")
    if cur and cur != "INR":
        sheet.add(t, f"Reports its financial statements in {cur} — a sign of a large overseas business",
                  "Yahoo Finance (financialCurrency)")


def build_prompt(companies: list[dict], macro: dict | None = None) -> tuple[FactSheet, str]:
    sheet = FactSheet("F")
    macro = macro or {}
    _add_backdrop(sheet, macro)
    shared = sheet.render(None)
    blocks = []
    for c in companies:
        _add_exposure(sheet, c)
        m = c["market"]
        blocks.append(f"== {c['ticker']} — {m['name']} ({m.get('industry') or 'industry unknown'})\n"
                      + (sheet.render(c["ticker"]) or "(no exposure data)"))
    names = ", ".join(f"{c['ticker']} ({c['market']['name']})" for c in companies)
    prompt = (f"Sector: {macro.get('sector') or 'unknown'} / {macro.get('industry') or 'unknown'}.\n"
              f"Judge the economic and sector backdrop for: {names}.\n"
              "Headlines are: date · publisher · headline (sector news: last 90 days; "
              "RBI policy news: last 180 days).\n\n"
              f"== Backdrop (shared by all)\n{shared or '(no backdrop data)'}\n\n"
              + "\n\n".join(blocks)
              + "\n\nReturn one assessment per company, using the tickers exactly as written above.")
    return sheet, prompt


def confidence_cap(macro: dict | None) -> tuple[float, str]:
    cap, reasons = 0.7, ["macro effects on a single company are indirect"]
    if not (macro or {}).get("sector_index"):
        cap -= 0.1
        reasons.append("no matching sector index")
    return round(cap, 2), ", ".join(reasons)


async def run_macro(companies: list[dict], macro: dict | None = None) -> AgentReport:
    sheet, prompt = build_prompt(companies, macro)
    cap, reason = confidence_cap(macro)
    return await run_specialist("macro", SYSTEM, prompt, sheet,
                                [c["ticker"] for c in companies], cap, reason)
