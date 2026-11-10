"""Try the Planner (no AI used): resolves names, checks sectors, picks agents.

Run:  .venv/bin/python try_plan.py TCS Infosys
      .venv/bin/python try_plan.py "kalyan jewellers" TITAN
      .venv/bin/python try_plan.py TCS HDFCBANK --allow-cross-sector
Put multi-word names in quotes.
"""

from __future__ import annotations

import sys
import warnings

from agents.planner import make_plan
from data.common import fmt_cr

warnings.filterwarnings("ignore")


def main(argv: list[str]):
    allow = "--allow-cross-sector" in argv
    plan = make_plan([a for a in argv if a != "--allow-cross-sector"], allow_cross_sector=allow)

    print(f"\nPlan: {'READY' if plan.ok else 'BLOCKED'}" + (f"  ({plan.match})" if plan.match else ""))
    for c in plan.companies:
        print(f"  {c.input!r:<22} → {c.ticker:<14} {c.name[:28]:<28} {c.industry or '?'}"
              f"  · {fmt_cr(c.market_cap)}" + ("  · financial" if c.is_financial else ""))
    for p in plan.problems:
        print(f"  {p.input!r:<22} → {p.kind}")
    for m in plan.blocking:
        print(f"\n  ⛔ {m}")
    for m in plan.warnings:
        print(f"\n  ⚠ {m}")
    if plan.ok:
        print("\n  Agents & weights: " + ", ".join(f"{a} {w:.0%}" for a, w in plan.weights.items()))
        for a, why in plan.skipped.items():
            print(f"  Skipped {a}: {why}")
        print(f"  Data to fetch: market + {', '.join(plan.data_parts)}"
              + (" + macro" if plan.needs_macro else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
