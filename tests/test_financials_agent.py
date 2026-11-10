"""Offline tests for the Financials agent — no API key or network needed.

Run:  .venv/bin/python -m tests.test_financials_agent
Uses two made-up companies and a fake Gemini to check the prompt, the
number/citation checks, bank handling, confidence cap, and failure handling.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from google.genai import errors

import agents.llm as llm
from agents.common import unmatched_numbers
from agents.financials import build_prompt, confidence_cap, run_financials
from data.ratios import FORMULAS, NOT_FOR_FINANCIALS


def year(period, rev, ni, roce, roe, prev_rev=None):
    return {"period": period, "revenue": rev, "net_income": ni, "ebitda_margin": 0.25,
            "operating_margin": 0.22, "net_margin": ni / rev, "roe": roe, "roce": roce,
            "debt_to_equity": 0.1, "interest_coverage": 50.0, "free_cash_flow": ni * 0.9,
            "cash_conversion": 1.0, "receivable_days": 70.0,
            "revenue_growth": None if prev_rev is None else rev / prev_rev - 1,
            "profit_growth": None}


def company(ticker, name, roce, financial=False, warnings=()):
    by_year = [year("FY26", 2.0e12, 4.0e11, roce, 0.40, 1.8e12),
               year("FY25", 1.8e12, 3.6e11, roce - 0.02, 0.38, 1.7e12),
               year("FY24", 1.7e12, 3.4e11, roce - 0.03, 0.36)]
    na = NOT_FOR_FINANCIALS if financial else []
    for y in by_year:
        for k in na:
            y[k] = None
    src = {"provider": "Yahoo Finance (yfinance)", "fetched_at": "2026-11-10T00:00:00+00:00"}
    return {
        "ticker": ticker,
        "market": {"name": name, "industry": "Banks - Regional" if financial else "IT Services",
                   "source": src},
        "ratios": {
            "ticker": ticker, "is_financial": financial, "not_applicable": na, "by_year": by_year,
            "growth": {"revenue_cagr_3y": None, "profit_cagr_3y": None, "cagr_period": None},
            "valuation": {"market_cap": 9.0e12, "pe": 22.5, "pe_earnings_basis": "annual FY26",
                          "pb": 8.0, "ev_ebitda": None if financial else 15.0},
            "latest_quarter": None, "data_warnings": list(warnings), "formulas": FORMULAS,
            "source": src,
        },
    }


def fake_gemini(*outcomes):
    calls = []

    async def gen(**kw):
        calls.append(kw)
        o = outcomes[min(len(calls), len(outcomes)) - 1]
        if isinstance(o, Exception):
            raise o
        return SimpleNamespace(parsed=None, text=json.dumps(o), usage_metadata=None)

    llm._get_client = lambda: SimpleNamespace(aio=SimpleNamespace(
        models=SimpleNamespace(generate_content=gen)))
    llm._semaphore = None
    llm._cooldown_until.clear()
    return calls


def find_id(sheet, ticker, label):
    return next(f.id for f in sheet.facts.values() if f.ticker == ticker and f.text.startswith(label))


def main():
    async def _no_sleep(_):
        return None
    llm.asyncio.sleep = _no_sleep

    # --- number matching
    assert unmatched_numbers("ROCE 58.5% in FY26", [58.5, 26]) == []
    assert unmatched_numbers("ROCE about 58%", [58.5]) == []          # rounding is fine
    assert unmatched_numbers("ROCE 59.5%", [58.5]) == ["59.5"]         # made-up number
    assert unmatched_numbers("₹1,90,203 cr over 3 years", [190203]) == []
    assert unmatched_numbers("won a $5 billion deal", [2]) == ["5"]   # small money must be sourced
    assert unmatched_numbers("margin rose 7% in Q2", [2]) == ["7"]
    assert unmatched_numbers("₹4 cr fine", []) == ["4"]
    print("[ok] number matching: exact and rounded pass, made-up numbers fail")

    # --- prompt
    a = company("AAA.NS", "Alpha Tech", 0.50)
    b = company("BBB.NS", "Beta Tech", 0.30, warnings=["Latest quarter on file is Q1 FY26 (497 days old)."])
    sheet, prompt = build_prompt([a, b])
    roce_a, roce_b = find_id(sheet, "AAA.NS", "ROCE"), find_id(sheet, "BBB.NS", "ROCE")
    assert "ROCE: FY24 47.0% → FY25 48.0% → FY26 50.0%" in prompt     # oldest → newest
    assert "Data warning: Latest quarter" in prompt
    assert "EBIT / average capital employed" in sheet.facts[roce_a].source
    print(f"[ok] prompt has {len(sheet.facts)} numbered facts with sources and formulas")

    bank = company("CCC.NS", "Gamma Bank", 0.0, financial=True)
    _, bank_prompt = build_prompt([bank, company("DDD.NS", "Delta Bank", 0.0, financial=True)])
    assert "ROCE:" not in bank_prompt and "; EV/EBITDA" not in bank_prompt
    assert "not applicable" in bank_prompt
    print("[ok] banks: ROCE / EBITDA / D/E left out and marked not applicable")

    # --- verification of AI output
    ai_output = {
        "summary": "Alpha Tech earns far more on its capital than Beta Tech.",
        "assessments": [
            {"ticker": "AAA.NS", "score": 12,     # out of range → clamped to 10
             "key_points": ["ROCE of 50.0% in FY26", "ROCE of 65% in FY26"],  # 2nd is invented
             "red_flags": [],
             "evidence": [
                 {"claim": "ROCE 50.0% in FY26 vs 30.0%", "source": f"{roce_a}, {roce_b}"},
                 {"claim": "ROCE 55.0% in FY26", "source": roce_a},     # wrong number
                 {"claim": "Strong brand", "source": "annual report"},  # no fact ID
             ]},
            {"ticker": "bbb", "score": 4, "key_points": ["Lower ROCE"],  # ticker without .NS
             "red_flags": ["Quarterly data is stale"], "evidence": []},
            {"ticker": "ZZZ.NS", "score": 9, "key_points": [], "red_flags": [], "evidence": []},
        ],
        "confidence": 0.95, "data_gaps": [],
    }
    fake_gemini(ai_output)
    r = asyncio.run(run_financials([a, b]))
    fa = r.findings.assessments
    assert r.status == "ok" and [x.ticker for x in fa] == ["AAA.NS", "BBB.NS"]
    assert fa[0].score == 10
    assert len(fa[0].evidence) == 1 and fa[0].evidence[0].source.startswith(f"{roce_a}, {roce_b} — Yahoo")
    assert fa[0].key_points == ["ROCE of 50.0% in FY26"]
    assert r.findings.confidence == 0.9   # capped: 1 data warning
    print("[ok] AI output checked:")
    print("     - wrong-number claim, uncited claim and invented key point removed")
    print("     - unknown company dropped, 'bbb' matched to BBB.NS, score clamped to 10")
    print(f"     - confidence 95% capped to {r.findings.confidence:.0%} (data warning)")
    for ch in r.checks:
        print(f"       check: {ch}")

    # --- missing company → one retry
    one = {**ai_output, "assessments": [ai_output["assessments"][0]]}
    calls = fake_gemini(one, ai_output)
    r = asyncio.run(run_financials([a, b]))
    assert len(calls) == 2 and "left out: BBB.NS" in calls[1]["contents"] and r.status == "ok"
    print("[ok] company missing from AI answer → asked again once")

    # --- daily limit → failed report with friendly message, no crash
    daily = errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
        "message": "Quota exceeded", "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}})
    fake_gemini(daily)
    r = asyncio.run(run_financials([a, b]))
    assert r.status == "failed" and r.error_kind == "rate_limit_daily"
    print(f"[ok] daily limit → report status 'failed'. User sees:\n     \"{r.error}\"")

    cap, why = confidence_cap([bank, company("EEE.NS", "E", 0.3, warnings=["x", "y"])])
    assert cap == 0.7, cap
    print(f"[ok] confidence cap {cap:.0%} ({why})")

    print("\nAll Financials agent tests passed.")


if __name__ == "__main__":
    main()
