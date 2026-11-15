"""News agent — what has happened to each company in the last 12 months.

Inputs, per company (headlines and filing titles only, no article text):
  - general news (Google News, any outlet)
  - Indian business press (ET, Business Standard, Mint, Moneycontrol, FE, BusinessLine)
  - leadership & governance headlines (CEO/board/regulator/auditor stories)
  - official NSE filings from the last 90 days (collected from NSE's RSS feeds)

    report = await run_news(companies)
"""

from __future__ import annotations

from agents.common import UNTRUSTED_NOTE, FactSheet, add_headlines, run_specialist, skipped
from agents.schemas import AgentReport

HEADLINES_PER_COMPANY = 15      # general + business press, newest first
GOVERNANCE_PER_COMPANY = 8
FILINGS_PER_COMPANY = 10

SYSTEM = f"""You are the News analyst in EquityLens, a research tool that compares 2–3 Indian \
listed companies in the same sector. You judge ONLY the news headlines and official stock-exchange \
filings given to you (last 12 months, newest first). Financial ratios, share-price data, social media and the economy are \
covered by other analysts — leave them out.

Judge each company, relative to the others here, on what the news says about the business:
- Positive: results beating expectations, large orders or deals, expansion, upgrades, \
well-received leadership changes.
- Negative: results missing expectations, guidance cuts, lay-offs, lost clients, governance, \
legal, tax or regulatory problems, fraud, auditor or management exits, downgrades.
- Weigh recent and serious events more. One governance or fraud story outweighs many routine ones.
- Leadership & governance headlines (CEO/MD exits, board changes, regulator action, auditor \
issues, disagreements in top management) deserve explicit attention: if there are any, say \
what they report in key_points or red_flags.
- NSE filings are the company's own official disclosures — the most reliable source here. \
Use them to confirm or correct headlines (e.g. a director's resignation, a results date).
- Ignore noise: market wraps ("stocks to watch", "Sensex today"), share-price-only headlines, \
and headlines that only mention the company in passing.
- More coverage is not better by itself.

Rules:
- {UNTRUSTED_NOTE}
- You only have headlines, not the articles. Do not invent details the headline doesn't state.
- Every evidence item cites the headline's fact ID(s) in `source`, e.g. "F7".
- Copy numbers exactly as written in the headline. Never calculate new numbers.
- If headlines are few or mostly noise, say so in data_gaps and lower confidence.
- Score 0–10 on this lens only, relative to the peers here; 5 = neutral or mixed news.
- This is research, not advice: never say buy, sell, invest, accumulate or avoid.
- Short, plain sentences for a retail investor.
"""


def _add_filings(sheet: FactSheet, ticker: str, filings: dict) -> list[str]:
    ids = []
    for f in (filings.get("items") or [])[:FILINGS_PER_COMPANY]:
        day = (f.get("published") or "")[:10] or "date unknown"
        what = f.get("detail") if f.get("kind") == "announcement" else (
            f"{f.get('subject')}" + (f" (meeting {f['meeting_date']})" if f.get("meeting_date") else ""))
        kind = {"announcement": "announcement", "board_meeting": "board meeting",
                "corporate_action": "corporate action"}.get(f.get("kind"), "filing")
        ids.append(sheet.add(ticker, f"{day} · NSE {kind} · {what}",
                             f"NSE filing: “{what}” ({day}) {f.get('link', '')}".strip()))
    return ids


def _company_block(sheet: FactSheet, c: dict) -> str:
    t, news = c["ticker"], c.get("news") or {}
    arts = news.get("articles") or []
    gov = [a for a in arts if "governance" in (a.get("feeds") or [])]
    rest = [a for a in arts if a not in gov]
    lines = [f"== {t} — {c['market']['name']}  ({news.get('total_found', 0)} headlines found "
             "in the last 12 months"
             + ("" if str(news.get("status", "ok")).startswith("ok")
                else f"; news feed {str(news.get('status'))[:60]}") + ")"]

    before = len(sheet.facts)
    add_headlines(sheet, t, rest, HEADLINES_PER_COMPANY)
    lines.append("-- News (all outlets + Indian business press), newest first:")
    lines.append("\n".join(f"[{fid}] {sheet.facts[fid].text}"
                           for fid in list(sheet.facts)[before:]) or "(no headlines found)")

    before = len(sheet.facts)
    add_headlines(sheet, t, gov, GOVERNANCE_PER_COMPANY)
    lines.append("-- Leadership & governance headlines:")
    lines.append("\n".join(f"[{fid}] {sheet.facts[fid].text}"
                           for fid in list(sheet.facts)[before:]) or "(none found)")

    filings = c.get("filings") or {}
    if str(filings.get("status", "")).startswith("ok"):
        ids = _add_filings(sheet, t, filings)
        since = (filings.get("history_starts") or "")[:10]
        lines.append(f"-- Official NSE filings, last {filings.get('days', 90)} days"
                     + (f" (our filing history starts {since})" if since else "")
                     + (f"; {filings['routine_skipped']} routine filings left out"
                        if filings.get("routine_skipped") else "") + ":")
        lines.append("\n".join(f"[{fid}] {sheet.facts[fid].text}" for fid in ids) or "(none)")
    return "\n".join(lines)


def build_prompt(companies: list[dict], macro: dict | None = None) -> tuple[FactSheet, str]:
    sheet = FactSheet("F")
    blocks = [_company_block(sheet, c) for c in companies]
    names = ", ".join(f"{c['ticker']} ({c['market']['name']})" for c in companies)
    prompt = (f"Compare what the news and filings say about: {names}.\n"
              "Each line: date · publisher (or NSE filing type) · headline.\n\n"
              + "\n\n".join(blocks)
              + "\n\nReturn one assessment per company, using the tickers exactly as written above.")
    return sheet, prompt


def confidence_cap(companies: list[dict]) -> tuple[float, str]:
    cap, reasons = 0.75, ["headlines only, no article text"]
    thin = [c["ticker"] for c in companies
            if len((c.get("news") or {}).get("articles") or []) < 5]
    if thin:
        cap -= 0.1 * len(thin)
        reasons.append(f"fewer than 5 headlines for {', '.join(thin)}")
    return max(round(cap, 2), 0.3), ", ".join(reasons)


async def run_news(companies: list[dict], macro: dict | None = None) -> AgentReport:
    if not any((c.get("news") or {}).get("articles") or (c.get("filings") or {}).get("items")
               for c in companies):
        return skipped("news", "No recent news headlines were found for these companies.")
    sheet, prompt = build_prompt(companies)
    cap, reason = confidence_cap(companies)
    return await run_specialist("news", SYSTEM, prompt, sheet,
                                [c["ticker"] for c in companies], cap, reason)
