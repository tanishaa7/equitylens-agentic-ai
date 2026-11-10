"""All specialist agents in one place: how to build their prompt, how to run
them, and what data each one needs (so we only fetch what's used)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agents import financials, macro, market, news, social


@dataclass(frozen=True)
class Specialist:
    build_prompt: Callable          # (companies, macro) -> (FactSheet, prompt)
    run: Callable                   # async (companies, macro) -> AgentReport
    needs: frozenset                # company data parts: financials / news / social
    needs_macro: bool


SPECIALISTS: dict[str, Specialist] = {
    "financials": Specialist(financials.build_prompt, financials.run_financials,
                             frozenset({"financials"}), False),
    "news": Specialist(news.build_prompt, news.run_news, frozenset({"news"}), False),
    "social": Specialist(social.build_prompt, social.run_social, frozenset({"social"}), False),
    "market": Specialist(market.build_prompt, market.run_market, frozenset(), True),
    "macro": Specialist(macro.build_prompt, macro.run_macro, frozenset({"financials"}), True),
}
