"""Shared output shapes. Every specialist agent returns the same structure,
so the debate step and the judge can read all of them the same way.

`AgentFindings` is what the AI model fills in (sent to Gemini as the JSON
schema). `AgentReport` wraps it with bookkeeping the code fills in, including
a failed status so one broken agent never crashes the whole comparison.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    claim: str = Field(description="One specific fact, e.g. 'ROCE 58.5% in FY26 vs 41.1%'.")
    source: str = Field(description="ID(s) of the facts this claim uses, e.g. 'F12' or 'F3, F27'.")


class CompanyAssessment(BaseModel):
    ticker: str = Field(description="Ticker exactly as given, e.g. 'TCS.NS'.")
    score: float = Field(description="0 to 10 on this agent's lens only. 5 = neutral.")
    key_points: list[str] = Field(description="2–5 short strengths or neutral facts.")
    red_flags: list[str] = Field(description="0–4 short concerns. Empty list if none.")
    evidence: list[Evidence] = Field(description="Facts backing the score, each with a source.")


class AgentFindings(BaseModel):
    summary: str = Field(description="2–3 plain-English sentences comparing the companies.")
    assessments: list[CompanyAssessment] = Field(description="One entry per company.")
    confidence: float = Field(description="0 to 1. Lower it when data is missing, stale or thin.")
    data_gaps: list[str] = Field(description="Missing or unreliable data that limited this view.")


AgentName = Literal["financials", "news", "social", "market", "macro"]


class AgentReport(BaseModel):
    agent: AgentName
    status: Literal["ok", "failed", "skipped"]
    findings: AgentFindings | None = None
    error: str | None = None      # shown to the user if status != "ok"
    error_kind: str | None = None  # AIUnavailable.kind, e.g. "rate_limit_daily"
    checks: list[str] = []        # notes from code-side verification of the AI's output
    model: str | None = None

    def clamp(self) -> "AgentReport":
        """Keep model-produced numbers inside their allowed ranges."""
        if self.findings:
            f = self.findings
            f.confidence = min(max(f.confidence, 0.0), 1.0)
            for a in f.assessments:
                a.score = min(max(a.score, 0.0), 10.0)
        return self
