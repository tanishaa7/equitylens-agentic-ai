"""Market data agent — how the share price has behaved: returns, risk, and
strength versus the Nifty 50 and the sector index. Backward-looking only.

    report = await run_market(companies, macro)
"""

from __future__ import annotations

from data.common import fmt_pct

from agents.common import FactSheet, run_specialist
from agents.schemas import AgentReport

SYSTEM = """You are the Market Data analyst in EquityLens, a research tool that compares 2–3 \
Indian listed companies in the same sector. You judge ONLY share-price behaviour from the facts \
given: returns, how far each stock is from its 52-week high, volatility, worst fall from a peak, \
and performance versus the Nifty 50 and the sector index. Financial statements, news, social \
media and the economy are covered by other analysts — leave them out.

Judge each company, relative to the others here, on:
1. Relative strength — returns over 1 month, 6 months, 1 year and 3 years, and versus the \
benchmarks (positive "pts" = beat the index).
2. Risk — volatility (how much the price swings) and maximum drawdown (worst fall from a peak). \
Lower risk for similar returns is better.
3. Position — distance from the 52-week high/low, as context only.

Rules:
- Use only the numbered facts. Every evidence item cites fact ID(s) in `source`, e.g. "F4" or "F2, F9".
- Copy numbers exactly as written in the facts. Never calculate new numbers.
- Past price moves do not predict future returns: describe them as history and risk, never as a \
forecast. A big fall can mean either trouble or a cheaper price — say it is a question for the \
other lenses, not a conclusion.
- Score 0–10 on this lens only, relative to the peers here; 5 = in line.
- This is research, not advice: never say buy, sell, invest, accumulate or avoid.
- Short, plain sentences for a retail investor.
"""

RETURN_LABELS = [("1m", "1 month"), ("6m", "6 months"), ("1y", "1 year"), ("3y", "3 years")]


def _rupees(x):
    return "n/a" if x is None else f"₹{x:,.1f}"


def _pts(a, b):
    return None if a is None or b is None else (a - b) * 100


def _fmt_pts(x):
    return "n/a" if x is None else f"{x:+.1f} pts"


def _returns_text(r: dict) -> str:
    return " · ".join(f"{label} {fmt_pct(r.get(k))}" for k, label in RETURN_LABELS)


def build_prompt(companies: list[dict], macro: dict | None = None) -> tuple[FactSheet, str]:
    sheet = FactSheet("F")
    macro = macro or {}
    bench = macro.get("benchmark") or {}
    sector = macro.get("sector_index") or {}
    yahoo = "Yahoo Finance daily prices (adjusted for splits and dividends)"

    if bench.get("returns"):
        sheet.add(None, f"{bench['name']} price return: {_returns_text(bench['returns'])}", yahoo)
    if sector.get("returns"):
        sheet.add(None, f"{sector['name']} (sector index) price return: "
                        f"{_returns_text(sector['returns'])}", yahoo)

    blocks = []
    for c in companies:
        t, m = c["ticker"], c["market"]
        r = m.get("returns") or {}
        below = m.get("below_52w_high")
        sheet.add(t, f"Share price {_rupees(m.get('price'))}; 52-week range "
                     f"{_rupees(m.get('low_52w'))} – {_rupees(m.get('high_52w'))}; "
                     + ("n/a below 52-week high" if below is None
                        else f"{fmt_pct(abs(below))} below its 52-week high"),
                  f"{yahoo}, as of {(m['source'].get('fetched_at') or '')[:10]}")
        sheet.add(t, f"Price return: {_returns_text(r)}", yahoo + " · return = price now / price then − 1")
        rel = [f"vs {bench.get('name', 'Nifty 50')} {_fmt_pts(_pts(r.get('1y'), bench.get('returns', {}).get('1y')))}"]
        if sector.get("returns"):
            rel.append(f"vs {sector['name']} {_fmt_pts(_pts(r.get('1y'), sector['returns'].get('1y')))}")
        sheet.add(t, "1-year return relative to indices: " + "; ".join(rel),
                  yahoo + " · pts = stock 1y return − index 1y return, in percentage points")
        sheet.add(t, f"Risk over the last year: volatility {fmt_pct(m.get('volatility_1y'))} a year; "
                     f"maximum drawdown {fmt_pct(m.get('max_drawdown_1y'))}",
                  yahoo + " · volatility = std. dev. of daily returns × √252; "
                          "max drawdown = worst fall from a previous peak")
        blocks.append(f"== {t} — {m['name']}\n" + sheet.render(t))

    shared = sheet.render(None)
    names = ", ".join(f"{c['ticker']} ({c['market']['name']})" for c in companies)
    prompt = (f"Compare the share-price behaviour of: {names}.\n"
              "'—' or 'n/a' = not available (e.g. listed for less than 3 years).\n\n"
              + (f"== Benchmarks\n{shared}\n\n" if shared else "")
              + "\n\n".join(blocks)
              + "\n\nReturn one assessment per company, using the tickers exactly as written above.")
    return sheet, prompt


def confidence_cap(companies: list[dict], macro: dict | None) -> tuple[float, str]:
    cap, reasons = 0.85, ["price history describes the past, not the business"]
    short = [c["ticker"] for c in companies if (c["market"].get("returns") or {}).get("3y") is None]
    if short:
        cap -= 0.1
        reasons.append(f"no 3-year history for {', '.join(short)}")
    if not (macro or {}).get("benchmark", {}).get("returns"):
        cap -= 0.1
        reasons.append("benchmark data missing")
    return round(cap, 2), ", ".join(reasons)


async def run_market(companies: list[dict], macro: dict | None = None) -> AgentReport:
    sheet, prompt = build_prompt(companies, macro)
    cap, reason = confidence_cap(companies, macro)
    return await run_specialist("market", SYSTEM, prompt, sheet,
                                [c["ticker"] for c in companies], cap, reason)
