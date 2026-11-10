"""Offline tests for agents/llm.py error handling — no API key or network needed.

Run:  .venv/bin/python -m tests.test_llm_errors
Fakes Gemini responses (success, per-minute limit, daily limit, server error,
bad key) and checks the user sees the right message.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

from google.genai import errors

import agents.llm as llm
import config
from agents.llm import AIUnavailable, generate
from agents.schemas import AgentFindings

GOOD_JSON = ('{"summary":"A is stronger.","assessments":[{"ticker":"TCS.NS","score":7,'
             '"key_points":["High ROCE"],"red_flags":[],"evidence":[]}],'
             '"confidence":0.8,"data_gaps":[]}')


def rate_limit(quota_id: str, retry: str | None = None) -> errors.ClientError:
    details = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{"quotaId": quota_id}]}]
    if retry:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry})
    return errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                              "message": "Quota exceeded", "details": details}})


class FakeClient:
    """Returns/raises the scripted outcomes in order."""

    def __init__(self, outcomes):
        self.outcomes, self.calls, self.models = list(outcomes), 0, []
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._gen))

    async def _gen(self, **kw):
        self.calls += 1
        self.models.append(kw.get("model"))
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return SimpleNamespace(parsed=None, text=o, usage_metadata=None)


def run(outcomes, fallback=False):
    fake = FakeClient(outcomes)
    llm._get_client = lambda: fake
    llm._semaphore = None
    llm._cooldown_until.clear()
    run.last_fake = fake
    try:
        result = asyncio.run(generate("sys", "prompt", AgentFindings, label="test",
                                      allow_fallback=fallback))
        return fake.calls, result, None
    except AIUnavailable as e:
        return fake.calls, None, e


async def _no_sleep(_):
    return None


def main():
    llm.asyncio.sleep = _no_sleep  # don't actually wait during tests
    original_client = llm._get_client

    calls, result, err = run([GOOD_JSON])
    assert err is None and result.assessments[0].score == 7 and calls == 1
    print("[ok] success returns parsed AgentFindings")

    calls, result, err = run([rate_limit("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "2s"),
                              GOOD_JSON])
    assert err is None and calls == 2
    print("[ok] short per-minute limit → waits, retries, succeeds")

    calls, _, err = run([rate_limit("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "50s")])
    assert err.kind == "rate_limit_minute" and calls == 1
    print(f"[ok] long per-minute wait → fails fast. User sees:\n     \"{err.user_message}\"")

    calls, _, err = run([rate_limit("GenerateRequestsPerDayPerProjectPerModel-FreeTier")])
    assert err.kind == "rate_limit_daily" and calls == 1
    print(f"[ok] daily limit → no retry. User sees:\n     \"{err.user_message}\"")

    server = errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE",
                                                "message": "overloaded"}})
    calls, _, err = run([server, server, server])
    assert err.kind == "busy" and calls == llm.MAX_RETRIES + 1
    print(f"[ok] server overloaded → retried {calls}x. User sees:\n     \"{err.user_message}\"")

    calls, result, err = run([server, server, server, GOOD_JSON], fallback=True)
    assert err is None and calls == 4 and result.confidence == 0.8
    print("[ok] model still overloaded after retries → falls back to the other model, succeeds")

    fake2 = FakeClient([GOOD_JSON])
    llm._get_client = lambda: fake2       # cool-down from the run above is still active
    asyncio.run(generate("sys", "prompt", AgentFindings, label="test"))
    assert fake2.models == [config.GEMINI_JUDGE_MODEL], fake2.models
    print("[ok] overloaded model is skipped for 10 minutes → next call goes straight to the backup")

    llm._cooldown_until.clear()
    llm._cooldown_until[config.GEMINI_SPECIALIST_MODEL] = llm._time_mod.monotonic() + 100
    llm._cooldown_until[config.GEMINI_JUDGE_MODEL] = llm._time_mod.monotonic() + 500
    fake3 = FakeClient([GOOD_JSON])
    llm._get_client = lambda: fake3
    asyncio.run(generate("sys", "prompt", AgentFindings, tier="judge", label="test"))
    assert fake3.models == [config.GEMINI_SPECIALIST_MODEL], fake3.models
    print("[ok] both models cooling down → tries the one that started cooling first")

    bad_key = errors.ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                                 "message": "API key not valid."}})
    calls, _, err = run([bad_key])
    assert err.kind == "config" and calls == 1
    print(f"[ok] bad API key → \"{err.user_message}\"  (detail kept for logs only)")

    calls, _, err = run(['{"summary": "cut off', '{"summary": "cut off', '{"summary": "cut off'])
    assert err.kind == "bad_output"
    print("[ok] broken JSON from model → retried, then friendly error")

    llm._get_client = original_client
    saved = config.GEMINI_API_KEY
    saved_env = os.environ.pop("GEMINI_API_KEY", None)   # .env is loaded into the environment
    config.GEMINI_API_KEY = ""
    try:
        asyncio.run(generate("sys", "prompt", AgentFindings))
        raise AssertionError("expected AIUnavailable")
    except AIUnavailable as e:
        assert e.kind == "config" and "GEMINI_API_KEY" in e.detail
        print("[ok] missing key → config error (no crash)")
        os.environ["GEMINI_API_KEY"] = "  test-key-added-later \n"
        assert config.gemini_key() == "test-key-added-later"
        print("[ok] key added after start (e.g. Streamlit Secrets) is picked up, whitespace stripped")
    finally:
        config.GEMINI_API_KEY = saved
        if saved_env is not None:
            os.environ["GEMINI_API_KEY"] = saved_env

    print("\nAll error-handling tests passed.")


if __name__ == "__main__":
    main()
