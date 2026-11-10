"""Run every offline test suite (each in its own process, since tests swap in fakes).

Run:  .venv/bin/python -m tests.run_all
"""

import subprocess
import sys

SUITES = ["test_llm_errors", "test_financials_agent", "test_specialists", "test_planner",
          "test_orchestrator", "test_debate", "test_judge", "test_cache_logging", "test_limits"]

failed = []
for s in SUITES:
    r = subprocess.run([sys.executable, "-m", f"tests.{s}"], capture_output=True, text=True)
    last = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
    print(f"{'PASS' if r.returncode == 0 else 'FAIL'}  {s:<24} {last if r.returncode == 0 else ''}")
    if r.returncode:
        failed.append(s)
        print(r.stdout[-1500:], r.stderr[-1500:], sep="\n")
print(f"\n{len(SUITES) - len(failed)}/{len(SUITES)} suites passed.")
sys.exit(1 if failed else 0)
