"""Offline tests for the disk cache, the saved-result reuse and the run log.

Run:  .venv/bin/python -m tests.test_cache_logging
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import agents.orchestrator as orch
import runlog
from data import lookup, store
from tests import test_orchestrator as T


def main():
    store.ROOT = Path(tempfile.mkdtemp())
    store.ENABLED = True
    runlog.LOG_DIR = Path(tempfile.mkdtemp())

    # ---------- store basics
    store.put("x", "k", {"a": 1})
    assert store.get("x", "k", ttl=60) == {"a": 1}
    real_time = store.time.time
    store.time.time = lambda: real_time() + 120
    assert store.get("x", "k", ttl=60) is None, "expired entry must be a miss"
    store.time.time = real_time
    store._path("x", "k").write_text("{broken json")
    assert store.get("x", "k", ttl=60) is None
    print("[ok] cache: read/write, expiry, broken file = miss (never crashes)")

    calls = []

    def fetch(v):
        calls.append(v)
        return {"status": v}
    for _ in range(2):
        store.cached_call("y", "bad", 60, fetch, "unavailable", ok=lambda r: r["status"] == "ok")
        store.cached_call("y", "good", 60, fetch, "ok", ok=lambda r: r["status"] == "ok")
    assert calls == ["unavailable", "ok", "unavailable"], calls
    print("[ok] failures (e.g. news unavailable) are never cached; successes are")

    # ---------- Yahoo lookups (typos remembered too)
    seen = []
    real_lookup = lookup._lookup_symbol
    lookup._lookup_symbol = lambda t: seen.append(t) or None
    assert lookup.lookup_symbol("ZZTOP") is None and lookup.lookup_symbol("zztop") is None
    assert seen == ["ZZTOP.NS"]
    lookup._lookup_symbol = real_lookup
    print("[ok] symbol lookups cached for a day, including 'not found'")

    # ---------- whole-comparison reuse
    orch.RETRY_PAUSE = 0
    T.setup({})
    first = orch.run_comparison_sync(["AAA.NS", "BBB.NS"])
    assert first.status == "ok" and not first.from_cache and T.fetched
    T.setup({})
    again = orch.run_comparison_sync(["BBB.NS", "AAA.NS"])   # other order, same companies
    assert again.from_cache and T.fetched == [] and T.judged == []
    print("[ok] same companies, same day → saved result reused: no data fetch, no AI")

    T.setup({})
    fresh = orch.run_comparison_sync(["AAA.NS", "BBB.NS"], refresh=True)
    assert not fresh.from_cache and T.fetched
    print("[ok] refresh=True ('Run fresh' button) runs everything again")

    real_today = store.today_ist
    store.today_ist = lambda: "2099-01-01"
    T.setup({})
    tomorrow = orch.run_comparison_sync(["AAA.NS", "BBB.NS"])
    store.today_ist = real_today
    assert not tomorrow.from_cache
    print("[ok] next day → new comparison (results are reused for one day only)")

    store.clear("result")
    T.setup({}, judge="busy")
    partial = orch.run_comparison_sync(["AAA.NS", "BBB.NS"])
    T.setup({})
    after = orch.run_comparison_sync(["AAA.NS", "BBB.NS"])
    assert partial.status == "partial" and not after.from_cache
    print("[ok] incomplete results (numbers-only verdict) are not saved — next try gets the full one")

    # ---------- run log + today's usage
    T.setup({}, plan_ok=False)
    orch.run_comparison_sync(["Tata Motors", "Maruti"])
    lines = [json.loads(x) for x in (runlog.LOG_DIR / "runs.jsonl").read_text().splitlines()]
    assert len(lines) == 7 and lines[1]["from_cache"] and lines[1]["ai_calls"] == 0
    assert lines[0]["tickers"] == ["AAA.NS", "BBB.NS"] and lines[0]["verdict"]["status"] == "ok"
    assert lines[-1]["status"] == "blocked" and lines[-1]["inputs"] == ["Tata Motors", "Maruti"]
    assert all("key" not in json.dumps(x).lower() for x in lines), "no keys in logs"
    u = runlog.usage_today()
    assert u["comparisons"] == 6 and u["from_cache"] == 1, u     # the blocked run isn't counted
    print(f"[ok] every run logged to runs.jsonl; today's usage: {u}")

    print("\nAll cache & logging tests passed.")


if __name__ == "__main__":
    main()
