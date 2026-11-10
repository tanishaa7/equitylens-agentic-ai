"""Offline tests for the Judge — fake Gemini, no key or network.

Run:  .venv/bin/python -m tests.test_judge
"""

from __future__ import annotations

import asyncio

import agents.llm as llm
from agents.debate import Argument, DebateReport
from agents.judge import (LENSES, lens_edges, lens_table, overall_confidence, run_judge,
                          weighted_scores)
from agents.planner import Company, Plan
from agents.schemas import AgentFindings, AgentReport, CompanyAssessment
from tests.test_debate import daily
from tests.test_financials_agent import company, fake_gemini

T = ["AAA.NS", "BBB.NS"]


def plan(weights=None) -> Plan:
    cs = [Company(input=t, ticker=t, name=n, sector="Technology", industry="IT", exchange="NSE",
                  market_cap=1e12, is_financial=False) for t, n in zip(T, ["Alpha Tech", "Beta Tech"])]
    w = weights or {"financials": 0.5, "news": 0.25, "market": 0.25}
    return Plan(ok=True, companies=cs, agents=list(w), weights=w)


def rep(agent, a, b, conf=0.8, flags=()):
    return AgentReport(agent=agent, status="ok", findings=AgentFindings(
        summary="s", confidence=conf, data_gaps=[], assessments=[
            CompanyAssessment(ticker="AAA.NS", score=a, key_points=[], red_flags=list(flags), evidence=[]),
            CompanyAssessment(ticker="BBB.NS", score=b, key_points=[], red_flags=[], evidence=[])]))


def companies():
    a = company("AAA.NS", "Alpha Tech", 0.50)
    b = company("BBB.NS", "Beta Tech", 0.30)
    a["ratios"]["growth"] = {"revenue_cagr_3y": 0.06, "profit_cagr_3y": 0.05, "cagr_period": "FY23–FY26"}
    b["ratios"]["growth"] = {"revenue_cagr_3y": 0.084, "profit_cagr_3y": 0.085, "cagr_period": "FY23–FY26"}
    b["ratios"]["valuation"]["pe"] = 15.0
    a["ratios"]["latest_quarter"] = {
        "current": {"period": "Q2 FY27", "revenue": 1, "net_income": 1, "ebitda_margin": 0.27},
        "revenue_qoq": 0.013, "revenue_yoy": None, "net_income_qoq": 0.04, "net_income_yoy": None,
        "ebitda_margin_change_qoq_pts": 0.6}
    return [a, b]


def debate():
    def arg(i, side, t, point, strength):
        return Argument(id=f"{side.upper()}-{i}", side=side, ticker=t, point=point,
                        strength=strength, fact_ids=["E1"], sources=["E1 — x"])
    return DebateReport(status="ok", rounds_completed=1, arguments=[
        arg(1, "bull", "AAA.NS", "ROCE of 50.0% is far ahead.", "strong"),
        arg(2, "bull", "BBB.NS", "Faster growth.", "moderate"),
        arg(1, "bear", "AAA.NS", "Slower growth.", "moderate"),
        arg(2, "bear", "BBB.NS", "Lower returns.", "strong")])


def fid(sheet_prompt, start):
    for line in sheet_prompt.splitlines():
        if start in line:
            return line.split("]")[0].lstrip("[")
    raise KeyError(start)


def main():
    async def _no_sleep(_):
        return None
    llm.asyncio.sleep = _no_sleep
    cs = companies()

    # ---------- deterministic layer
    rows = lens_table(cs)
    by = {r.metric: r for r in rows}
    assert by["3-year revenue CAGR"].edge == "BBB.NS" and by["ROCE"].edge == "AAA.NS"
    assert by["Debt / equity"].edge is None              # 0.10 vs 0.10 → tie
    assert by["P/E"].edge == "BBB.NS" and by["P/E"].better == "lower"
    edges = lens_edges(rows)
    assert edges["Growth"] == "BBB.NS" and edges["Returns"] == "AAA.NS"   # ROE tie, ROCE → Alpha
    assert edges["Safety"] is None or edges["Safety"] in T
    print("[ok] head-to-head: edges by metric (higher/lower is better), ties within 5% → no edge")

    bank_a, bank_b = company("CCC.NS", "C Bank", 0, financial=True), company("DDD.NS", "D Bank", 0, financial=True)
    metrics = {r.metric for r in lens_table([bank_a, bank_b])}
    assert "ROCE" not in metrics and "Debt / equity" not in metrics and "ROE" in metrics
    bank_b["ratios"]["growth"] = {"revenue_cagr_3y": 0.27, "profit_cagr_3y": 0.12, "cagr_period": "x"}
    bank_a["ratios"]["growth"] = {"revenue_cagr_3y": 0.18, "profit_cagr_3y": 0.16, "cagr_period": "x"}
    bank_b["ratios"]["data_warnings"] = ["Revenue changed +65% in FY24 — may reflect a merger"]
    g = next(r for r in lens_table([bank_a, bank_b]) if r.metric == "3-year revenue CAGR")
    assert g.edge is None and g.values["DDD.NS"].endswith(" *") and "merger" in g.source
    print("[ok] banks: ROCE / D/E rows dropped; merger-inflated growth starred, no edge given")

    p = plan()
    reports = [rep("financials", 8, 5, 0.9), rep("news", 4, 8, 0.4), rep("market", 6, 6, 0.8),
               AgentReport(agent="macro", status="failed", error="busy")]
    scores, breakdown, eff = weighted_scores(p, reports)
    # eff ∝ 0.5×0.9, 0.25×0.4, 0.25×0.8 = 0.45, 0.10, 0.20 → /0.75
    assert eff == {"financials": 0.6, "news": 0.133, "market": 0.267}, eff
    assert scores == {"AAA.NS": 6.93, "BBB.NS": 5.67}, scores   # B: .6×5 + .133×8 + .267×6
    print(f"[ok] weighted score = score × weight × confidence: {scores} (low-confidence news counts less)")

    c_full = overall_confidence(p, reports[:3], debate())
    c_nodebate = overall_confidence(p, reports[:3], None)
    assert c_nodebate == round(c_full - 0.1, 2)
    print(f"[ok] overall confidence {c_full:.0%}; without a debate {c_nodebate:.0%}")

    # ---------- numbers-only verdict
    v = asyncio.run(run_judge(p, cs, reports, debate(), use_ai=False, skip_reason="daily limit"))
    assert v.status == "fallback" and v.leader == "AAA.NS" and not v.too_close
    assert v.headline.startswith("Fundamentally stronger: Alpha Tech — ahead on returns")
    assert "Beta Tech leads on growth" in v.headline and v.note == "daily limit"
    a_view = v.companies[0]
    assert a_view.bull_case.text == "ROCE of 50.0% is far ahead." and a_view.bear_case.text == "Slower growth."
    assert a_view.what_changed[0].startswith("Q2 FY27: revenue n/a vs a year ago, +1.3%")
    print(f"[ok] no AI → verdict from numbers:\n     \"{v.headline}\"")

    close = [rep("financials", 6.1, 6.0), rep("news", 5, 5), rep("market", 5, 5)]
    v = asyncio.run(run_judge(p, cs, close, None, use_ai=False))
    assert v.too_close and v.leader is None and v.headline.startswith("Too close to call")
    print("[ok] scores within 0.5 → 'too close to call', no winner named")

    # ---------- AI explanation, checked
    holder = {}

    async def capture():
        from agents.judge import build_facts
        s, b, e = weighted_scores(p, reports)
        r = lens_table(cs)
        sheet, prompt = build_facts(p, cs, reports, debate(), s, b, e, r, lens_edges(r), "AAA.NS", False)
        holder["prompt"] = prompt
    asyncio.run(capture())
    prompt = holder["prompt"]
    j_result = fid(prompt, "Computed result")
    j_roce = fid(prompt, "Returns · ROCE")
    j_growth = fid(prompt, "Growth · 3-year revenue CAGR")
    assert "Alpha Tech (AAA.NS) is fundamentally stronger" in prompt and "Bull on AAA.NS (strong)" in prompt

    ai = {
        "headline": "Fundamentally stronger: Alpha Tech — better returns on capital. But Beta Tech "
                    "is growing faster, so growth is the thing to watch.",
        "headline_fact_ids": [j_result, j_roce],
        "reasons": [
            {"text": "Alpha's ROCE of 50.0% beats Beta's 30.0%.", "fact_ids": [j_roce]},
            {"text": "Alpha's ROCE of 75.0% is world class.", "fact_ids": [j_roce]},     # invented
            {"text": "Investors should buy Alpha.", "fact_ids": [j_result]},             # advice
            {"text": "Beta grows faster.", "fact_ids": []},                             # uncited
        ],
        "watch": [{"text": "Beta's 3-year revenue CAGR of 8.4% is higher.", "fact_ids": [j_growth]}],
        "companies": [
            {"ticker": "AAA.NS", "bull_case": {"text": "High returns.", "fact_ids": [j_roce]},
             "bear_case": {"text": "Slower growth at 6.0%.", "fact_ids": [j_growth]}, "red_flags": []},
            {"ticker": "bbb", "bull_case": {"text": "Growth of 8.4%.", "fact_ids": [j_growth]},
             "bear_case": {"text": "Sell it.", "fact_ids": [j_roce]},                   # advice → keeps fallback
             "red_flags": []},
        ],
    }
    calls = fake_gemini(ai)
    v = asyncio.run(run_judge(p, cs, reports, debate()))
    assert v.status == "ok" and v.headline == ai["headline"] and len(calls) == 1
    assert [r.text for r in v.reasons] == ["Alpha's ROCE of 50.0% beats Beta's 30.0%."]
    assert v.reasons[0].sources[0].startswith(f"{j_roce} — Returns · ROCE")
    assert v.companies[1].bull_case.text == "Growth of 8.4%."
    assert v.companies[1].bear_case.text == "Lower returns."           # debate fallback kept
    print("[ok] AI verdict used; invented number, buy/sell wording and uncited points removed:")
    for ch in v.checks:
        print(f"       check: {ch}")

    wrong = {**ai, "headline": "Fundamentally stronger: Beta Tech — faster growth."}
    fake_gemini(wrong)
    v = asyncio.run(run_judge(p, cs, reports, debate()))
    assert v.headline.startswith("Fundamentally stronger: Alpha Tech")
    print("[ok] AI names a different winner than the computed result → numbers-based headline used")

    from agents.judge import unsupported_metrics
    bank_rows = lens_table([company("CCC.NS", "C Bank", 0, financial=True),
                            company("DDD.NS", "D Bank", 0, financial=True)])
    assert unsupported_metrics("better returns, lower debt", bank_rows) == ["debt"]
    assert unsupported_metrics("better returns, lower debt", lens_table(cs)) == []
    assert unsupported_metrics("higher ROE and cheaper P/B", bank_rows) == []
    print("[ok] banks: a headline claiming 'lower debt' (not available for banks) is rejected")

    close_reports = [rep("financials", 6.1, 6.0), rep("news", 5, 5), rep("market", 5, 5)]
    fake_gemini({**ai, "headline": "Fundamentally stronger: There is no clear winner here."})
    v = asyncio.run(run_judge(p, cs, close_reports, None))
    assert v.too_close and v.headline.startswith("Too close to call")
    print("[ok] too close to call but AI headline says 'stronger:' → numbers-based headline used")

    fake_gemini(daily())
    v = asyncio.run(run_judge(p, cs, reports, debate()))
    assert v.status == "fallback" and v.error_kind == "rate_limit_daily" and "free AI limit" in v.note
    print(f"[ok] judge hits the daily limit → numbers-only verdict + note:\n     \"{v.note}\"")

    assert LENSES == ["Growth", "Returns", "Profitability", "Safety", "Cash quality", "Valuation"]
    print("\nAll Judge tests passed.")


if __name__ == "__main__":
    main()
