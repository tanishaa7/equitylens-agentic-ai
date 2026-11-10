"""Judge ("father agent") — the final, explained verdict.

Code decides, the AI explains:
1. Python computes everything that is a number or a decision:
   - head-to-head table over the deck's six lenses (Growth, Returns,
     Profitability, Safety, Cash quality, Valuation) with an Edge per metric
   - weighted score per company: agent score × planner weight × agent confidence
   - who is stronger (or "too close to call"), overall confidence, what changed
2. The AI (judge model) writes the plain-English verdict, reasons, things to
   watch, and a bull / bear case + red flags per company — citing facts [J…].
3. Code checks the AI's text (citations, numbers, no buy/sell wording, names
   the computed leader). If the AI is unavailable or its text fails the checks,
   a template verdict built from the numbers is shown instead — the user still
   gets an answer.

    verdict = await run_judge(plan, companies, reports, debate)
"""

from __future__ import annotations

import logging
from typing import Callable, Literal

from pydantic import BaseModel, Field

from agents.common import FactSheet, advice_words, match_ticker, unmatched_numbers
from agents.debate import DebateReport
from agents import llm
from agents.llm import AIUnavailable, generate, model_for
from agents.planner import Plan
from agents.schemas import AgentReport
from data.common import fmt_pct
from data.ratios import FORMULAS

log = logging.getLogger("equitylens.judge")

TOO_CLOSE = 0.5   # score points (0–10) — below this gap there is no clear winner
LABELS = {"financials": "Financials", "news": "News", "social": "Social",
          "market": "Market data", "macro": "Macro & sector"}
DISCLAIMER = ("EquityLens compares companies using public data. It does not predict share prices "
              "and is not investment advice. Check the cited sources before you decide anything.")


# ---------------------------------------------------------------- output shapes

class LensRow(BaseModel):
    lens: str
    metric: str
    better: Literal["higher", "lower"]
    values: dict[str, str]          # ticker -> formatted value
    edge: str | None                # ticker with the better value; None = tie / n/a
    source: str


class CitedPoint(BaseModel):
    text: str
    sources: list[str] = []         # "J4 — <fact> [<source>]"


class CompanyView(BaseModel):
    ticker: str
    name: str
    bull_case: CitedPoint | None = None
    bear_case: CitedPoint | None = None
    red_flags: list[CitedPoint] = []
    what_changed: list[str] = []


class Verdict(BaseModel):
    status: Literal["ok", "fallback"]          # fallback = template text from the numbers
    leader: str | None                         # ticker, or None when too close to call
    too_close: bool
    scores: dict[str, float]                   # weighted score per ticker (0–10)
    score_breakdown: dict[str, dict[str, float]]   # agent -> ticker -> score
    effective_weights: dict[str, float]        # agent -> weight actually used
    lens_table: list[LensRow]
    lens_edges: dict[str, str | None]          # lens -> ticker (None = split / n/a)
    confidence: float
    confidence_label: Literal["High", "Medium", "Low"]
    headline: str
    reasons: list[CitedPoint] = []
    watch: list[CitedPoint] = []
    companies: list[CompanyView] = []
    checks: list[str] = []
    note: str | None = None                    # e.g. why the template was used
    error_kind: str | None = None
    model: str | None = None
    disclaimer: str = DISCLAIMER


class CitedOut(BaseModel):
    text: str = Field(description="One point in 1–2 plain sentences.")
    fact_ids: list[str] = Field(description="IDs of the facts it relies on, e.g. ['J3', 'J17'].")


class CompanyOut(BaseModel):
    ticker: str = Field(description="Ticker exactly as given.")
    bull_case: CitedOut = Field(description="The best honest case for this company, in one point.")
    bear_case: CitedOut = Field(description="The main honest case for caution, in one point.")
    red_flags: list[CitedOut] = Field(description="0–3 specific concerns worth checking.")


class JudgeOut(BaseModel):
    headline: str = Field(description="1–2 sentences: 'Fundamentally stronger: <company> — <its "
                                      "two biggest advantages from the facts>. But <its main "
                                      "weakness from the facts>, so <that> is the thing to watch.'")
    headline_fact_ids: list[str] = Field(description="Fact IDs behind the headline.")
    reasons: list[CitedOut] = Field(description="3–5 reasons behind the verdict, most important first.")
    watch: list[CitedOut] = Field(description="1–3 things to keep an eye on.")
    companies: list[CompanyOut] = Field(description="One entry per company.")


# ---------------------------------------------------------------- deterministic layer

def _y0(r, key):
    return r["by_year"][0][key] if r["by_year"] else None


def _fcf_margin(r):
    if not r["by_year"]:
        return None
    y = r["by_year"][0]
    return None if y["free_cash_flow"] is None or not y["revenue"] else y["free_cash_flow"] / y["revenue"]


def _x(v):
    return f"{v:.1f}x"


def _r2(v):
    return f"{v:.2f}"


def _r1(v):
    return f"{v:.1f}"


def _days(v):
    return f"{v:.0f} days"


# (lens, metric, getter, better, formatter, absolute tie tolerance, key for N/A + formula)
LENS_METRICS: list[tuple[str, str, Callable, str, Callable, float, str]] = [
    ("Growth", "3-year revenue CAGR", lambda r: r["growth"]["revenue_cagr_3y"], "higher", fmt_pct, 0.005, "revenue_cagr_3y"),
    ("Growth", "3-year profit CAGR", lambda r: r["growth"]["profit_cagr_3y"], "higher", fmt_pct, 0.005, "profit_cagr_3y"),
    ("Returns", "ROE", lambda r: _y0(r, "roe"), "higher", fmt_pct, 0.005, "roe"),
    ("Returns", "ROCE", lambda r: _y0(r, "roce"), "higher", fmt_pct, 0.005, "roce"),
    ("Profitability", "EBITDA margin", lambda r: _y0(r, "ebitda_margin"), "higher", fmt_pct, 0.005, "ebitda_margin"),
    ("Profitability", "Net margin", lambda r: _y0(r, "net_margin"), "higher", fmt_pct, 0.005, "net_margin"),
    ("Safety", "Debt / equity", lambda r: _y0(r, "debt_to_equity"), "lower", _r2, 0.02, "debt_to_equity"),
    ("Safety", "Interest coverage", lambda r: _y0(r, "interest_coverage"), "higher", _x, 0.5, "interest_coverage"),
    ("Cash quality", "Cash conversion (OCF / net profit)", lambda r: _y0(r, "cash_conversion"), "higher", _r2, 0.03, "cash_conversion"),
    ("Cash quality", "Free cash flow margin", _fcf_margin, "higher", fmt_pct, 0.005, "free_cash_flow"),
    ("Cash quality", "Receivable days", lambda r: _y0(r, "receivable_days"), "lower", _days, 2, "receivable_days"),
    ("Valuation", "P/E", lambda r: r["valuation"]["pe"], "lower", _r1, 0.5, "pe"),
    ("Valuation", "P/B", lambda r: r["valuation"]["pb"], "lower", _r1, 0.2, "pb"),
    ("Valuation", "EV/EBITDA", lambda r: r["valuation"]["ev_ebitda"], "lower", _r1, 0.5, "ev_ebitda"),
]
LENSES = ["Growth", "Returns", "Profitability", "Safety", "Cash quality", "Valuation"]
EXTRA_FORMULAS = {"free_cash_flow": "Free cash flow / Revenue"}


def _edge(vals: dict[str, float], better: str, abs_tol: float) -> str | None:
    """Ticker with the best value — None if fewer than 2 values or best ≈ runner-up."""
    if len(vals) < 2:
        return None
    ranked = sorted(vals.items(), key=lambda kv: kv[1], reverse=(better == "higher"))
    (best, a), (_, b) = ranked[0], ranked[1]
    if abs(a - b) <= max(abs_tol, 0.05 * max(abs(a), abs(b))):
        return None
    return best


def _not_like_for_like(r: dict) -> bool:
    """A big one-year revenue jump (merger / demerger) makes multi-year growth misleading."""
    return any(w.startswith("Revenue changed") for w in r["data_warnings"])


def lens_table(companies: list[dict]) -> list[LensRow]:
    rows = []
    for lens, metric, get, better, fmt, tol, key in LENS_METRICS:
        values, nums, periods, distorted = {}, {}, [], []
        for c in companies:
            r = c["ratios"]
            if key in r["not_applicable"]:
                values[c["ticker"]] = "n/a"
                continue
            v = get(r)
            values[c["ticker"]] = "—" if v is None else fmt(v)
            if lens == "Growth" and v is not None and _not_like_for_like(r):
                values[c["ticker"]] += " *"
                distorted.append(c["ticker"])
            if v is not None:
                nums[c["ticker"]] = v
            if r["by_year"]:
                periods.append(f"{c['ticker']} {r['by_year'][0]['period']}")
        if all(v == "n/a" for v in values.values()):
            continue   # e.g. ROCE for a bank-vs-bank comparison
        formula = FORMULAS.get(key) or EXTRA_FORMULAS.get(key, "")
        note = (f" · * {', '.join(distorted)}: includes a merger-size revenue jump, so no edge "
                "is given" if distorted else "")
        rows.append(LensRow(lens=lens, metric=metric, better=better, values=values,
                            edge=None if distorted else _edge(nums, better, tol),
                            source=f"Yahoo Finance statements ({', '.join(periods)}) · {formula}{note}"))
    return rows


def lens_edges(rows: list[LensRow]) -> dict[str, str | None]:
    out = {}
    for lens in LENSES:
        tally: dict[str, int] = {}
        for r in rows:
            if r.lens == lens and r.edge:
                tally[r.edge] = tally.get(r.edge, 0) + 1
        if not tally:
            out[lens] = None
            continue
        best = sorted(tally.items(), key=lambda kv: kv[1], reverse=True)
        out[lens] = best[0][0] if len(best) == 1 or best[0][1] > best[1][1] else None
    return out


def weighted_scores(plan: Plan, reports: list[AgentReport]):
    """Score = Σ(weight × confidence × agent score) / Σ(weight × confidence), over agents that
    succeeded. Low-confidence agents (e.g. headline-only news) therefore count less."""
    tickers = [c.ticker for c in plan.companies]
    breakdown, eff = {}, {}
    for r in reports:
        if r.status != "ok" or not r.findings or r.agent not in plan.weights:
            continue
        scores = {a.ticker: a.score for a in r.findings.assessments}
        if not all(t in scores for t in tickers):
            continue
        breakdown[r.agent] = scores
        eff[r.agent] = plan.weights[r.agent] * r.findings.confidence
    total = sum(eff.values())
    if total == 0:
        return {t: 5.0 for t in tickers}, breakdown, {}
    eff = {a: w / total for a, w in eff.items()}
    scores = {t: round(sum(eff[a] * breakdown[a][t] for a in eff), 2) for t in tickers}
    return scores, breakdown, {a: round(w, 3) for a, w in eff.items()}


def overall_confidence(plan: Plan, reports: list[AgentReport], debate: DebateReport | None) -> float:
    ok = [r for r in reports if r.status == "ok" and r.findings and r.agent in plan.weights]
    if not ok:
        return 0.0
    coverage = sum(plan.weights[r.agent] for r in ok)          # share of planned weight that ran
    mean = (sum(plan.weights[r.agent] * r.findings.confidence for r in ok)
            / sum(plan.weights[r.agent] for r in ok))
    conf = mean * (0.5 + 0.5 * coverage)
    if debate is None or debate.status in ("failed", "skipped"):
        conf -= 0.1
    return round(max(min(conf, 1.0), 0.0), 2)


def confidence_label(c: float) -> str:
    return "High" if c >= 0.7 else "Medium" if c >= 0.45 else "Low"


def _signed_pct(x):
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def what_changed(c: dict) -> list[str]:
    q = (c.get("ratios") or {}).get("latest_quarter")
    if not q or not q["current"]:
        return []
    lines = [f"{q['current']['period']}: revenue {_signed_pct(q['revenue_yoy'])} vs a year ago, "
             f"{_signed_pct(q['revenue_qoq'])} vs the previous quarter",
             f"Net profit {_signed_pct(q['net_income_yoy'])} vs a year ago, "
             f"{_signed_pct(q['net_income_qoq'])} vs the previous quarter"]
    pts = q["ebitda_margin_change_qoq_pts"]
    if pts is not None and "ebitda_margin" not in c["ratios"]["not_applicable"]:
        lines.append(f"EBITDA margin {pts:+.1f} pts vs the previous quarter")
    stale = [w for w in c["ratios"]["data_warnings"] if w.startswith("Latest quarter on file")]
    return lines + stale


# ---------------------------------------------------------------- facts for the AI

def build_facts(plan, companies, reports, debate, scores, breakdown, eff, rows, edges,
                leader, too_close) -> tuple[FactSheet, str]:
    sheet = FactSheet("J")
    names = {c.ticker: c.name for c in plan.companies}
    out = []

    def add(ticker, text, source):
        out.append(f"[{sheet.add(ticker, text, source)}] {text}")

    score_txt = ", ".join(f"{t} {s:.1f}" for t, s in scores.items())
    add(None, ("Computed result: too close to call — weighted scores " if too_close
               else f"Computed result: {names[leader]} ({leader}) is fundamentally stronger — "
                    "weighted scores ") + f"{score_txt} out of 10",
        "EquityLens scoring: agent score × weight × confidence")
    for a, w in eff.items():
        add(None, f"{LABELS[a]} analyst (counts {w:.0%} of the score): "
                  + ", ".join(f"{t} {s:.1f}" for t, s in breakdown[a].items()), f"{LABELS[a]} agent")
    for w in plan.warnings:
        add(None, f"Comparison warning: {w}", "EquityLens planner")

    out.append("\n== Head-to-head (computed from statements)")
    for r in rows:
        vals = ", ".join(f"{t} {v}" for t, v in r.values.items())
        add(None, f"{r.lens} · {r.metric} ({r.better} is better): {vals} → edge "
                  f"{r.edge or 'none (too close or n/a)'}", r.source)
    lens_txt = "; ".join(f"{lens}: {t or 'split'}" for lens, t in edges.items())
    add(None, f"Lens edges — {lens_txt}", "EquityLens head-to-head table")

    for c in companies:
        t = c["ticker"]
        out.append(f"\n== {t} — {names[t]}")
        for line in what_changed(c):
            add(t, f"What changed: {line}", "Yahoo Finance quarterly statements")
        for w in c["ratios"]["data_warnings"]:
            add(t, f"Data warning: {w}", "EquityLens data checks")
        for r in reports:
            if r.status == "ok" and r.findings:
                for a in r.findings.assessments:
                    if a.ticker == t:
                        for flag in a.red_flags:
                            add(t, f"{LABELS[r.agent]} red flag: {flag}", f"{LABELS[r.agent]} agent")

    if debate and debate.arguments:
        out.append("\n== Bull vs bear debate")
        replies = {}
        for rb in debate.rebuttals:
            replies.setdefault(rb.target_id, []).append(rb)
        for a in debate.arguments:
            add(a.ticker, f"{a.side.title()} on {a.ticker} ({a.strength}): {a.point}",
                f"Debate {a.id}")
            for rb in replies.get(a.id, []):
                verb = "concedes" if rb.concedes else "replies"
                add(a.ticker, f"{rb.side.title()} {verb} to {a.id}: {rb.response}", f"Debate rebuttal")

    company_list = ", ".join(f"{t} ({n})" for t, n in names.items())
    prompt = (f"Companies: {company_list}.\n\n" + "\n".join(out)
              + "\n\nWrite the verdict, reasons, things to watch, and for each company a bull case, "
                "a bear case and red flags.")
    return sheet, prompt


JUDGE_SYSTEM = """You are the Judge in EquityLens, a research tool that compares 2–3 Indian \
listed companies in the same sector for do-it-yourself investors. Specialist analysts and a \
bull-vs-bear debate have already done the work. Code has computed the scores, the head-to-head \
edges and which company is fundamentally stronger. Your job is to explain that result clearly \
and fairly — do not change it.

How to write:
- Headline pattern: "Fundamentally stronger: <company> — <its two biggest advantages>. But \
<its main weakness>, so <that> is the thing to watch." Fill it ONLY from the facts: every \
advantage and weakness must appear in the head-to-head table or the findings. Never mention a \
metric that is missing or not applicable (e.g. debt or ROCE for banks). Name the stronger \
company exactly as the computed result says. If the result is "too close to call", say there \
is no clear winner and what separates them.
- Reasons: the 3–5 facts that matter most, most important first. Prefer the head-to-head \
numbers and Financials over News, Market data or Social.
- Respect the debate: where a side conceded a point, treat it as settled.
- Each company gets its best honest bull case and its main bear case, plus 0–3 red flags.
- Things to watch: what could change the picture (e.g. a stale quarter, rising debt, valuation).

Rules:
- Use only the numbered facts [J…], and cite the IDs every point relies on.
- Copy numbers exactly as written in the facts. Never calculate new numbers.
- This is research, not advice. Never say buy, sell, accumulate, target price, invest in, \
or tell the reader what to do with their money. Describe, compare and explain.
- Short, plain sentences for a retail investor. Explain a technical term in a few words."""


# ---------------------------------------------------------------- AI output checks

def _cited(item: CitedOut, sheet: FactSheet,
           rows: list[LensRow] = ()) -> tuple[CitedPoint | None, str | None]:
    facts = sheet.resolve(", ".join(item.fact_ids))
    if not facts:
        return None, "no citation"
    if unmatched_numbers(item.text, [n for f in facts for n in f.numbers]):
        return None, "number not in cited facts"
    if advice_words(item.text):
        return None, "advice wording"
    if rows and unsupported_metrics(item.text, rows):
        return None, "mentions a metric that isn't available"
    return CitedPoint(text=item.text, sources=[f"{f.id} — {f.text} [{f.source}]" for f in facts]), None


# Words that refer to a metric — if that metric isn't in the head-to-head table for anyone
# (e.g. debt / ROCE / EBITDA for banks), a headline using the word is making it up.
METRIC_WORDS = {
    "debt": ("Debt / equity", "Interest coverage"), "leverage": ("Debt / equity",),
    "roce": ("ROCE",), "ebitda": ("EBITDA margin", "EV/EBITDA"),
    "cash flow": ("Free cash flow margin", "Cash conversion (OCF / net profit)"),
}


def unsupported_metrics(text: str, rows: list[LensRow]) -> list[str]:
    available = {r.metric for r in rows if any(v not in ("n/a", "—") for v in r.values.values())}
    t = text.lower()
    return [w for w, metrics in METRIC_WORDS.items()
            if w in t and not any(m in available for m in metrics)]


def _no_winner_claimed(headline: str) -> bool:
    """For a too-close result the headline must not announce a winner."""
    return "stronger:" not in headline.lower()


def _names_leader(headline: str, leader: str, name: str) -> bool:
    h = headline.lower()
    first_word = name.split()[0].lower()
    return leader.split(".")[0].lower() in h or name.lower() in h or first_word in h


def apply_ai(v: Verdict, out: JudgeOut, sheet: FactSheet, names: dict[str, str]) -> Verdict:
    tickers = list(names)
    dropped: dict[str, int] = {}

    def keep(item):
        point, why = _cited(item, sheet, v.lens_table)
        if why:
            dropped[why] = dropped.get(why, 0) + 1
        return point

    head_ok = (not unmatched_numbers(out.headline, sheet.all_numbers())
               and not advice_words(out.headline)
               and not unsupported_metrics(out.headline, v.lens_table)
               and (_no_winner_claimed(out.headline) if v.too_close
                    else _names_leader(out.headline, v.leader, names[v.leader])))
    if head_ok:
        v.headline = out.headline
    else:
        v.checks.append("The AI headline didn't match the computed result or failed a check, "
                        "so a headline built from the numbers is shown.")
    v.reasons = [p for p in (keep(i) for i in out.reasons[:5]) if p]
    v.watch = [p for p in (keep(i) for i in out.watch[:3]) if p]

    views = {cv.ticker: cv for cv in v.companies}
    for co in out.companies:
        t = match_ticker(co.ticker, tickers)
        if t is None:
            continue
        cv = views[t]
        cv.bull_case = keep(co.bull_case) or cv.bull_case
        cv.bear_case = keep(co.bear_case) or cv.bear_case
        flags = [p for p in (keep(f) for f in co.red_flags[:3]) if p]
        cv.red_flags = flags or cv.red_flags
    for why, n in dropped.items():
        v.checks.append(f"Removed {n} AI point(s): {why}.")
    return v


# ---------------------------------------------------------------- template fallback

def _and(items: list[str]) -> str:
    items = [i.lower() for i in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def template_headline(v: Verdict, names: dict[str, str]) -> str:
    score_txt = " vs ".join(f"{s:.1f}" for s in v.scores.values())
    if v.too_close:
        who = " vs ".join(f"{names[t]} {s:.1f}" for t, s in v.scores.items())
        return f"Too close to call on the evidence available (weighted scores {who} out of 10)."
    won = [lens for lens, t in v.lens_edges.items() if t == v.leader]
    s = f"Fundamentally stronger: {names[v.leader]}"
    s += f" — ahead on {_and(won)}" if won else ""
    s += f" (weighted score {score_txt} out of 10)."
    for t in v.scores:
        lost = [lens for lens, e in v.lens_edges.items() if e == t and t != v.leader]
        if lost:
            s += f" {names[t]} leads on {_and(lost)}, so weigh that too."
    return s


def _debate_fallback(views: dict[str, CompanyView], debate: DebateReport | None):
    """Without the AI judge, use the strongest verified debate arguments."""
    if not debate:
        return
    rank = {"strong": 0, "moderate": 1, "weak": 2}
    for a in sorted(debate.arguments, key=lambda a: rank.get(a.strength, 3)):
        cv = views[a.ticker]
        point = CitedPoint(text=a.point, sources=a.sources)
        if a.side == "bull" and cv.bull_case is None:
            cv.bull_case = point
        if a.side == "bear" and cv.bear_case is None:
            cv.bear_case = point


# ---------------------------------------------------------------- run

async def run_judge(plan: Plan, companies: list[dict], reports: list[AgentReport],
                    debate: DebateReport | None, use_ai: bool = True,
                    skip_reason: str | None = None) -> Verdict:
    names = {c.ticker: c.name for c in plan.companies}
    scores, breakdown, eff = weighted_scores(plan, reports)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    too_close = len(ranked) < 2 or ranked[0][1] - ranked[1][1] < TOO_CLOSE
    leader = None if too_close else ranked[0][0]
    rows = lens_table(companies)
    edges = lens_edges(rows)
    conf = overall_confidence(plan, reports, debate)

    v = Verdict(status="fallback", leader=leader, too_close=too_close, scores=scores,
                score_breakdown=breakdown, effective_weights=eff, lens_table=rows,
                lens_edges=edges, confidence=conf, confidence_label=confidence_label(conf),
                headline="", companies=[CompanyView(ticker=c["ticker"], name=names[c["ticker"]],
                                                    what_changed=what_changed(c))
                                        for c in companies])
    v.headline = template_headline(v, names)
    views = {cv.ticker: cv for cv in v.companies}
    _debate_fallback(views, debate)
    for r in reports:   # fallback red flags: Financials' own concerns
        if r.agent == "financials" and r.status == "ok" and r.findings:
            for a in r.findings.assessments:
                views[a.ticker].red_flags = [CitedPoint(text=f, sources=["Financials agent"])
                                             for f in a.red_flags[:3]]

    if not use_ai:
        v.note = skip_reason or "The written explanation was skipped."
        return v

    sheet, prompt = build_facts(plan, companies, reports, debate, scores, breakdown, eff,
                                rows, edges, leader, too_close)
    try:
        out = await generate(JUDGE_SYSTEM, prompt, JudgeOut, tier="judge", label="judge")
    except AIUnavailable as e:
        log.warning("judge failed: %s", e)
        v.note, v.error_kind = e.user_message, e.kind
        return v
    used = [u["model"] for u in llm.usage_log if u["label"] == "judge"]
    v.model = used[-1] if used else model_for("judge")
    if v.model != model_for("judge"):
        v.checks.append(f"The judge model was busy, so {v.model} wrote the explanation.")
    v = apply_ai(v, out, sheet, names)
    v.status = "ok"
    return v
