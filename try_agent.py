"""Try one specialist agent on 2–3 companies.

Run:  .venv/bin/python try_agent.py financials TCS INFY
      .venv/bin/python try_agent.py news TCS INFY --dry-run   # show the AI prompt, no AI call
      .venv/bin/python try_agent.py all TCS INFY --dry-run    # every agent's prompt
Agents: financials, news, social, market, macro (or all).
The report is saved to cache/ as JSON.
"""

from __future__ import annotations

import asyncio
import json
import sys
import warnings
from datetime import date

import config
from agents import llm
from agents.llm import usage_log
from agents.registry import SPECIALISTS
from data.collect import collect_company, collect_macro
from data.common import normalize_ticker

warnings.filterwarnings("ignore")

CHOICES = [*SPECIALISTS, "all"]


def show(report):
    print(f"\n== {report.agent.title()} agent  [{report.status}]  model: {report.model}")
    if report.status == "skipped":
        print(f"\nSkipped:  (i) {report.error}")
        return
    if report.status != "ok":
        print(f"\nMessage on screen:  ⚠ {report.error}")
        return
    f = report.findings
    print(f"\n{f.summary}\n\nConfidence: {f.confidence:.0%}")
    for a in f.assessments:
        bar = "█" * round(a.score) + "░" * (10 - round(a.score))
        print(f"\n{a.ticker:<14} {bar} {a.score:.1f}/10")
        for p in a.key_points:
            print(f"  + {p}")
        for p in a.red_flags:
            print(f"  ! {p}")
        for e in a.evidence:
            print(f"  · {e.claim}\n      [{e.source}]")
    for g in f.data_gaps:
        print(f"\n(gap) {g}")
    for ch in report.checks:
        print(f"(check) {ch}")


def main(argv: list[str]):
    dry = "--dry-run" in argv
    args = [a for a in argv if a != "--dry-run"]
    if len(args) < 3 or args[0] not in CHOICES or not 2 <= len(args) - 1 <= 3:
        sys.exit(f"Usage: python try_agent.py {{{'|'.join(CHOICES)}}} TICKER1 TICKER2 [TICKER3] [--dry-run]")
    names = list(SPECIALISTS) if args[0] == "all" else [args[0]]
    tickers = [normalize_ticker(t) for t in args[1:]]
    agents = [SPECIALISTS[n] for n in names]
    parts = frozenset().union(*(a.needs for a in agents))

    print(f"Fetching {', '.join(tickers)} ...")
    companies = []
    for t in tickers:
        c = collect_company(t, parts)
        if "error" in c:
            sys.exit(f"!! {t}: {c['error']}")
        companies.append(c)
    macro = collect_macro(companies) if any(a.needs_macro for a in agents) else None

    if dry:
        for n, a in zip(names, agents):
            sheet, prompt = a.build_prompt(companies, macro)
            print(f"\n--- {n} prompt ({len(sheet.facts)} facts, ~{len(prompt) // 4:,} tokens) ---\n")
            print(prompt)
        return

    async def run_all():
        try:
            return await asyncio.gather(*(a.run(companies, macro) for a in agents))
        finally:
            await llm.aclose()

    reports = asyncio.run(run_all())
    for report in reports:
        show(report)
    for u in usage_log:
        print(f"\nTokens [{u['label']}]: {u['input_tokens']:,} in / {u['output_tokens']:,} out ({u['model']})")

    out = config.CACHE_DIR / f"agent_{args[0]}_{'_'.join(args[1:]).upper()}_{date.today()}.json"
    out.write_text(json.dumps([r.model_dump() for r in reports], indent=2))
    print(f"\nSaved to {out.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1:])
