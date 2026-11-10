"""Bull vs bear debate — stress-tests the specialists' findings before the judge.

Round 1 (parallel): the Bull makes the strongest honest case FOR each company,
the Bear the strongest case AGAINST each company.
Round 2 (parallel, if DEBATE_ROUNDS=2): each side rebuts the other's most
important points — and must concede points that are fair.

The debaters only see the specialists' verified output, as numbered facts
[E1], [E2] … They must cite fact IDs; code drops any argument that cites
nothing or uses a number not in the cited facts. Failures never crash the
comparison: the judge can still work from the specialists alone.

    debate = await run_debate(plan, reports)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from pydantic import BaseModel, Field

import config
from agents.common import FactSheet, advice_words, match_ticker, unmatched_numbers
from agents.llm import AIUnavailable, generate
from agents.planner import Plan
from agents.schemas import AgentReport

log = logging.getLogger("equitylens.debate")

Side = Literal["bull", "bear"]
MAX_ARGS_PER_COMPANY = 4
MAX_REBUTTALS = 4
LABELS = {"financials": "Financials", "news": "News", "social": "Social",
          "market": "Market data", "macro": "Macro & sector"}


# ---------------------------------------------------------------- AI output shapes

class ArgumentOut(BaseModel):
    ticker: str = Field(description="Ticker exactly as given, e.g. 'TCS.NS'.")
    point: str = Field(description="One argument in 1–2 plain sentences.")
    fact_ids: list[str] = Field(description="IDs of the facts it relies on, e.g. ['E3', 'E12'].")
    strength: Literal["strong", "moderate", "weak"] = Field(
        description="Honest strength: weak if it rests on low-confidence or thin evidence.")


class CaseOut(BaseModel):
    summary: str = Field(description="2–3 sentences: your overall case across the companies.")
    arguments: list[ArgumentOut] = Field(description="2–4 arguments per company, strongest first.")


class RebuttalOut(BaseModel):
    target_id: str = Field(description="ID of the opponent's argument you answer, e.g. 'BEAR-2'.")
    response: str = Field(description="Your answer in 1–2 plain sentences.")
    fact_ids: list[str] = Field(description="IDs of facts you rely on. Empty if none.")
    concedes: bool = Field(description="True if the opponent's point is fair and you accept it.")


class RebuttalsOut(BaseModel):
    rebuttals: list[RebuttalOut] = Field(description="2–4 answers to the opponent's key points.")


# ---------------------------------------------------------------- stored result

class Argument(BaseModel):
    id: str                    # BULL-1, BEAR-3 …
    side: Side
    ticker: str
    point: str
    strength: str
    fact_ids: list[str]
    sources: list[str]         # "E4 — Financials · TCS.NS · evidence: … [F9 — Yahoo …]"


class Rebuttal(BaseModel):
    side: Side                 # who is answering
    target_id: str
    response: str
    concedes: bool
    fact_ids: list[str] = []
    sources: list[str] = []


class DebateReport(BaseModel):
    status: Literal["ok", "partial", "failed", "skipped"]
    rounds_completed: int = 0
    bull_summary: str | None = None
    bear_summary: str | None = None
    arguments: list[Argument] = []
    rebuttals: list[Rebuttal] = []
    checks: list[str] = []
    error: str | None = None
    error_kind: str | None = None


# ---------------------------------------------------------------- prompts

_RULES = """Rules:
- Use only the numbered facts [E…]. They are the specialists' checked findings.
- Every argument cites the fact IDs it uses in `fact_ids`. Copy numbers exactly as written in \
the facts. Never calculate new numbers.
- Weigh the source: Financials findings are the most reliable; News is headline-only; Social \
is opinion; a low agent confidence means weaker evidence. Rate `strength` honestly.
- Give each company its own arguments, using the tickers exactly as written.
- This is research, not advice: never say buy, sell, invest, accumulate or avoid.
- Short, plain sentences for a retail investor."""

BULL_SYSTEM = f"""You are the BULL in an EquityLens research debate about 2–3 Indian listed \
companies in the same sector. For EACH company, make the strongest honest case that it is \
fundamentally strong relative to its peers: durable strengths, improving trends, sound balance \
sheet, reasonable valuation. Don't hide red flags — if one exists, you may argue why it matters \
less, citing facts. A weaker company still gets its best honest case.

{_RULES}"""

BEAR_SYSTEM = f"""You are the BEAR in an EquityLens research debate about 2–3 Indian listed \
companies in the same sector. For EACH company, make the strongest honest case for caution: \
weaknesses, deteriorating trends, risks, stretched valuation, data that is missing or \
unreliable. A stronger company still gets its most serious honest risks.

{_RULES}"""

REBUTTAL_SYSTEM = """You are the {side} in an EquityLens research debate. You have read your \
opponent's case. Answer the 2–4 opponent arguments that matter most for judging the companies.
- If an argument is fair, concede it (concedes = true) and say briefly why it matters less, or \
simply accept it. Honesty matters more than winning.
- If it is wrong, overstated or rests on weak evidence, say why, citing fact IDs.
- `target_id` must be one of the opponent's argument IDs exactly as written.
- Copy numbers exactly as written in the facts. Never calculate new numbers.
- This is research, not advice: never say buy, sell, invest, accumulate or avoid."""


def build_brief(plan: Plan, reports: list[AgentReport]) -> tuple[FactSheet, str]:
    """Turn the specialists' verified findings into numbered facts [E1]…"""
    sheet = FactSheet("E")
    lines = []
    for w in plan.warnings:
        fid = sheet.add(None, f"Comparison warning: {w}", "EquityLens planner")
        lines.append(f"[{fid}] Comparison warning: {w}")

    for r in reports:
        if r.status != "ok" or not r.findings:
            continue
        f, label = r.findings, LABELS[r.agent]
        weight = plan.weights.get(r.agent)
        lines.append(f"\n== {label} analyst (weight in final score "
                     f"{'n/a' if weight is None else f'{weight:.0%}'}, confidence {f.confidence:.0%})")
        fid = sheet.add(None, f"{label} · summary: {f.summary}", f"{label} agent")
        lines.append(f"[{fid}] summary: {f.summary}")
        for a in f.assessments:
            lines.append(f"-- {a.ticker}")
            items = ([("score", f"{a.score:.1f}/10", f"{label} agent score")]
                     + [("strength", p, f"{label} agent") for p in a.key_points]
                     + [("concern", p, f"{label} agent") for p in a.red_flags]
                     + [("evidence", e.claim, f"{label}: {e.source}") for e in a.evidence])
            for kind, text, src in items:
                fid = sheet.add(a.ticker, f"{label} · {a.ticker} · {kind}: {text}", src)
                lines.append(f"[{fid}] {kind}: {text}")
        for g in f.data_gaps:
            fid = sheet.add(None, f"{label} · data gap: {g}", f"{label} agent")
            lines.append(f"[{fid}] data gap: {g}")

    names = ", ".join(f"{c.ticker} ({c.name})" for c in plan.companies)
    brief = (f"Companies: {names}.\nSector: {plan.companies[0].sector} / "
             f"{plan.companies[0].industry}.\n" + "\n".join(lines))
    return sheet, brief


# ---------------------------------------------------------------- verification

def _check_text(text: str, ids: list[str], sheet: FactSheet, extra: list[float] = ()):
    facts = sheet.resolve(", ".join(ids))
    nums = [n for f in facts for n in f.numbers] + list(extra)
    return facts, unmatched_numbers(text, nums)


def _sources(facts) -> list[str]:
    return [f"{f.id} — {f.text} [{f.source}]" for f in facts]


def verify_case(case: CaseOut, side: Side, sheet: FactSheet,
                tickers: list[str]) -> tuple[list[Argument], list[str]]:
    args, checks, per_company = [], [], {t: 0 for t in tickers}
    uncited = mismatched = advice = 0
    for a in case.arguments:
        t = match_ticker(a.ticker, tickers)
        if t is None or per_company[t] >= MAX_ARGS_PER_COMPANY:
            continue
        facts, bad = _check_text(a.point, a.fact_ids, sheet)
        if not facts:
            uncited += 1
            continue
        if bad:
            mismatched += 1
            continue
        if advice_words(a.point):
            advice += 1
            continue
        per_company[t] += 1
        args.append(Argument(id=f"{side.upper()}-{len(args) + 1}", side=side, ticker=t,
                             point=a.point, strength=a.strength, fact_ids=[f.id for f in facts],
                             sources=_sources(facts)))
    who = side.title()
    if uncited:
        checks.append(f"{who}: removed {uncited} argument(s) that cited no facts.")
    if mismatched:
        checks.append(f"{who}: removed {mismatched} argument(s) with numbers not in the cited facts.")
    if advice:
        checks.append(f"{who}: removed {advice} argument(s) worded like investment advice.")
    for t, n in per_company.items():
        if n == 0:
            checks.append(f"{who} made no usable argument for {t}.")
    return args, checks


def verify_rebuttals(out: RebuttalsOut, side: Side, opponent: list[Argument],
                     sheet: FactSheet) -> tuple[list[Rebuttal], list[str]]:
    by_id = {a.id: a for a in opponent}
    kept, dropped = [], 0
    for r in out.rebuttals:
        target = by_id.get(r.target_id.strip().upper())
        if target is None or len(kept) >= MAX_REBUTTALS:
            dropped += 1
            continue
        target_nums = [n for f in sheet.resolve(", ".join(target.fact_ids)) for n in f.numbers]
        facts, bad = _check_text(r.response, r.fact_ids, sheet, target_nums)
        if bad or advice_words(r.response):
            dropped += 1
            continue
        kept.append(Rebuttal(side=side, target_id=target.id, response=r.response,
                             concedes=r.concedes, fact_ids=[f.id for f in facts],
                             sources=_sources(facts)))
    checks = [f"{side.title()}: removed {dropped} rebuttal(s) that were unverifiable or "
              "answered a non-existent point."] if dropped else []
    return kept, checks


# ---------------------------------------------------------------- run

async def _call(system, prompt, schema, label):
    try:
        return await generate(system, prompt, schema, label=label), None
    except AIUnavailable as e:
        log.warning("debate %s failed: %s", label, e)
        return None, e


def _case_text(args: list[Argument], summary: str | None) -> str:
    return (f"Summary: {summary}\n" if summary else "") + "\n".join(
        f"[{a.id}] ({a.ticker}, {a.strength}) {a.point}  — facts: {', '.join(a.fact_ids)}"
        for a in args)


async def run_debate(plan: Plan, reports: list[AgentReport],
                     rounds: int | None = None) -> DebateReport:
    rounds = config.DEBATE_ROUNDS if rounds is None else rounds
    if not any(r.status == "ok" for r in reports):
        return DebateReport(status="skipped", error="No specialist findings to debate.")
    tickers = [c.ticker for c in plan.companies]
    sheet, brief = build_brief(plan, reports)
    ask = (f"{brief}\n\nMake your case for each company: 2–4 arguments per company, "
           "strongest first.")

    (bull, bull_err), (bear, bear_err) = await asyncio.gather(
        _call(BULL_SYSTEM, ask, CaseOut, "debate-bull"),
        _call(BEAR_SYSTEM, ask, CaseOut, "debate-bear"))
    if bull is None and bear is None:
        e = bull_err or bear_err
        return DebateReport(status="failed", error=e.user_message, error_kind=e.kind)

    report = DebateReport(status="ok", rounds_completed=1,
                          bull_summary=bull.summary if bull else None,
                          bear_summary=bear.summary if bear else None)
    bull_args, bear_args = [], []
    if bull:
        bull_args, ch = verify_case(bull, "bull", sheet, tickers)
        report.checks += ch
    if bear:
        bear_args, ch = verify_case(bear, "bear", sheet, tickers)
        report.checks += ch
    report.arguments = bull_args + bear_args

    missing = bull_err or bear_err
    if missing:
        side = "Bull" if bull_err else "Bear"
        report.status, report.error, report.error_kind = "partial", missing.user_message, missing.kind
        report.checks.append(f"The {side} case couldn't be completed, so there was no rebuttal round.")
        return report
    if rounds < 2 or not bull_args or not bear_args:
        return report

    def rebut_prompt(opponent_name, opponent_args, opponent_summary):
        return (f"{brief}\n\n== Your opponent ({opponent_name}) argued:\n"
                f"{_case_text(opponent_args, opponent_summary)}\n\n"
                "Answer the opponent arguments that matter most.")

    (bull_reb, e1), (bear_reb, e2) = await asyncio.gather(
        _call(REBUTTAL_SYSTEM.format(side="BULL"), rebut_prompt("the Bear", bear_args, bear.summary),
              RebuttalsOut, "debate-bull-rebuttal"),
        _call(REBUTTAL_SYSTEM.format(side="BEAR"), rebut_prompt("the Bull", bull_args, bull.summary),
              RebuttalsOut, "debate-bear-rebuttal"))
    for side, out, opponent in (("bull", bull_reb, bear_args), ("bear", bear_reb, bull_args)):
        if out:
            kept, ch = verify_rebuttals(out, side, opponent, sheet)
            report.rebuttals += kept
            report.checks += ch
    if e1 or e2:
        e = e1 or e2
        report.status, report.error, report.error_kind = "partial", e.user_message, e.kind
        report.checks.append("Part of the rebuttal round couldn't be completed.")
    if bull_reb or bear_reb:
        report.rounds_completed = 2
    return report
