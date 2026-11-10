"""Orchestrator — runs one comparison end to end.

    result = await run_comparison(["TCS", "Infosys"], on_event=print_progress)

1. Planner (no AI): resolve inputs, sector check, agents, weights.
2. Fetch data in parallel: each company + the macro backdrop, in threads.
3. Run the planned specialist agents in parallel (the Gemini wrapper limits
   how many AI calls run at once).
4. Never crash: an agent that errors or times out becomes a "failed" report
   and the rest carry on. If the AI is unusable for everyone (daily limit hit,
   bad key) the remaining agents are cancelled and ONE clear message is shown.
5. Decide whether there's enough to judge: Financials must succeed, plus at
   least one other agent.
6. Bull vs bear debate on the specialists' findings (if there's enough to
   judge). A failed debate doesn't stop the run.
7. Judge: scores and edges computed in code, verdict explained by the AI.
   If the AI is unusable by now (daily limit / bad key), or the judge's AI
   call fails, the user still gets a verdict built from the numbers.
8. Cache + log: a complete result is reused for the same companies for the
   rest of the day (refresh=True forces a new run); every run is logged.
"""

from __future__ import annotations

import asyncio
import logging
import time

import config
from typing import Callable, Literal

from pydantic import BaseModel

from agents import llm
from agents.debate import DebateReport, run_debate
from agents.judge import Verdict, run_judge
from agents.planner import Plan, make_plan
from agents.registry import SPECIALISTS
from agents.schemas import AgentReport
import runlog
from data import store
from data.collect import collect_company, macro_for
from data.common import now_iso

log = logging.getLogger("equitylens.orchestrator")

AGENT_TIMEOUT = 240          # seconds per agent, including its retries
FATAL_KINDS = {"rate_limit_daily", "config"}   # retrying other agents can't help
RETRY_KINDS = {"busy", "rate_limit_minute"}   # temporary — worth one more go for Financials
RETRY_PAUSE = 8              # seconds before re-trying Financials, once the other agents are done
CACHE_VERSION = 2            # bump when scoring / prompts change, so old results aren't reused
RESULT_TTL = 24 * 3600       # the key also contains today's IST date → reused for that day only
LABELS = {"financials": "Financials", "news": "News", "social": "Social",
          "market": "Market data", "macro": "Macro & sector"}

Status = Literal["ok", "partial", "blocked", "data_error", "ai_unavailable", "insufficient",
                 "limited"]
GateFn = Callable[[], "str | None"]   # None = go ahead; a message = usage limit reached
EventFn = Callable[[str, str], None]   # (stage, detail) → e.g. update a progress bar


class ComparisonRun(BaseModel):
    status: Status
    user_message: str | None = None   # one banner for the user, if anything went wrong
    can_judge: bool = False           # enough agents succeeded for a fair verdict
    plan: Plan
    reports: list[AgentReport] = []
    debate: DebateReport | None = None
    verdict: Verdict | None = None
    companies: list[dict] = []        # raw data per company (for tables/charts in the UI)
    macro: dict | None = None
    timings: dict[str, float] = {}
    usage: list[dict] = []            # AI tokens used by this run
    started_at: str = ""
    from_cache: bool = False          # True = today's saved result was reused (no AI used)


def _emit(on_event: EventFn | None, stage: str, detail: str = ""):
    if on_event:
        try:
            on_event(stage, detail)
        except Exception:   # a broken progress display must never stop the run
            log.exception("progress callback failed")


# ---------------------------------------------------------------- data

async def _fetch_data(plan: Plan, on_event) -> tuple[list[dict], dict | None]:
    parts = frozenset(plan.data_parts)

    async def one(ticker: str) -> dict:
        c = await asyncio.to_thread(collect_company, ticker, parts)
        _emit(on_event, "data", f"{ticker} ready")
        return c

    first = plan.companies[0]
    jobs = [one(c.ticker) for c in plan.companies]
    if plan.needs_macro:
        jobs.append(asyncio.to_thread(macro_for, first.sector, first.industry))
    results = await asyncio.gather(*jobs)
    macro = results.pop() if plan.needs_macro else None
    if plan.needs_macro:
        _emit(on_event, "data", "sector & economy ready")
    return list(results), macro


# ---------------------------------------------------------------- agents

async def _run_one(name: str, companies: list[dict], macro: dict | None) -> AgentReport:
    try:
        report = await asyncio.wait_for(SPECIALISTS[name].run(companies, macro), AGENT_TIMEOUT)
    except asyncio.TimeoutError:
        report = AgentReport(agent=name, status="failed", error_kind="busy",
                             error=llm.user_message("busy"))
    except Exception as e:   # a bug in one agent must not sink the comparison
        log.exception("%s agent crashed", name)
        report = AgentReport(agent=name, status="failed", error_kind="error",
                             error=llm.user_message("error"), checks=[f"internal error: {e!r}"[:200]])
    return report


async def _run_agents(plan: Plan, companies, macro, on_event) -> tuple[list[AgentReport], str | None]:
    """Run agents in parallel. On a fatal AI error, cancel the rest."""
    tasks = {asyncio.create_task(_run_one(a, companies, macro)): a for a in plan.agents}
    reports: dict[str, AgentReport] = {}
    fatal: AgentReport | None = None

    for done in asyncio.as_completed(list(tasks)):
        r = await done
        reports[r.agent] = r
        _emit(on_event, "agent", f"{LABELS[r.agent]}: {r.status}")
        if r.status == "failed" and r.error_kind in FATAL_KINDS:
            fatal = r
            break

    if fatal:
        for t, name in tasks.items():
            if not t.done():
                t.cancel()
            if name not in reports:
                reports[name] = AgentReport(agent=name, status="failed", error=fatal.error,
                                            error_kind=fatal.error_kind)
        await asyncio.gather(*tasks, return_exceptions=True)

    fin = reports.get("financials")
    if not fatal and fin and fin.status == "failed" and fin.error_kind in RETRY_KINDS:
        # Financials is required for a verdict — give it one more go when the load has dropped
        _emit(on_event, "agent", "Financials: retrying (AI was busy)")
        await asyncio.sleep(RETRY_PAUSE)
        reports["financials"] = await _run_one("financials", companies, macro)
        _emit(on_event, "agent", f"Financials: {reports['financials'].status}")

    ordered = [reports[a] for a in plan.agents]
    ordered += [AgentReport(agent=a, status="skipped", error=why) for a, why in plan.skipped.items()]
    return ordered, (fatal.error if fatal else None)


def _verdict(reports: list[AgentReport]) -> tuple[Status, bool, str | None]:
    ok = {r.agent for r in reports if r.status == "ok"}
    failed = [r for r in reports if r.status == "failed"]
    can_judge = "financials" in ok and len(ok) >= 2
    if not ok:
        msg = failed[0].error if failed else "No analysis could be completed. Please try again."
        return "ai_unavailable", False, msg
    if not can_judge:
        return "insufficient", False, ("Not enough of the analysis finished to compare these "
                                       "companies fairly. Please try again in a few minutes.")
    if failed:
        names = ", ".join(LABELS[r.agent] for r in failed)
        return "partial", True, (f"Some parts of the analysis couldn't finish ({names}). "
                                 "The comparison below uses the rest.")
    return "ok", True, None


# ---------------------------------------------------------------- main

def result_key(plan: Plan, allow_cross_sector: bool) -> str:
    """Same companies (any order, any spelling) + same day + same settings → same key."""
    tickers = ",".join(sorted(c.ticker for c in plan.companies))
    return (f"v{CACHE_VERSION}|{store.today_ist()}|{tickers}|cross={allow_cross_sector}"
            f"|rounds={config.DEBATE_ROUNDS}|agents={','.join(plan.agents)}")


async def run_comparison(inputs: list[str], allow_cross_sector: bool = False,
                         on_event: EventFn | None = None, refresh: bool = False,
                         gate: GateFn | None = None) -> ComparisonRun:
    """refresh=True ignores today's saved result and runs everything again.
    gate() is asked only when a NEW comparison is needed (saved results are free);
    the website uses it for per-visitor / per-day limits."""
    try:
        run = await _run_comparison(inputs, allow_cross_sector, on_event, refresh, gate)
    finally:
        await llm.aclose()
    runlog.log_run(run, inputs)
    return run


async def _run_comparison(inputs: list[str], allow_cross_sector: bool,
                          on_event: EventFn | None, refresh: bool,
                          gate: GateFn | None = None) -> ComparisonRun:
    started, t0 = now_iso(), time.perf_counter()
    usage_start = len(llm.usage_log)
    timings: dict[str, float] = {}

    _emit(on_event, "plan", "checking companies")
    plan = await asyncio.to_thread(make_plan, inputs, allow_cross_sector)
    timings["plan"] = round(time.perf_counter() - t0, 1)
    if not plan.ok:
        return ComparisonRun(status="blocked", user_message=" ".join(plan.blocking), plan=plan,
                             timings=timings, started_at=started)
    _emit(on_event, "plan", ", ".join(c.ticker for c in plan.companies))

    key = result_key(plan, allow_cross_sector)
    if not refresh:
        saved = store.get("result", key, RESULT_TTL)
        if saved:
            try:
                run = ComparisonRun.model_validate(saved)
                run.from_cache = True
                _emit(on_event, "cache", "using today's saved result (no AI used)")
                return run
            except Exception:   # an old/broken file is just a miss
                log.warning("ignoring unreadable cached result", exc_info=True)

    limit_msg = gate() if gate else None
    if limit_msg:
        return ComparisonRun(status="limited", user_message=limit_msg, plan=plan,
                             timings=timings, started_at=started)

    t1 = time.perf_counter()
    try:
        companies, macro = await _fetch_data(plan, on_event)
    except Exception:
        log.exception("data fetch failed")
        return ComparisonRun(status="data_error", plan=plan, timings=timings, started_at=started,
                             user_message="Couldn't fetch market data right now. "
                                          "Please check your connection and try again.")
    missing = [c["ticker"] for c in companies if "error" in c]
    timings["data"] = round(time.perf_counter() - t1, 1)
    if missing:
        return ComparisonRun(status="data_error", plan=plan, timings=timings, started_at=started,
                             user_message=f"Couldn't fetch data for {', '.join(missing)}. "
                                          "Please try again.")

    t2 = time.perf_counter()
    reports, fatal_msg = await _run_agents(plan, companies, macro, on_event)
    timings["agents"] = round(time.perf_counter() - t2, 1)

    status, can_judge, msg = _verdict(reports)
    if fatal_msg:
        status, can_judge, msg = "ai_unavailable", False, fatal_msg

    debate = None
    if can_judge:
        _emit(on_event, "debate", "bull vs bear")
        t3 = time.perf_counter()
        try:
            debate = await run_debate(plan, reports)
        except Exception as e:   # never let the debate sink a finished analysis
            log.exception("debate crashed")
            debate = DebateReport(status="failed", error_kind="error",
                                  error=llm.user_message("error"), checks=[f"internal error: {e!r}"[:200]])
        timings["debate"] = round(time.perf_counter() - t3, 1)
        _emit(on_event, "debate", debate.status)

    verdict = None
    if can_judge:
        _emit(on_event, "judge", "weighing the evidence")
        t4 = time.perf_counter()
        ai_ok = debate is None or debate.error_kind not in FATAL_KINDS
        skip = None if ai_ok else debate.error
        try:
            verdict = await run_judge(plan, companies, reports, debate, use_ai=ai_ok, skip_reason=skip)
        except Exception:
            log.exception("judge crashed")
            try:   # numbers-only verdict needs no AI and rarely fails
                verdict = await run_judge(plan, companies, reports, debate, use_ai=False,
                                          skip_reason=llm.user_message("error"))
            except Exception:
                log.exception("fallback verdict failed")
        timings["judge"] = round(time.perf_counter() - t4, 1)
        _emit(on_event, "judge", verdict.status if verdict else "failed")
        if verdict is None:
            status, msg = "partial", "The final verdict couldn't be produced. The analysis is shown below."
        elif verdict.status == "fallback":
            status = "partial"
            msg = ((verdict.note + " " if verdict.note else "")
                   + "The verdict below is built from the numbers only, without the written explanation.")

    timings["total"] = round(time.perf_counter() - t0, 1)
    _emit(on_event, "done", status)
    run = ComparisonRun(status=status, user_message=msg, can_judge=can_judge, plan=plan,
                        reports=reports, debate=debate, verdict=verdict, companies=companies,
                        macro=macro, timings=timings, usage=llm.usage_log[usage_start:],
                        started_at=started)
    if status == "ok" and verdict is not None and verdict.status == "ok":
        store.put("result", key, run.model_dump(mode="json"))   # only complete results are reused
    return run


def run_comparison_sync(*args, **kwargs) -> ComparisonRun:
    return asyncio.run(run_comparison(*args, **kwargs))
