"""Social agent — retail investor sentiment on Indian investing subreddits.
Low weight by design: sentiment is noisy and can be hype. Skipped when
Reddit data isn't available (Reddit needs API keys in .env).

    report = await run_social(companies)
"""

from __future__ import annotations

import config
from agents.common import UNTRUSTED_NOTE, FactSheet, run_specialist, skipped
from agents.schemas import AgentReport

POSTS_PER_COMPANY = 12
SNIPPET_CHARS = 200

SYSTEM = f"""You are the Social Sentiment analyst in EquityLens, a research tool that compares \
2–3 Indian listed companies in the same sector. You judge ONLY the Reddit posts given to you, \
from Indian investing communities over the last 12 months. Financial data, share prices, news \
and the economy are covered by other analysts — leave them out.

Judge each company, relative to the others here, on retail investor sentiment:
- What are people worried about or excited about? Are concerns specific (e.g. management, \
debt, slowing growth) or vague?
- How much engagement (upvotes ▲ and comments) do the posts get?
- Flag hype or pump-style posts ("multibagger", "going to the moon", price targets without \
reasons) as a red flag, not a positive.

Rules:
- {UNTRUSTED_NOTE}
- Sentiment is opinion, not evidence of business quality. Say so where it matters.
- Every evidence item cites the post's fact ID(s) in `source`, e.g. "F3".
- Copy numbers exactly as written in the facts. Never calculate new numbers.
- Score 0–10 on this lens only, relative to the peers here; 5 = neutral or mixed sentiment.
- Few posts = weak signal: say so in data_gaps and lower confidence.
- This is research, not advice: never say buy, sell, invest, accumulate or avoid.
- Short, plain sentences for a retail investor.
"""


def _post_fact(p: dict) -> str:
    snippet = " ".join((p.get("text") or "").split())[:SNIPPET_CHARS]
    return (f"{(p.get('created') or '')[:10]} · r/{p['subreddit']} · ▲{p['score']} · "
            f"{p['num_comments']} comments · {p['title']}" + (f" — {snippet}" if snippet else ""))


def build_prompt(companies: list[dict], macro: dict | None = None) -> tuple[FactSheet, str]:
    sheet = FactSheet("F")
    blocks = []
    for c in companies:
        t, social = c["ticker"], c.get("social") or {}
        posts = social.get("posts") or []
        for p in posts[:POSTS_PER_COMPANY]:
            sheet.add(t, _post_fact(p), f"Reddit r/{p['subreddit']}: “{p['title']}” {p['link']}")
        blocks.append(f"== {t} — {c['market']['name']}  ({len(posts)} posts found, "
                      f"{min(len(posts), POSTS_PER_COMPANY)} shown)\n"
                      + (sheet.render(t) or "(no posts found)"))
    names = ", ".join(f"{c['ticker']} ({c['market']['name']})" for c in companies)
    prompt = (f"Compare retail investor sentiment on: {names}.\n"
              "Each line: date · subreddit · upvotes · comments · title — start of the post.\n\n"
              + "\n\n".join(blocks)
              + "\n\nReturn one assessment per company, using the tickers exactly as written above.")
    return sheet, prompt


def confidence_cap(companies: list[dict]) -> tuple[float, str]:
    cap, reasons = 0.5, ["social sentiment is noisy and opinion-based"]
    thin = [c["ticker"] for c in companies if len((c.get("social") or {}).get("posts") or []) < 5]
    if thin:
        cap -= 0.1
        reasons.append(f"fewer than 5 posts for {', '.join(thin)}")
    return round(cap, 2), ", ".join(reasons)


def _skip_reason(companies: list[dict]) -> str:
    statuses = [(c.get("social") or {}).get("status", "") for c in companies]
    if any(s.startswith("unavailable") for s in statuses):
        if not (config.REDDIT_CLIENT_ID and config.REDDIT_CLIENT_SECRET):
            return "Reddit sentiment isn't available (Reddit API keys are not set up)."
        return "Reddit couldn't be reached right now, so social sentiment was left out."
    return "No recent Reddit discussion was found for these companies."


async def run_social(companies: list[dict], macro: dict | None = None) -> AgentReport:
    if not any((c.get("social") or {}).get("posts") for c in companies):
        return skipped("social", _skip_reason(companies))
    sheet, prompt = build_prompt(companies)
    cap, reason = confidence_cap(companies)
    return await run_specialist("social", SYSTEM, prompt, sheet,
                                [c["ticker"] for c in companies], cap, reason)
