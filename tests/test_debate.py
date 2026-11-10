"""Offline tests for the bull vs bear debate — fake Gemini, no key or network.

Run:  .venv/bin/python -m tests.test_debate
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from google.genai import errors

import agents.llm as llm
from agents.debate import build_brief, run_debate
from agents.planner import Company, Plan
from agents.schemas import AgentFindings, AgentReport, CompanyAssessment, Evidence

T = ["AAA.NS", "BBB.NS"]


def plan() -> Plan:
    cs = [Company(input=t, ticker=t, name=n, sector="Technology", industry="IT", exchange="NSE",
                  market_cap=1e12, is_financial=False) for t, n in zip(T, ["Alpha", "Beta"])]
    return Plan(ok=True, companies=cs, warnings=["Alpha is about 25 times larger than Beta."],
                agents=["financials", "news"], weights={"financials": 0.67, "news": 0.33})


def reports() -> list[AgentReport]:
    fin = AgentFindings(
        summary="Alpha earns more on capital.", confidence=0.8, data_gaps=["No FY27 annual data."],
        assessments=[
            CompanyAssessment(ticker="AAA.NS", score=7.5, key_points=["ROCE 58.5% in FY26"],
                              red_flags=["Receivable days rose to 79 days"],
                              evidence=[Evidence(claim="ROCE 58.5% in FY26",
                                                 source="F9 — Yahoo Finance annual statements")]),
            CompanyAssessment(ticker="BBB.NS", score=5.0, key_points=["Revenue growth 15.3% in FY26"],
                              red_flags=["ROCE 41.1% in FY26"], evidence=[])])
    news = AgentFindings(summary="Mixed headlines.", confidence=0.6, data_gaps=[], assessments=[
        CompanyAssessment(ticker=t, score=5, key_points=[], red_flags=[], evidence=[]) for t in T])
    return [AgentReport(agent="financials", status="ok", findings=fin),
            AgentReport(agent="news", status="ok", findings=news),
            AgentReport(agent="market", status="failed", error="busy")]


def fid(sheet, contains):
    return next(f.id for f in sheet.facts.values() if contains in f.text)


def fake_gemini(responses: dict):
    """Route by role: bull / bear opening case, bull-reb / bear-reb rebuttals."""
    calls = []

    async def gen(model, contents, config):
        sys = config.system_instruction
        key = ("bull" if "BULL" in sys else "bear") + ("-reb" if "opponent's case" in sys else "")
        calls.append((key, contents))
        out = responses[key]
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(parsed=None, text=json.dumps(out), usage_metadata=None)

    llm._get_client = lambda: SimpleNamespace(aio=SimpleNamespace(
        models=SimpleNamespace(generate_content=gen)))
    llm._semaphore = None
    llm._cooldown_until.clear()
    return calls


def daily():
    return errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
        "message": "Quota exceeded", "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}})


def busy():
    return errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "x"}})


def main():
    async def _no_sleep(_):
        return None
    llm.asyncio.sleep = _no_sleep

    # ---------- brief
    sheet, brief = build_brief(plan(), reports())
    assert "Financials analyst (weight in final score 67%, confidence 80%)" in brief
    assert "Market" not in brief, "failed agents must not be debated"
    roce, rec = fid(sheet, "AAA.NS · evidence: ROCE 58.5%"), fid(sheet, "Receivable days")
    growth, warn = fid(sheet, "Revenue growth 15.3%"), fid(sheet, "25 times larger")
    gap = fid(sheet, "data gap: No FY27")
    assert "F9 — Yahoo Finance" in sheet.facts[roce].source
    print(f"[ok] brief: {len(sheet.facts)} facts from specialist findings, weights + confidence "
          "shown, failed agents left out")

    # ---------- full two-round debate
    bull = {"summary": "Alpha is the quality pick; Beta is growing faster.", "arguments": [
        {"ticker": "AAA.NS", "point": "ROCE of 58.5% shows very efficient use of capital.",
         "fact_ids": [roce], "strength": "strong"},
        {"ticker": "AAA.NS", "point": "ROCE of 72% is the best in India.",          # invented
         "fact_ids": [roce], "strength": "strong"},
        {"ticker": "BBB", "point": "Revenue grew 15.3% in FY26.", "fact_ids": [growth],
         "strength": "moderate"},
        {"ticker": "BBB.NS", "point": "Great brand.", "fact_ids": [], "strength": "weak"},  # uncited
        {"ticker": "ZZZ.NS", "point": "Unrelated.", "fact_ids": [growth], "strength": "weak"},
    ]}
    bear = {"summary": "Alpha's collections are slowing; Beta earns less on capital.", "arguments": [
        {"ticker": "AAA.NS", "point": "Receivable days rose to 79 days, so cash comes in slower.",
         "fact_ids": [rec], "strength": "moderate"},
        {"ticker": "BBB.NS", "point": "The comparison is lopsided: Alpha is 25 times larger.",
         "fact_ids": [warn, gap], "strength": "moderate"},
    ]}
    bull_reb = {"rebuttals": [
        {"target_id": "BEAR-1", "response": "Fair, 79 days is a real slowdown but cash conversion "
         "is still solid.", "fact_ids": [], "concedes": True},
        {"target_id": "BEAR-9", "response": "No such point.", "fact_ids": [], "concedes": False},
    ]}
    bear_reb = {"rebuttals": [
        {"target_id": "bull-1", "response": "58.5% is high but it may not last.", "fact_ids": [],
         "concedes": False},
        {"target_id": "BULL-2", "response": "Growth of 99% is fake.", "fact_ids": [],
         "concedes": False},                                                        # invented number
    ]}
    calls = fake_gemini({"bull": bull, "bear": bear, "bull-reb": bull_reb, "bear-reb": bear_reb})
    d = asyncio.run(run_debate(plan(), reports(), rounds=2))
    assert d.status == "ok" and d.rounds_completed == 2 and len(calls) == 4
    ids = [a.id for a in d.arguments]
    assert ids == ["BULL-1", "BULL-2", "BEAR-1", "BEAR-2"], ids
    assert d.arguments[1].ticker == "BBB.NS"
    assert d.arguments[0].sources[0].startswith(f"{roce} — Financials · AAA.NS · evidence")
    assert [(r.side, r.target_id, r.concedes) for r in d.rebuttals] == [
        ("bull", "BEAR-1", True), ("bear", "BULL-1", False)]
    reb_prompt = next(c for k, c in calls if k == "bear-reb")
    assert "[BULL-1] (AAA.NS, strong) ROCE of 58.5%" in reb_prompt
    print("[ok] 2 rounds: invented / uncited / unknown-company arguments dropped,")
    print("     rebuttals checked (fake target and invented number dropped, concession kept)")
    for ch in d.checks:
        print(f"       check: {ch}")

    # ---------- one round only
    calls = fake_gemini({"bull": bull, "bear": bear})
    d = asyncio.run(run_debate(plan(), reports(), rounds=1))
    assert d.rounds_completed == 1 and len(calls) == 2 and d.rebuttals == []
    print("[ok] DEBATE_ROUNDS=1 → opening cases only (2 AI calls)")

    # ---------- one side fails
    calls = fake_gemini({"bull": bull, "bear": busy()})
    d = asyncio.run(run_debate(plan(), reports(), rounds=2))
    assert d.status == "partial" and all(a.side == "bull" for a in d.arguments)
    assert not any(k.endswith("-reb") for k, _ in calls)
    print("[ok] Bear fails → partial: Bull case kept, no rebuttal round")

    # ---------- both fail
    fake_gemini({"bull": daily(), "bear": daily()})
    d = asyncio.run(run_debate(plan(), reports()))
    assert d.status == "failed" and d.error_kind == "rate_limit_daily"
    print(f"[ok] daily limit → debate failed with message:\n     \"{d.error}\"")

    d = asyncio.run(run_debate(plan(), [AgentReport(agent="news", status="failed", error="x")]))
    assert d.status == "skipped"
    print("[ok] no specialist findings → debate skipped (no AI call)")

    print("\nAll Debate tests passed.")


if __name__ == "__main__":
    main()
