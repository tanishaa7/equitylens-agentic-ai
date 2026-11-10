"""Logging for EquityLens.

- logs/equitylens.log — warnings and AI-call details (rotating, 3 × 1 MB)
- logs/runs.jsonl     — one line per comparison: inputs, status, timings, AI calls,
                        tokens, verdict. Used for "today's usage" and debugging.

No API keys or personal data are logged — only the public tickers typed in.

    setup_logging()                 # once, at app / script start
    log_run(run, inputs)            # after each comparison (the orchestrator does this)
    usage_today()                   # {"comparisons": 3, "from_cache": 1, "ai_calls": 18, ...}
"""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import config
from data.common import now_iso
from data.store import today_ist

LOG_DIR = config.ROOT / "logs"
_configured = False


def setup_logging(console_level: int = logging.WARNING) -> None:
    global _configured
    if _configured:
        return
    LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fh = RotatingFileHandler(LOG_DIR / "equitylens.log", maxBytes=1_000_000, backupCount=3)
    fh.setFormatter(fmt)
    fh.setLevel(logging.INFO)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(console_level)
    app = logging.getLogger("equitylens")
    app.setLevel(logging.INFO)
    app.addHandler(fh)
    app.addHandler(ch)
    app.propagate = False
    _configured = True


def _runs_file() -> Path:
    return LOG_DIR / "runs.jsonl"


def run_record(run, inputs: list[str]) -> dict:
    usage = [] if run.from_cache else run.usage
    models: dict[str, int] = {}
    for u in usage:
        models[u["model"]] = models.get(u["model"], 0) + 1
    v = run.verdict
    removed = sum(1 for r in run.reports for c in r.checks if c.startswith("Removed"))
    return {
        "ts": now_iso(),
        "date_ist": today_ist(),
        "inputs": [i for i in inputs if i and i.strip()],
        "tickers": [c.ticker for c in run.plan.companies],
        "status": run.status,
        "from_cache": run.from_cache,
        "user_message": run.user_message,
        "timings": run.timings,
        "ai_calls": len(usage),
        "tokens_in": sum(u["input_tokens"] for u in usage),
        "tokens_out": sum(u["output_tokens"] for u in usage),
        "models": models,
        "agents": {r.agent: r.status if r.status != "failed" else f"failed:{r.error_kind}"
                   for r in run.reports},
        "debate": run.debate.status if run.debate else None,
        "verdict": None if v is None else {"status": v.status, "leader": v.leader,
                                           "scores": v.scores, "confidence": v.confidence},
        "claims_removed_notes": removed,
    }


def log_run(run, inputs: list[str]) -> None:
    """Append one line to logs/runs.jsonl. Never raises — logging must not break a run."""
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with _runs_file().open("a") as f:
            f.write(json.dumps(run_record(run, inputs), default=str) + "\n")
    except Exception:
        logging.getLogger("equitylens.runlog").warning("couldn't write run log", exc_info=True)


def usage_today() -> dict:
    out = {"date": today_ist(), "comparisons": 0, "from_cache": 0, "ai_calls": 0, "tokens": 0,
           "limit_hits": 0}
    try:
        lines = _runs_file().read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("date_ist") != out["date"] or r.get("status") in ("blocked", "limited"):
            continue
        out["comparisons"] += 1
        out["from_cache"] += bool(r.get("from_cache"))
        out["ai_calls"] += r.get("ai_calls", 0)
        out["tokens"] += r.get("tokens_in", 0) + r.get("tokens_out", 0)
        out["limit_hits"] += "free AI limit" in (r.get("user_message") or "")
    return out
