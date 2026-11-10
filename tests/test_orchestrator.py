"""Offline tests for the Orchestrator — fake planner, data and agents.

Run:  .venv/bin/python -m tests.test_orchestrator
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

import runlog
from data import store

import agents.llm as llm
import agents.orchestrator as orch
from agents.debate import DebateReport
from agents.judge import Verdict
from agents.planner import Company, Plan
from agents.registry import Specialist
from agents.schemas import AgentFindings, AgentReport, CompanyAssessment

TICKERS = ["AAA.NS", "BBB.NS"]
AGENTS = ["financials", "news", "market", "macro"]
fetched: list[str] = []
debated: list[bool] = []
judged: list[bool] = []   # use_ai flag per judge call


def plan(ok=True) -> Plan:
    cs = [Company(input=t, ticker=t, name=t, sector="Technology", industry="IT", exchange="NSE",
                  market_cap=1e12, is_financial=False) for t in TICKERS]
    return Plan(ok=ok, companies=cs, blocking=[] if ok else ["Please pick one."],
                agents=AGENTS, skipped={"social": "No Reddit keys."},
                weights={a: 0.25 for a in AGENTS}, data_parts=["financials", "news"], needs_macro=True)


def ok_report(agent):
    f = AgentFindings(summary="fine", confidence=0.7, data_gaps=[], assessments=[
        CompanyAssessment(ticker=t, score=6, key_points=[], red_flags=[], evidence=[]) for t in TICKERS])
    return AgentReport(agent=agent, status="ok", findings=f)


def failed(agent, kind, msg="AI problem"):
    return AgentReport(agent=agent, status="failed", error=msg, error_kind=kind)


def agent(name, behaviour="ok", delay=0.0):
    calls = []

    async def run(companies, macro):
        calls.append(1)
        await asyncio.sleep(delay)
        if behaviour == "busy_once":
            return failed(name, "busy") if len(calls) == 1 else ok_report(name)
        if behaviour == "ok":
            return ok_report(name)
        if behaviour == "crash":
            raise KeyError("by_year")
        return failed(name, behaviour, f"{behaviour} message")
    return Specialist(None, run, frozenset(), False)


def setup(behaviours: dict, plan_ok=True, data_fails=False, debate="ok", judge="ok"):
    fetched.clear()
    debated.clear()

    async def fake_debate(plan, reports):
        debated.append(True)
        if debate == "crash":
            raise ValueError("bad debate")
        if debate == "daily":
            return DebateReport(status="failed", error="daily limit message",
                                error_kind="rate_limit_daily")
        return DebateReport(status="ok", rounds_completed=2)
    orch.run_debate = fake_debate
    judged.clear()

    async def fake_judge(plan, companies, reports, debate, use_ai=True, skip_reason=None):
        judged.append(use_ai)
        if judge == "crash" and use_ai:
            raise RuntimeError("judge bug")
        status = "ok" if use_ai and judge == "ok" else "fallback"
        return Verdict(status=status, note=skip_reason or ("AI busy" if status == "fallback" else None),
                       leader=None, too_close=True, scores={}, score_breakdown={},
                       effective_weights={}, lens_table=[], lens_edges={}, confidence=0.5,
                       confidence_label="Medium", headline="h")
    orch.run_judge = fake_judge
    orch.make_plan = lambda inputs, allow: plan(plan_ok)

    def collect(ticker, parts):
        if data_fails:
            raise ConnectionError("no network")
        fetched.append(ticker)
        return {"ticker": ticker, "market": {}}
    orch.collect_company = collect
    orch.macro_for = lambda sector, industry: {"sector": sector}
    orch.SPECIALISTS = {a: agent(a, *behaviours.get(a, ("ok",))) for a in AGENTS}


def run(**kw):
    return orch.run_comparison_sync(TICKERS, **kw)


def main():
    store.ENABLED = False                              # caching has its own tests
    runlog.LOG_DIR = Path(tempfile.mkdtemp())          # don't write test runs to logs/
    events = []
    setup({})
    r = run(on_event=lambda s, d: events.append(s))
    assert r.status == "ok" and r.can_judge and r.user_message is None
    assert [x.agent for x in r.reports] == AGENTS + ["social"] and r.reports[-1].status == "skipped"
    assert sorted(fetched) == TICKERS and r.macro == {"sector": "Technology"}
    assert events[0] == "plan" and events[-1] == "done" and events.count("agent") == 4
    assert r.debate.status == "ok" and events.count("debate") == 2 and "debate" in r.timings
    assert judged == [True] and r.verdict.status == "ok" and events.count("judge") == 2
    print("[ok] all agents succeed → debate → judge, status ok; Social listed as skipped")

    setup({"news": ("crash",)})
    r = run()
    news = next(x for x in r.reports if x.agent == "news")
    assert r.status == "partial" and r.can_judge and news.status == "failed"
    assert "KeyError" in news.checks[0] and "News" in r.user_message
    print(f"[ok] one agent crashes → others continue, status partial:\n     \"{r.user_message}\"")

    orch.RETRY_PAUSE = 0
    setup({"financials": ("busy_once",)})
    r = run(on_event=lambda s, d: events.append(d))
    assert r.status == "ok" and r.can_judge and "Financials: retrying (AI was busy)" in events
    print("[ok] Financials busy → retried once after the others finish → comparison completes")

    setup({"financials": ("busy",)})
    r = run()
    assert r.status == "insufficient" and not r.can_judge
    assert debated == [] and r.debate is None and judged == [] and r.verdict is None
    print("[ok] Financials fails → not enough to judge fairly, no debate or judge (saves AI quota)")

    setup({}, debate="crash")
    r = run()
    assert r.status == "ok" and r.can_judge and r.debate.status == "failed"
    print("[ok] debate crashes → analysis kept, judge can still use the specialists")

    setup({}, debate="daily")
    r = run()
    assert judged == [False], "judge must not call the AI after a daily-limit error"
    assert r.status == "partial" and r.verdict.status == "fallback"
    assert r.user_message.startswith("daily limit message") and "numbers only" in r.user_message
    print(f"[ok] daily limit during debate → verdict still shown, built from numbers:\n     \"{r.user_message}\"")

    setup({}, judge="busy")
    r = run()
    assert r.status == "partial" and r.verdict.status == "fallback" and "numbers only" in r.user_message
    print("[ok] judge's AI call fails → numbers-only verdict, status partial")

    setup({}, judge="crash")
    r = run()
    assert judged == [True, False] and r.verdict.status == "fallback" and r.status == "partial"
    print("[ok] judge crashes → retried without AI, verdict still shown")

    setup({"financials": ("rate_limit_daily", 0.0), "news": ("ok", 3.0), "market": ("ok", 3.0),
           "macro": ("ok", 3.0)})
    t = time.perf_counter()
    r = run()
    took = time.perf_counter() - t
    assert took < 1.5, f"slow agents were not cancelled ({took:.1f}s)"
    assert r.status == "ai_unavailable" and r.user_message == "rate_limit_daily message"
    assert all(x.status == "failed" for x in r.reports if x.agent != "social")
    print(f"[ok] daily limit → other agents cancelled in {took:.1f}s, one message for the user")

    orch.AGENT_TIMEOUT = 0.2
    setup({"market": ("ok", 2.0)})
    r = run()
    orch.AGENT_TIMEOUT = 240
    m = next(x for x in r.reports if x.agent == "market")
    assert m.status == "failed" and m.error_kind == "busy" and r.status == "partial"
    print("[ok] agent takes too long → timed out as 'busy', rest of the comparison continues")

    setup({}, plan_ok=False)
    r = run()
    assert r.status == "blocked" and fetched == [] and r.user_message == "Please pick one."
    print("[ok] blocked plan → nothing fetched, no AI used")

    setup({}, data_fails=True)
    r = run()
    assert r.status == "data_error" and "try again" in r.user_message
    print("[ok] data fetch fails → friendly data error")

    def broken(stage, detail):
        raise RuntimeError("UI glitch")
    setup({})
    assert run(on_event=broken).status == "ok"
    print("[ok] a broken progress display never stops the run")

    async def grab():
        return llm._get_semaphore()
    s1, s2 = asyncio.run(grab()), asyncio.run(grab())
    assert s1 is not s2
    print("[ok] back-to-back runs get their own AI limiter (no 'different event loop' crash)")

    print("\nAll Orchestrator tests passed.")


if __name__ == "__main__":
    main()
