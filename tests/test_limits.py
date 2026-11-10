"""Offline tests for the public-site usage limits.

Run:  .venv/bin/python -m tests.test_limits
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import agents.orchestrator as orch
import limits
import runlog
from data import store
from tests import test_orchestrator as T


def main():
    limits.reset()
    limits.MAX_RUNS_PER_VISITOR_HOUR, limits.MAX_RUNS_PER_DAY = 3, 5
    a = limits.visitor_id("203.0.113.7, 10.0.0.1", "session-a")
    b = limits.visitor_id(None, "session-b")
    assert "203" not in a and len(a) == 16 and a != b
    print("[ok] visitors identified by a hash of the IP (no raw IP kept)")

    t0 = 1_000_000.0
    for i in range(3):
        assert limits.check(a, t0 + i) is None
        limits.record(a, t0 + i)
    msg = limits.check(a, t0 + 10)
    assert msg and "3 new comparisons in the last hour" in msg and "60 minutes" in msg
    print(f"[ok] 4th new comparison within an hour blocked:\n     \"{msg}\"")
    assert limits.check(b, t0 + 10) is None, "other visitors aren't affected"
    assert limits.check(a, t0 + 3601) is None
    print("[ok] other visitors unaffected; allowed again after the hour")

    limits.record(b, t0 + 20)
    limits.record(b, t0 + 21)       # site total now 5 = daily cap
    msg = limits.check(limits.visitor_id("198.51.100.1", "c"), t0 + 30)
    assert msg and "today's limit" in msg
    print(f"[ok] site-wide daily cap:\n     \"{msg}\"")

    # ---------- orchestrator: saved results never count, limit stops before any fetch / AI
    store.ROOT, store.ENABLED = Path(tempfile.mkdtemp()), True
    runlog.LOG_DIR = Path(tempfile.mkdtemp())
    orch.RETRY_PAUSE = 0
    asked = []

    def allow():
        asked.append("allow")
        return None

    T.setup({})
    first = orch.run_comparison_sync(["AAA.NS", "BBB.NS"], gate=allow)
    T.setup({})
    again = orch.run_comparison_sync(["AAA.NS", "BBB.NS"], gate=allow)
    assert first.status == "ok" and again.from_cache and asked == ["allow"]
    print("[ok] saved result reused without asking the limiter (free for everyone)")

    T.setup({})
    r = orch.run_comparison_sync(["AAA.NS", "BBB.NS"], refresh=True, gate=lambda: "limit msg")
    assert r.status == "limited" and r.user_message == "limit msg" and T.fetched == [] and T.judged == []
    assert runlog.usage_today()["comparisons"] == 2
    print("[ok] limit reached → stops before fetching data or calling AI; not counted as usage")

    print("\nAll limits tests passed.")


if __name__ == "__main__":
    main()
