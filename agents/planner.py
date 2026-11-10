"""Planner — checks the request before any data fetching or AI (uses no AI quota).

1. Resolve each input to an NSE/BSE company: exact symbol first ('TCS', 'M&M'),
   then name search ('kalyan jewellers').
2. Block with a clear message: not 2–3 companies, not found, ambiguous name,
   the same company twice, or companies in different sectors.
3. Warn (but allow): same sector but different industry, a lender next to a
   non-lender, very different company sizes.
4. Decide which agents run (Social is skipped without Reddit keys), what data
   to fetch, and the weight each agent gets in the final score.

    plan = make_plan(["TCS", "Infosys"])
    if not plan.ok: show plan.blocking (and plan.problems[i].candidates to pick from)
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel

import config
from agents.registry import SPECIALISTS
from data import lookup
from data.ratios import is_financial

# How much each lens counts in the final score (the judge uses these in Step 8).
# Social is low by design: opinion, not evidence.
WEIGHTS = {"financials": 0.40, "news": 0.20, "market": 0.15, "macro": 0.15, "social": 0.10}
SIZE_GAP_WARN = 20          # warn when the biggest company is 20x+ the smallest
_SYMBOL_RE = re.compile(r"[A-Za-z0-9&\-.^]+")


class Company(BaseModel):
    input: str
    ticker: str
    name: str
    sector: str | None
    industry: str | None
    exchange: str
    market_cap: float | None
    is_financial: bool


class Candidate(BaseModel):
    ticker: str
    name: str
    industry: str | None


class InputProblem(BaseModel):
    input: str
    kind: Literal["not_found", "ambiguous", "duplicate", "unavailable"]
    message: str
    candidates: list[Candidate] = []


class Plan(BaseModel):
    ok: bool
    companies: list[Company]
    problems: list[InputProblem] = []
    blocking: list[str] = []          # shown to the user; comparison doesn't run
    warnings: list[str] = []          # shown to the user; comparison runs
    match: Literal["same_industry", "same_sector", "different_sector", "unknown"] | None = None
    agents: list[str] = []
    skipped: dict[str, str] = {}      # agent -> reason
    weights: dict[str, float] = {}    # renormalised over planned agents
    data_parts: list[str] = []
    needs_macro: bool = False


# ---------------------------------------------------------------- resolve

def _company(raw: str, p: dict) -> Company:
    return Company(input=raw, ticker=p["ticker"], name=p["name"], sector=p["sector"],
                   industry=p["industry"], exchange=p["exchange"], market_cap=p["market_cap"],
                   is_financial=is_financial({"industry": p["industry"]}))


def resolve(raw: str) -> Company | InputProblem:
    text = raw.strip()
    if _SYMBOL_RE.fullmatch(text):
        p = lookup.lookup_symbol(text)
        if p:
            return _company(raw, p)

    try:
        found = lookup.search_company(text)
    except lookup.LookupUnavailable:
        return InputProblem(input=raw, kind="unavailable",
                            message=f"Couldn't reach Yahoo Finance to look up “{text}” right now. "
                                    "Please try again in a minute.")
    exact = [c for c in found if c["exact"]]
    if len(exact) == 1:
        p = lookup.lookup_symbol(exact[0]["ticker"])
        if p:
            return _company(raw, p)
    options = (exact or found)[:4]
    if options:
        cands = [Candidate(ticker=c["ticker"], name=c["name"], industry=c["industry"]) for c in options]
        listing = ", ".join(f"{c.ticker} ({c.name})" for c in cands)
        lead = "matches more than one company" if len(exact) > 1 else "isn't an exact match"
        return InputProblem(input=raw, kind="ambiguous", candidates=cands,
                            message=f"“{text}” {lead} — please pick one: {listing}.")
    return InputProblem(input=raw, kind="not_found",
                        message=f"Couldn't find a company listed on NSE/BSE for “{text}”. "
                                "Try its NSE symbol, e.g. TCS or HDFCBANK.")


# ---------------------------------------------------------------- checks

def _sector_check(cs: list[Company], allow_cross_sector: bool):
    blocking, warnings = [], []
    if any(c.sector is None or c.industry is None for c in cs):
        missing = ", ".join(c.ticker for c in cs if c.sector is None or c.industry is None)
        warnings.append(f"Yahoo Finance has no sector for {missing}, so we can't confirm these "
                        "companies are in the same business.")
        return "unknown", blocking, warnings
    if len({c.industry for c in cs}) == 1:
        return "same_industry", blocking, warnings
    detail = "; ".join(f"{c.name}: {c.industry} ({c.sector})" for c in cs)
    if len({c.sector for c in cs}) == 1:
        warnings.append(f"Same sector but different businesses — {detail}. "
                        "The comparison is less like-for-like.")
        return "same_sector", blocking, warnings
    msg = (f"These companies are in different sectors — {detail}. EquityLens compares companies "
           "in the same sector, where the numbers mean the same thing.")
    (warnings if allow_cross_sector else blocking).append(msg)
    return "different_sector", blocking, warnings


def _other_warnings(cs: list[Company]) -> list[str]:
    out = []
    lenders = [c for c in cs if c.is_financial]
    if lenders and len(lenders) < len(cs):
        out.append(f"{', '.join(c.name for c in lenders)} is a financial company and the others "
                   "aren't — ratios like ROCE and debt/equity can't be compared across them.")
    caps = [c for c in cs if c.market_cap]
    if len(caps) >= 2:
        big, small = max(caps, key=lambda c: c.market_cap), min(caps, key=lambda c: c.market_cap)
        ratio = big.market_cap / small.market_cap
        if ratio >= SIZE_GAP_WARN:
            out.append(f"{big.name} is about {ratio:.0f} times larger than {small.name} by market "
                       "value — size alone changes risk and growth, so keep that in mind.")
    return out


def _agents() -> tuple[list[str], dict[str, str]]:
    skipped = {}
    if not (config.REDDIT_CLIENT_ID and config.REDDIT_CLIENT_SECRET):
        skipped["social"] = "Reddit sentiment isn't available (Reddit API keys are not set up)."
    return [a for a in SPECIALISTS if a not in skipped], skipped


# ---------------------------------------------------------------- plan

def make_plan(inputs: list[str], allow_cross_sector: bool = False) -> Plan:
    inputs = [i for i in inputs if i and i.strip()]
    if not 2 <= len(inputs) <= 3:
        return Plan(ok=False, companies=[], blocking=["Please enter 2 or 3 companies to compare."])

    with ThreadPoolExecutor(max_workers=3) as pool:
        resolved = list(pool.map(resolve, inputs))

    companies: list[Company] = []
    problems = [r for r in resolved if isinstance(r, InputProblem)]
    for r in resolved:
        if isinstance(r, Company):
            twin = next((c for c in companies if c.ticker.split(".")[0] == r.ticker.split(".")[0]), None)
            if twin:
                problems.append(InputProblem(input=r.input, kind="duplicate",
                                             message=f"“{twin.input}” and “{r.input}” are the same "
                                                     f"company ({twin.ticker})."))
            else:
                companies.append(r)

    blocking = [p.message for p in problems]
    warnings: list[str] = []
    match = None
    if not problems:
        match, b, w = _sector_check(companies, allow_cross_sector)
        blocking += b
        warnings += w + _other_warnings(companies)

    agents, skipped = _agents()
    total = sum(WEIGHTS[a] for a in agents)
    specs = [SPECIALISTS[a] for a in agents]
    return Plan(
        ok=not blocking, companies=companies, problems=problems, blocking=blocking,
        warnings=warnings, match=match, agents=agents, skipped=skipped,
        weights={a: round(WEIGHTS[a] / total, 3) for a in agents},
        data_parts=sorted(set().union(*(s.needs for s in specs))),
        needs_macro=any(s.needs_macro for s in specs),
    )
