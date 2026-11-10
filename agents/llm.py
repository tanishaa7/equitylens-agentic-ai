"""The one place that talks to the AI provider (Gemini free tier).

Agents call `generate(...)` and get back a validated Pydantic object.
When something goes wrong, it raises `AIUnavailable` with a `user_message`
that is safe to show on screen as-is, e.g. when the free limit is used up:

    try:
        findings = await generate(SYSTEM, prompt, AgentFindings)
    except AIUnavailable as e:
        st.warning(e.user_message)       # Streamlit UI (Step 9)
"""

from __future__ import annotations

import asyncio
import logging
import re
import time as _time_mod
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

import config

log = logging.getLogger("equitylens.llm")
T = TypeVar("T", bound=BaseModel)

Tier = Literal["specialist", "judge"]
Kind = Literal["rate_limit_minute", "rate_limit_daily", "busy", "config", "bad_output", "error"]

MAX_RETRIES = 2              # per call, for per-minute limits and server hiccups
MAX_WAIT_PER_RETRY = 30.0    # seconds; longer waits fail fast with a message instead
CALL_TIMEOUT = 90           # seconds per AI call; a model that hangs isn't retried
FIRST_TRY_TIMEOUT = 45      # shorter when another model can take over
COOLDOWN = 600              # skip a model that just hung / was overloaded for 10 minutes
_cooldown_until: dict[str, float] = {}


# ---------------------------------------------------------------- messages

def daily_reset_time_ist() -> str:
    """Gemini daily quotas reset at midnight Pacific time. Show it in IST."""
    pacific = ZoneInfo("America/Los_Angeles")
    now = datetime.now(pacific)
    reset = datetime.combine(now.date() + timedelta(days=1), time(0, 0), tzinfo=pacific)
    ist = reset.astimezone(ZoneInfo("Asia/Kolkata"))
    return ist.strftime("%-I:%M %p IST")


def user_message(kind: Kind, retry_after: float | None = None) -> str:
    if kind == "rate_limit_minute":
        wait = f"about {int(retry_after) + 1} seconds" if retry_after else "a minute"
        return (f"EquityLens is getting a lot of requests right now. "
                f"Please wait {wait} and try again.")
    if kind == "rate_limit_daily":
        return ("Today's free AI limit has been reached, so new comparisons are paused. "
                f"Please try again after {daily_reset_time_ist()}.")
    if kind == "busy":
        return "The AI service is busy at the moment. Please try again in a few minutes."
    if kind == "config":
        return "The AI service isn't set up correctly. Please contact the site owner."
    if kind == "bad_output":
        return "The AI gave an incomplete answer. Please try the comparison again."
    return "Something went wrong while analysing. Please try again."


@dataclass
class AIUnavailable(Exception):
    kind: Kind
    user_message: str
    detail: str = ""                 # technical detail for logs, never shown to users
    retry_after: float | None = None

    def __str__(self):
        return f"{self.kind}: {self.user_message} [{self.detail}]"


# ---------------------------------------------------------------- error mapping

def _retry_delay_seconds(details) -> float | None:
    """Read google.rpc.RetryInfo 'retryDelay': '37s' if the API sent one."""
    for d in _detail_items(details):
        if "RetryInfo" in str(d.get("@type", "")):
            m = re.match(r"([\d.]+)s", str(d.get("retryDelay", "")))
            if m:
                return float(m.group(1))
    return None


def _detail_items(details) -> list[dict]:
    if not isinstance(details, dict):
        return []
    items = details.get("error", {}).get("details") or details.get("details") or []
    return [d for d in items if isinstance(d, dict)]


def _is_daily_quota(details, message: str) -> bool:
    text = message or ""
    for d in _detail_items(details):
        for v in d.get("violations", []) or []:
            text += " " + str(v.get("quotaId", "")) + " " + str(v.get("quotaMetric", ""))
    return bool(re.search(r"per ?day|PerDay|daily", text, re.IGNORECASE))


def classify(err: Exception) -> AIUnavailable:
    """Turn any provider exception into an AIUnavailable with a friendly message."""
    from google.genai import errors

    if isinstance(err, AIUnavailable):
        return err
    if isinstance(err, errors.APIError):
        code, status, msg = err.code, err.status or "", err.message or ""
        detail = f"{code} {status}: {msg}"[:500]
        if code == 429 or status == "RESOURCE_EXHAUSTED":
            if _is_daily_quota(err.details, msg):
                return AIUnavailable("rate_limit_daily", user_message("rate_limit_daily"), detail)
            delay = _retry_delay_seconds(err.details)
            return AIUnavailable("rate_limit_minute", user_message("rate_limit_minute", delay),
                                 detail, retry_after=delay)
        if code in (401, 403) or "API key" in msg or status in ("PERMISSION_DENIED", "UNAUTHENTICATED"):
            return AIUnavailable("config", user_message("config"), detail)
        if code == 404:
            return AIUnavailable("config", user_message("config"),
                                 detail + " (model name may be wrong or retired)")
        if code and code >= 500:
            return AIUnavailable("busy", user_message("busy"), detail)
        return AIUnavailable("error", user_message("error"), detail)
    if isinstance(err, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return AIUnavailable("busy", user_message("busy"), repr(err))
    return AIUnavailable("error", user_message("error"), repr(err)[:500])


# ---------------------------------------------------------------- client

_client = None
_client_loop = None
_semaphore: asyncio.Semaphore | None = None
usage_log: list[dict] = []   # token counts per call, to measure real usage


def _get_client():
    global _client
    if config.LLM_PROVIDER != "gemini":
        raise AIUnavailable("config", user_message("config"),
                            f"LLM_PROVIDER={config.LLM_PROVIDER!r} is not supported yet")
    if not config.GEMINI_API_KEY:
        raise AIUnavailable("config", user_message("config"), "GEMINI_API_KEY is not set in .env")
    global _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client_loop is not loop:   # async HTTP sessions are per loop
        from google import genai
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
        _client_loop = loop
    return _client


_semaphore_loop = None


def _get_semaphore() -> asyncio.Semaphore:
    """One limiter per event loop — each asyncio.run() (e.g. every Streamlit
    click) gets a fresh loop, and a Semaphore can't be shared across loops."""
    global _semaphore, _semaphore_loop
    loop = asyncio.get_running_loop()
    if _semaphore is None or _semaphore_loop is not loop:
        _semaphore = asyncio.Semaphore(config.GEMINI_MAX_CONCURRENT)
        _semaphore_loop = loop
    return _semaphore


def model_for(tier: Tier) -> str:
    return config.GEMINI_JUDGE_MODEL if tier == "judge" else config.GEMINI_SPECIALIST_MODEL


# ---------------------------------------------------------------- main call

FALLBACK_KINDS = {"busy", "rate_limit_minute", "rate_limit_daily"}


def _model_missing(e: AIUnavailable) -> bool:
    return e.kind == "config" and "model name may be wrong" in e.detail


async def generate(system: str, prompt: str, schema: type[T], tier: Tier = "specialist",
                   temperature: float = 0.2, label: str = "", allow_fallback: bool = True) -> T:
    """Ask the model for JSON matching `schema`. Retries short rate limits and
    server errors; raises AIUnavailable (with user_message) otherwise.

    If the model is overloaded, rate-limited or retired, the call falls back to
    the other configured model (judge ↔ specialist) — another model beats no answer.
    """
    from google.genai import types

    client = _get_client()
    cfg = types.GenerateContentConfig(
        system_instruction=system,
        temperature=temperature,
        response_mime_type="application/json",
        response_schema=schema,
    )
    models = [model_for(tier)]
    other = model_for("specialist" if tier == "judge" else "judge")
    if allow_fallback and other not in models:
        models.append(other)

    now = _time_mod.monotonic()
    healthy = [m for m in models if _cooldown_until.get(m, 0) <= now]
    # all cooling down → try anyway, the one that started cooling first (most likely recovered)
    models = healthy or sorted(models, key=lambda m: _cooldown_until.get(m, 0))
    for i, model in enumerate(models):
        has_backup = i + 1 < len(models)
        try:
            return await _generate_one(client, model, prompt, cfg, schema, label,
                                       FIRST_TRY_TIMEOUT if has_backup else CALL_TIMEOUT)
        except AIUnavailable as e:
            if e.kind in ("busy", "rate_limit_daily"):
                _cooldown_until[model] = _time_mod.monotonic() + COOLDOWN
            if has_backup and (e.kind in FALLBACK_KINDS or _model_missing(e)):
                log.warning("%s unavailable (%s) — falling back to %s", model, e.kind, models[i + 1])
                continue
            raise


async def _generate_one(client, model: str, prompt: str, cfg, schema: type[T], label: str,
                        timeout: float = CALL_TIMEOUT) -> T:
    last: AIUnavailable | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with _get_semaphore():
                started = _time_mod.monotonic()
                resp = await asyncio.wait_for(
                    client.aio.models.generate_content(model=model, contents=prompt, config=cfg),
                    timeout=timeout,
                )
            _record_usage(resp, model, label, _time_mod.monotonic() - started)
            parsed = resp.parsed if isinstance(resp.parsed, schema) else None
            if parsed is None:
                parsed = schema.model_validate_json(resp.text or "")
            return parsed
        except ValidationError as e:
            last = AIUnavailable("bad_output", user_message("bad_output"), str(e)[:500])
        except Exception as e:  # noqa: BLE001 — everything is mapped to a friendly error
            last = classify(e)

        log.warning("AI call %s on %s failed (attempt %d): %s", label, model, attempt + 1, last)
        if last.kind in ("rate_limit_daily", "config", "error"):
            break  # retrying won't help
        if "TimeoutError" in last.detail:
            break  # a hanging model usually keeps hanging — move on (fallback / failed report)
        if attempt < MAX_RETRIES:
            # 503 "high demand" spikes last a while: wait 5s, then 10s
            wait = last.retry_after or (5 * 2 ** attempt if last.kind == "busy" else 2 ** (attempt + 1))
            if wait > MAX_WAIT_PER_RETRY:
                break  # don't keep the user staring at a spinner
            await asyncio.sleep(wait)

    raise last


def _record_usage(resp, model: str, label: str, seconds: float = 0.0):
    u = getattr(resp, "usage_metadata", None)
    entry = {
        "label": label, "model": model, "seconds": round(seconds, 1),
        "input_tokens": (u.prompt_token_count or 0) if u else 0,
        "output_tokens": ((u.candidates_token_count or 0)
                          + (getattr(u, "thoughts_token_count", 0) or 0)) if u else 0,
    }
    usage_log.append(entry)
    log.info("AI call %s on %s: %d in / %d out tokens, %.1fs", label, model,
             entry["input_tokens"], entry["output_tokens"], seconds)


async def aclose():
    """Close the AI client's connections before the event loop ends (avoids
    'Event loop is closed' errors when the next run creates a new loop)."""
    global _client, _client_loop
    if _client is not None and _client_loop is asyncio.get_running_loop():
        try:
            await _client.aio.aclose()
        except Exception:  # closing is best-effort
            log.debug("client close failed", exc_info=True)
        _client, _client_loop = None, None


def generate_sync(*args, **kwargs):
    """For scripts: run generate() without writing async code."""
    async def _run():
        try:
            return await generate(*args, **kwargs)
        finally:
            await aclose()
    return asyncio.run(_run())
