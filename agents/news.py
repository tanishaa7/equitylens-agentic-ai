"""News agent — what has happened to each company in the last 12 months,
from Google News headlines (India edition). Headlines only, no article text.

    report = await run_news(companies)
"""

from __future__ import annotations

from agents.common import UNTRUSTED_NOTE, FactSheet, add_headlines, run_specialist, skipped
from agents.schemas import AgentReport

HEADLINES_PER_COMPANY = 15

SYSTEM = f"""You are the News analyst in EquityLens, a research tool that compares 2–3 Indian \
listed companies in the same sector. You judge ONLY the news headlines given to you (last 12 \
months, newest first). Financial ratios, share-price data, social media and the economy are \
covered by other analysts — leave them out.

Judge each company, relative to the others here, on what the news says about the business:
- Positive: results beating expectations, large orders or deals, expansion, upgrades, \
well-received leadership changes.
- Negative: results missing expectations, guidance cuts, lay-offs, lost clients, governance, \
legal, tax or regulatory problems, fraud, auditor or management exits, downgrades.
- Weigh recent and serious events more. One governance or fraud story outweighs many routine ones.
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


def build_prompt(companies: list[dict], macro: dict | None = None) -> tuple[FactSheet, str]:
    sheet = FactSheet("F")
    blocks = []
    for c in companies:
        t, news = c["ticker"], c.get("news") or {}
        arts = news.get("articles") or []
        shown = add_headlines(sheet, t, arts, HEADLINES_PER_COMPANY)
        status = news.get("status", "not fetched")
        head = (f"== {t} — {c['market']['name']}  ({news.get('total_found', 0)} headlines found "
                f"in the last 12 months, newest {shown} shown"
                + ("" if status.startswith("ok") else f"; news feed {status[:60]}") + ")")
        blocks.append(head + "\n" + (sheet.render(t) or "(no headlines found)"))
    names = ", ".join(f"{c['ticker']} ({c['market']['name']})" for c in companies)
    prompt = (f"Compare what the news says about: {names}.\n"
              "Each line: date · publisher · headline.\n\n" + "\n\n".join(blocks)
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
    if not any((c.get("news") or {}).get("articles") for c in companies):
        return skipped("news", "No recent news headlines were found for these companies.")
    sheet, prompt = build_prompt(companies)
    cap, reason = confidence_cap(companies)
    return await run_specialist("news", SYSTEM, prompt, sheet,
                                [c["ticker"] for c in companies], cap, reason)
