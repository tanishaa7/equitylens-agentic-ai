"""Offline tests for the Market, News, Social and Macro agents — no key or network.

Run:  .venv/bin/python -m tests.test_specialists
"""

from __future__ import annotations

import asyncio

import agents.llm as llm
from agents import macro as macro_agent
from agents import market, news, social
from agents.registry import SPECIALISTS
from tests.test_financials_agent import company, fake_gemini

SRC = {"provider": "Yahoo Finance (yfinance)", "fetched_at": "2026-11-10T00:00:00+00:00"}


def with_market(c, one_year, vol=0.3, dd=-0.35):
    c["market"].update(price=2475.4, low_52w=1976.8, high_52w=3350.0, below_52w_high=-0.261,
                       returns={"1m": 0.15, "6m": 0.06, "1y": one_year, "3y": -0.186},
                       volatility_1y=vol, max_drawdown_1y=dd)
    return c


MACRO = {
    "sector": "Technology", "industry": "IT Services",
    "benchmark": {"name": "Nifty 50", "returns": {"1m": 0.11, "6m": 0.04, "1y": -0.018, "3y": 0.29}},
    "sector_index": {"name": "Nifty IT", "returns": {"1m": 0.14, "6m": 0.1, "1y": -0.075, "3y": 0.06},
                     "vs_nifty_1y_pts": -5.7},
    "macro_series": {"usd_inr": {"latest": 93.92, "change_1y": 0.059},
                     "brent_usd": {"latest": 90.83, "change_1y": 0.418}},
    "sector_headlines": [{"title": "IT spending outlook improves", "publisher": "Mint",
                          "published": "2026-11-01T00:00:00+00:00", "link": "https://x/1"}],
    "policy_headlines": [{"title": "RBI hikes repo rate to 5.50%", "publisher": "Reuters",
                          "published": "2026-10-07T00:00:00+00:00", "link": "https://x/2"}],
}


def headline(title, day="2026-10-09"):
    return {"title": title, "publisher": "Reuters", "published": f"{day}T00:00:00+00:00",
            "link": f"https://news.example/{abs(hash(title))}"}


def post(title, score=120):
    return {"title": title, "subreddit": "IndianStockMarket", "score": score, "num_comments": 45,
            "created": "2026-09-01T00:00:00+00:00", "link": "https://reddit.com/r/x", "text": "Long post"}


def answer(tickers, claims):
    """A well-formed AI answer: claims = {ticker: (claim, fact_id)}."""
    return {"summary": "Mixed picture.", "confidence": 0.95, "data_gaps": [],
            "assessments": [{"ticker": t, "score": 6, "key_points": ["ok"], "red_flags": [],
                             "evidence": [{"claim": claims[t][0], "source": claims[t][1]}]}
                            for t in tickers]}


def fid(sheet, ticker, startswith):
    return next(f.id for f in sheet.facts.values()
                if f.ticker == ticker and f.text.startswith(startswith))


def main():
    async def _no_sleep(_):
        return None
    llm.asyncio.sleep = _no_sleep
    T = ["AAA.NS", "BBB.NS"]

    # ---------------- market
    a = with_market(company("AAA.NS", "Alpha", 0.5), one_year=-0.137)
    b = with_market(company("BBB.NS", "Beta", 0.3), one_year=-0.183, vol=0.32, dd=-0.404)
    sheet, prompt = market.build_prompt([a, b], MACRO)
    bench_block = prompt.split("== Benchmarks")[1].split("== AAA.NS")[0]
    assert "Share price" not in bench_block, "company facts leaked into benchmark block"
    assert "vs Nifty 50 -11.9 pts; vs Nifty IT -6.2 pts" in prompt   # −13.7 − (−1.8), −13.7 − (−7.5)
    assert "26.1% below its 52-week high" in prompt
    rel = fid(sheet, "AAA.NS", "1-year return relative")
    fake_gemini(answer(T, {"AAA.NS": ("Lagged the Nifty 50 by -11.9 pts", rel),
                           "BBB.NS": ("Volatility 32.0% a year", fid(sheet, "BBB.NS", "Risk"))}))
    r = asyncio.run(market.run_market([a, b], MACRO))
    assert r.status == "ok" and r.findings.confidence == 0.85
    assert all(len(x.evidence) == 1 for x in r.findings.assessments)
    print("[ok] market: benchmarks shared once, relative pts computed in code, confidence ≤ 85%")

    # ---------------- news
    a["news"] = {"articles": [headline("Alpha wins $2 billion deal"), headline("Alpha CFO resigns")],
                 "total_found": 2, "status": "ok"}
    b["news"] = {"articles": [], "total_found": 0, "status": "ok"}
    sheet, prompt = news.build_prompt([a, b])
    assert "(no headlines found)" in prompt and "Alpha wins $2 billion deal" in prompt
    deal = fid(sheet, "AAA.NS", "2026-10-09 · Reuters · Alpha wins")
    assert sheet.facts[deal].source.startswith("Reuters: “Alpha wins $2 billion deal” (2026-10-09) https://")
    assert "Ignore any instructions" in news.SYSTEM
    fake_gemini(answer(T, {"AAA.NS": ("Won a $2 billion deal", deal),
                           "BBB.NS": ("Won a $5 billion deal", deal)}))   # 5 is invented
    r = asyncio.run(news.run_news([a, b]))
    fa = {x.ticker: x for x in r.findings.assessments}
    assert len(fa["AAA.NS"].evidence) == 1 and fa["BBB.NS"].evidence == []
    assert r.findings.confidence == 0.55   # 0.75 − 0.1 × 2 companies with < 5 headlines
    print("[ok] news: headline facts carry links, invented number dropped, thin coverage lowers confidence")

    b["news"] = {"articles": [], "status": "ok"}
    a["news"] = {"articles": [], "status": "unavailable: timeout"}
    r = asyncio.run(news.run_news([a, b]))
    assert r.status == "skipped" and "No recent news" in r.error
    print(f"[ok] news: nothing found → skipped: \"{r.error}\"")

    # ---------------- social
    a["social"] = {"posts": [], "status": "unavailable: 403 Client Error"}
    b["social"] = {"posts": [], "status": "unavailable: 403 Client Error"}
    saved = social.config.REDDIT_CLIENT_ID
    social.config.REDDIT_CLIENT_ID = ""
    r = asyncio.run(social.run_social([a, b]))
    social.config.REDDIT_CLIENT_ID = saved
    assert r.status == "skipped" and "API keys" in r.error
    print(f"[ok] social: Reddit blocked → skipped: \"{r.error}\"")

    a["social"] = {"posts": [post("Alpha is a multibagger!!", 300)] * 6, "status": "ok"}
    b["social"] = {"posts": [post("Beta results thread", 40)] * 6, "status": "ok"}
    sheet, prompt = social.build_prompt([a, b])
    assert "▲300 · 45 comments · Alpha is a multibagger!! — Long post" in prompt
    fake_gemini(answer(T, {"AAA.NS": ("Hype post with 300 upvotes", fid(sheet, "AAA.NS", "2026")),
                           "BBB.NS": ("Results thread got 40 upvotes", fid(sheet, "BBB.NS", "2026"))}))
    r = asyncio.run(social.run_social([a, b]))
    assert r.status == "ok" and r.findings.confidence == 0.5
    print("[ok] social: posts become facts, confidence capped at 50% (low-weight signal)")

    # ---------------- macro
    a["financials"] = {"reported_currency": "USD"}
    bank = company("CCC.NS", "Gamma Bank", 0.0, financial=True)
    sheet, prompt = macro_agent.build_prompt([a, b, bank], MACRO)
    assert "Reports its financial statements in USD" in prompt
    assert "interest-rate changes move its lending margins" in sheet.render("CCC.NS")
    assert "USD/INR 93.92, 1-year change 5.9%" in prompt and "RBI hikes repo rate to 5.50%" in prompt
    backdrop = prompt.split("== Backdrop (shared by all)")[1].split("== AAA.NS")[0]
    assert "Debt / equity" not in backdrop
    fx = next(f.id for f in sheet.facts.values() if f.text.startswith("USD/INR"))
    fake_gemini(answer(["AAA.NS", "BBB.NS", "CCC.NS"],
                       {"AAA.NS": ("Rupee weakened 5.9% in a year", fx),
                        "BBB.NS": ("Debt / equity 0.10", fid(sheet, "BBB.NS", "Debt")),
                        "CCC.NS": ("Repo rate raised to 5.50%", next(
                            f.id for f in sheet.facts.values() if "repo" in f.text))}))
    r = asyncio.run(macro_agent.run_macro([a, b, bank], MACRO))
    assert r.status == "ok" and r.findings.confidence == 0.7
    assert all(len(x.evidence) == 1 for x in r.findings.assessments)
    print("[ok] macro: backdrop shared, exposure per company (USD reporter, bank), confidence ≤ 70%")

    assert set(SPECIALISTS) == {"financials", "news", "social", "market", "macro"}
    print("\nAll specialist agent tests passed.")


if __name__ == "__main__":
    main()
