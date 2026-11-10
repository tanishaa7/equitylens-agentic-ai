"""Run a full comparison from the terminal: plan → data → agents → bull vs bear → judge.

Run:  .venv/bin/python compare.py TCS Infosys
      .venv/bin/python compare.py "kalyan jewellers" TITAN --details
Options: --details (agent scores, the full debate and every agent's findings),
         --allow-cross-sector, --refresh (ignore today's saved result)
"""

from __future__ import annotations

import json
import sys
import time
import warnings
from datetime import date

import config
import runlog
from agents.orchestrator import LABELS, run_comparison_sync
from try_agent import show

warnings.filterwarnings("ignore")


def progress():
    t0 = time.perf_counter()

    def on_event(stage: str, detail: str):
        print(f"  [{time.perf_counter() - t0:5.1f}s] {stage:<6} {detail}")
    return on_event


def score_table(run):
    tickers = [c.ticker for c in run.plan.companies]
    print("\n" + "Agent (weight)".ljust(24) + "".join(t.ljust(16) for t in tickers) + "Confidence")
    print("-" * (24 + 16 * len(tickers) + 10))
    for r in run.reports:
        w = run.plan.weights.get(r.agent)
        label = f"{LABELS[r.agent]} ({w:.0%})" if w else LABELS[r.agent]
        if r.status != "ok":
            print(label.ljust(24) + f"— {r.status}: {r.error}"[:80])
            continue
        scores = {a.ticker: a.score for a in r.findings.assessments}
        cells = "".join((f"{scores[t]:.1f}" if t in scores else "—").ljust(16) for t in tickers)
        print(label.ljust(24) + cells + f"{r.findings.confidence:.0%}")


def show_debate(run):
    d = run.debate
    if d is None:
        return
    print(f"\n== Bull vs bear  [{d.status}, {d.rounds_completed} round(s)]")
    if d.error:
        print(f"  ⚠ {d.error}")
    answers = {}
    for r in d.rebuttals:
        answers.setdefault(r.target_id, []).append(r)
    for c in run.plan.companies:
        print(f"\n  {c.ticker} — {c.name}")
        for a in (x for x in d.arguments if x.ticker == c.ticker):
            mark = "▲" if a.side == "bull" else "▼"
            print(f"   {mark} [{a.id}] ({a.strength}) {a.point}   {{{', '.join(a.fact_ids)}}}")
            for r in answers.get(a.id, []):
                print(f"        ↳ {r.side} {'concedes' if r.concedes else 'replies'}: {r.response}")
    for ch in d.checks:
        print(f"  (check) {ch}")


def show_verdict(run):
    v = run.verdict
    if v is None:
        return
    names = {c.ticker: c.name for c in run.plan.companies}
    tickers = list(names)
    print("\n" + "=" * 78)
    print(f"VERDICT  ·  confidence: {v.confidence_label} ({v.confidence:.0%})"
          + ("  ·  numbers-only" if v.status == "fallback" else ""))
    print("=" * 78)
    print(f"\n{v.headline}")
    if v.note:
        print(f"  ({v.note})")

    print("\n" + "Metric".ljust(40) + "".join(t.ljust(14) for t in tickers) + "Edge")
    print("-" * (40 + 14 * len(tickers) + 12))
    lens = None
    for r in v.lens_table:
        if r.lens != lens:
            lens = r.lens
            print(f"{lens.upper()}  (edge: {names.get(v.lens_edges.get(lens), 'split')})")
        print(f"  {r.metric}".ljust(40) + "".join(r.values[t].ljust(14) for t in tickers)
              + (names[r.edge] if r.edge else "—"))
    print("\nWeighted score:  " + "   ".join(f"{names[t]} {s:.1f}/10" for t, s in v.scores.items())
          + "   (weights used: " + ", ".join(f"{a} {w:.0%}" for a, w in v.effective_weights.items()) + ")")

    if v.reasons:
        print("\nWhy:")
        for p in v.reasons:
            print(f"  • {p.text}")
    for cv in v.companies:
        print(f"\n{cv.name} ({cv.ticker})")
        if cv.bull_case:
            print(f"  ▲ Bull: {cv.bull_case.text}")
        if cv.bear_case:
            print(f"  ▼ Bear: {cv.bear_case.text}")
        for f in cv.red_flags:
            print(f"  ! {f.text}")
        for line in cv.what_changed:
            print(f"  Δ {line}")
    if v.watch:
        print("\nThings to watch:")
        for p in v.watch:
            print(f"  • {p.text}")
    for ch in v.checks:
        print(f"  (check) {ch}")
    print(f"\n{v.disclaimer}")


def main(argv: list[str]):
    details = "--details" in argv
    allow = "--allow-cross-sector" in argv
    refresh = "--refresh" in argv
    runlog.setup_logging()
    inputs = [a for a in argv if not a.startswith("--")]

    print("Running comparison ...")
    run = run_comparison_sync(inputs, allow_cross_sector=allow, on_event=progress(),
                              refresh=refresh)
    if run.from_cache:
        print(f"\n  💾 Today's saved result (started {run.started_at[:16]} UTC) — no AI used. "
              "Add --refresh to run again.")

    print(f"\nStatus: {run.status}" + (f"   (enough to judge: {'yes' if run.can_judge else 'no'})"
                                       if run.status != "blocked" else ""))
    if run.user_message:
        print(f"\n  ⚠ {run.user_message}")
    for p in run.plan.problems:
        for c in p.candidates:
            print(f"      → {c.ticker}  {c.name}")
    for w in run.plan.warnings:
        print(f"\n  ⚠ {w}")
    if run.status in ("blocked", "data_error"):
        return

    show_verdict(run)
    if details or run.verdict is None:
        print("\n--- Agent scores")
        score_table(run)
    if details:
        show_debate(run)
        for r in run.reports:
            show(r)

    tokens = sum(u["input_tokens"] + u["output_tokens"] for u in run.usage)
    print(f"\nTime: {run.timings}   AI calls: {len(run.usage)}   tokens: {tokens:,}")
    out = config.CACHE_DIR / f"run_{'_'.join(c.ticker for c in run.plan.companies)}_{date.today()}.json"
    out.write_text(json.dumps(run.model_dump(), indent=2, default=str))
    print(f"Saved to {out.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1:])
