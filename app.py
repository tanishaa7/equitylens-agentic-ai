"""EquityLens — Streamlit website.

Run:  .venv/bin/streamlit run app.py
Demo: .venv/bin/streamlit run app.py -- --demo cache/run_TCS.NS_INFY.NS_2026-11-10.json
      (opens with a saved comparison, no AI quota used)
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

import limits
import runlog
from agents.orchestrator import LABELS, ComparisonRun, run_comparison
from data.common import fmt_pct
from data.store import IST

runlog.setup_logging()

st.set_page_config(page_title="EquityLens", page_icon="🔍", layout="wide")

EXAMPLES = [("TCS", "Infosys"), ("HDFC Bank", "ICICI Bank"), ("Titan", "Kalyan Jewellers")]
INPUT_KEYS = ["c1", "c2", "c3"]
STAGES = {"plan": "Checking companies", "cache": "Saved result", "data": "Fetching data",
          "agent": "Specialist agents",
          "debate": "Bull vs bear debate", "judge": "Judge", "done": "Done"}
CONF_COLOR = {"High": "green", "Medium": "orange", "Low": "red"}


def short(name: str, ticker: str) -> str:
    """Column-friendly name: 'Infosys' stays, 'Tata Consultancy Services' → 'TCS'."""
    return name if len(name) <= 14 else ticker.split(".")[0]


def md(text: str | None) -> str:
    """Make third-party / AI text safe for Streamlit markdown ($ would start LaTeX)."""
    return (text or "").replace("$", "\\$")


# ---------------------------------------------------------------- footnotes

class Footnotes:
    """Numbered sources: points show [1][2], the list sits in an expander below."""

    def __init__(self):
        self.items: list[str] = []

    def refs(self, sources: list[str]) -> str:
        nums = []
        for s in sources:
            if s not in self.items:
                self.items.append(s)
            nums.append(self.items.index(s) + 1)
        return "".join(f"<sup>[{n}]</sup>" for n in nums)

    def render(self, title="Sources"):
        if self.items:
            with st.expander(f"{title} ({len(self.items)})"):
                for i, s in enumerate(self.items, 1):
                    st.markdown(f"<small>[{i}] {md(s)}</small>", unsafe_allow_html=True)


def point(text: str, sources: list[str], notes: Footnotes, prefix: str = "- "):
    st.markdown(f"{prefix}{md(text)} {notes.refs(sources)}", unsafe_allow_html=True)


# ---------------------------------------------------------------- input

def set_example(a: str, b: str):
    st.session_state.update(c1=a, c2=b, c3="", pending=True)


def run_fresh():
    st.session_state.update(pending=True, refresh=True)


def pick_candidate(raw: str, ticker: str):
    for k in INPUT_KEYS:
        if st.session_state.get(k, "").strip() == raw.strip():
            st.session_state[k] = ticker
    st.session_state.pending = True


def input_section():
    st.markdown("### Compare 2–3 companies from the same sector")
    with st.form("compare"):
        cols = st.columns(3)
        for col, key, hint in zip(cols, INPUT_KEYS, ["e.g. TCS", "e.g. Infosys", "optional"]):
            col.text_input(f"Company {key[-1]}", key=key, placeholder=hint)
        st.checkbox("Allow companies from different sectors (less meaningful)", key="allow")
        submitted = st.form_submit_button("Compare", type="primary")
    st.caption("Try:")
    cols = st.columns(len(EXAMPLES))
    for col, (a, b) in zip(cols, EXAMPLES):
        col.button(f"{a} vs {b}", on_click=set_example, args=(a, b), width="stretch")
    return submitted


def visitor() -> str:
    """Anonymous visitor id (hashed IP) for the usage limits — memory only, never logged."""
    sid = st.session_state.setdefault("sid", uuid.uuid4().hex)
    try:
        ip = st.context.headers.get("X-Forwarded-For") or st.context.ip_address
    except Exception:
        ip = None
    return limits.visitor_id(ip, sid)


def gate() -> str | None:
    vid = visitor()
    msg = limits.check(vid)
    if msg is None:
        limits.record(vid)
    return msg


def run_with_progress(inputs: list[str], allow: bool, refresh: bool = False) -> ComparisonRun:
    with st.status("Starting …", expanded=True) as status:
        def on_event(stage: str, detail: str):
            status.update(label=f"{STAGES.get(stage, stage)} …")
            status.write(f"**{STAGES.get(stage, stage)}** · {md(detail)}")

        run = asyncio.run(run_comparison(inputs, allow_cross_sector=allow, on_event=on_event,
                                         refresh=refresh, gate=gate))
        bad = run.status in ("blocked", "data_error", "ai_unavailable", "insufficient", "limited")
        status.update(label=("Couldn't complete the comparison" if bad
                             else "Loaded today's saved result" if run.from_cache
                             else f"Done in {run.timings.get('total', 0):.0f} s"),
                      state="error" if bad else "complete", expanded=False)
    return run


# ---------------------------------------------------------------- results

def ist_time(utc_iso: str) -> str:
    try:
        return pd.Timestamp(utc_iso).tz_convert(IST).strftime("%-I:%M %p IST")
    except Exception:
        return "earlier today"


def banner(run: ComparisonRun):
    if run.from_cache:
        cols = st.columns([5, 1])
        cols[0].info(f"Saved result from {ist_time(run.started_at)} today — reused so no AI "
                     "quota was spent. Market data may have moved since.", icon="💾")
        cols[1].button("Run fresh", on_click=run_fresh, width="stretch",
                       help="Re-runs every analyst (about 9 AI calls).")
    msg = md(run.user_message)
    if run.status in ("blocked", "data_error"):
        st.error(msg, icon="⛔")
        for p in run.plan.problems:
            if p.candidates:
                st.write(f"Which one did you mean by **{md(p.input)}**?")
                cols = st.columns(len(p.candidates) + 1)
                for col, c in zip(cols, p.candidates):
                    col.button(f"{c.name} ({c.ticker})", key=f"pick_{p.input}_{c.ticker}",
                               on_click=pick_candidate, args=(p.input, c.ticker))
    elif run.status in ("ai_unavailable", "limited"):
        st.error(msg, icon="⏳")
    elif run.status in ("insufficient", "partial") and msg:
        st.warning(msg, icon="⚠️")
    for w in run.plan.warnings:
        st.info(md(w), icon="ℹ️")


def verdict_section(run: ComparisonRun):
    v = run.verdict
    names = {c.ticker: c.name for c in run.plan.companies}
    notes = Footnotes()

    with st.container(border=True):
        top = st.columns([4, 1])
        top[0].markdown(f"### {md(v.headline)}")
        color = CONF_COLOR[v.confidence_label]
        top[1].markdown(f"**Confidence**<br>:{color}[**{v.confidence_label}** · {v.confidence:.0%}]",
                        unsafe_allow_html=True)
        if v.status == "fallback":
            st.caption("Built from the numbers only — the written explanation wasn't available.")

        cols = st.columns(len(v.scores))
        for col, (t, s) in zip(cols, v.scores.items()):
            lead = " ✓" if t == v.leader else ""
            col.metric(f"{names[t]}{lead}", f"{s:.1f} / 10")
            col.progress(min(max(s / 10, 0.0), 1.0))
        st.caption("Weighted score = each analyst's score × its weight × its confidence. "
                   + ("Gap under 0.5 → too close to call." if v.too_close else ""))

        if v.reasons:
            st.markdown("**Why**")
            for p in v.reasons:
                point(p.text, p.sources, notes)

    cols = st.columns(len(v.companies))
    for col, cv in zip(cols, v.companies):
        with col.container(border=True):
            st.markdown(f"#### {md(cv.name)}  \n<small>{cv.ticker}</small>", unsafe_allow_html=True)
            if cv.bull_case:
                point(cv.bull_case.text, cv.bull_case.sources, notes, prefix="🟢 **Bull:** ")
            if cv.bear_case:
                point(cv.bear_case.text, cv.bear_case.sources, notes, prefix="🔴 **Bear:** ")
            if cv.red_flags:
                st.markdown("**Red flags**")
                for f in cv.red_flags:
                    point(f.text, f.sources, notes, prefix="- ⚠️ ")
            if cv.what_changed:
                st.markdown("**What changed (latest quarter)**")
                for line in cv.what_changed:
                    st.markdown(f"- {md(line)}")

    if v.watch:
        st.markdown("**Things to watch**")
        for p in v.watch:
            point(p.text, p.sources, notes)
    for ch in v.checks:
        st.caption(f"✓ Check: {md(ch)}")
    notes.render("Sources for the verdict")


def head_to_head_tab(run: ComparisonRun):
    v = run.verdict
    names = {c.ticker: short(c.name, c.ticker) for c in run.plan.companies}
    st.markdown("Computed by code from the statements — **the AI doesn't do the maths.** "
                "Edge = clearly better value (ties within 5% get none).")
    edges = " · ".join(f"**{lens}:** {md(names.get(t, 'split'))}" for lens, t in v.lens_edges.items())
    st.markdown(f"<small>{edges}</small>", unsafe_allow_html=True)

    rows = []
    for r in v.lens_table:
        row = {"Lens": r.lens, "Metric": r.metric, "Better": "↑ higher" if r.better == "higher" else "↓ lower"}
        row.update({names[t]: val for t, val in r.values.items()})
        row["Edge"] = names.get(r.edge, "—")
        rows.append(row)
    df = pd.DataFrame(rows)

    def highlight(row):
        return ["background-color: rgba(34,197,94,0.18); font-weight: 600"
                if col == row["Edge"] else "" for col in row.index]

    st.dataframe(df.style.apply(highlight, axis=1), hide_index=True, width="stretch",
                 height=35 * (len(df) + 1) + 3)
    if any("*" in val for r in v.lens_table for val in r.values.values()):
        st.caption("\\* includes a merger-size revenue jump, so growth isn't like-for-like and no edge is given.")
    with st.expander("Formulas and sources"):
        for r in v.lens_table:
            st.markdown(f"<small>**{r.metric}** — {md(r.source)}</small>", unsafe_allow_html=True)

    st.markdown("**Analyst scores** (0–10, 5 = in line with peers)")
    srows = []
    for agent, scores in v.score_breakdown.items():
        row = {"Analyst": LABELS[agent], "Counts for": f"{v.effective_weights.get(agent, 0):.0%}"}
        row.update({names[t]: f"{s:.1f}" for t, s in scores.items()})
        srows.append(row)
    if srows:
        st.dataframe(pd.DataFrame(srows), hide_index=True, width="stretch")


def debate_tab(run: ComparisonRun):
    d = run.debate
    if d is None:
        st.info("The debate didn't run.")
        return
    if d.error:
        st.warning(md(d.error))
    st.caption(f"{d.rounds_completed} round(s). Each side must cite facts; fair points are conceded.")
    replies: dict[str, list] = {}
    for r in d.rebuttals:
        replies.setdefault(r.target_id, []).append(r)
    for c in run.plan.companies:
        st.markdown(f"#### {md(c.name)}")
        cols = st.columns(2)
        for col, side, title in ((cols[0], "bull", "🟢 Bull case"), (cols[1], "bear", "🔴 Bear case")):
            with col.container(border=True):
                st.markdown(f"**{title}**")
                for a in (x for x in d.arguments if x.ticker == c.ticker and x.side == side):
                    st.markdown(f"- {md(a.point)} <small>({a.strength})</small>", unsafe_allow_html=True)
                    for r in replies.get(a.id, []):
                        tag = "✅ concedes" if r.concedes else "↩ replies"
                        st.markdown(f"  <small>&nbsp;&nbsp;{r.side.title()} {tag}: {md(r.response)}</small>",
                                    unsafe_allow_html=True)
    for ch in d.checks:
        st.caption(f"✓ Check: {md(ch)}")


def agents_tab(run: ComparisonRun):
    names = {c.ticker: c.name for c in run.plan.companies}
    for r in run.reports:
        label = LABELS[r.agent]
        w = run.plan.weights.get(r.agent)
        head = f"{label}" + (f" · weight {w:.0%}" if w else "")
        if r.status != "ok":
            icon = "⏭️" if r.status == "skipped" else "❌"
            st.markdown(f"{icon} **{head}** — {r.status}: {md(r.error)}")
            continue
        f = r.findings
        with st.expander(f"✅ {head} · confidence {f.confidence:.0%}"):
            st.markdown(md(f.summary))
            notes = Footnotes()
            for a in f.assessments:
                st.markdown(f"**{md(names.get(a.ticker, a.ticker))}** — {a.score:.1f}/10")
                for p in a.key_points:
                    st.markdown(f"- ➕ {md(p)}")
                for p in a.red_flags:
                    st.markdown(f"- ⚠️ {md(p)}")
                for e in a.evidence:
                    point(e.claim, [e.source], notes, prefix="- 📎 ")
            for g in f.data_gaps:
                st.caption(f"Data gap: {md(g)}")
            for ch in r.checks:
                st.caption(f"✓ Check: {md(ch)}")
            notes.render()


def context_tab(run: ComparisonRun):
    m = run.macro or {}
    if m:
        st.markdown("**Sector & economy**")
        si, b = m.get("sector_index"), m.get("benchmark") or {}
        cols = st.columns(3)
        if si:
            cols[0].metric(f"{si['name']} (1 year)", fmt_pct(si["returns"].get("1y")),
                           f"{si['vs_nifty_1y_pts']:+.1f} pts vs {b.get('name', 'Nifty 50')}"
                           if si.get("vs_nifty_1y_pts") is not None else None)
        ms = m.get("macro_series") or {}
        if ms.get("usd_inr", {}).get("latest"):
            cols[1].metric("USD/INR", f"{ms['usd_inr']['latest']:.2f}",
                           f"{fmt_pct(ms['usd_inr']['change_1y'])} in 1 year", delta_color="off")
        if ms.get("brent_usd", {}).get("latest"):
            cols[2].metric("Brent crude ($/bbl)", f"{ms['brent_usd']['latest']:.2f}",
                           f"{fmt_pct(ms['brent_usd']['change_1y'])} in 1 year", delta_color="off")
    cols = st.columns(len(run.companies) or 1)
    for col, c in zip(cols, run.companies):
        with col:
            st.markdown(f"**News · {md(c['market']['name'])}**")
            for a in (c.get("news") or {}).get("articles", [])[:10]:
                day = (a.get("published") or "")[:10]
                st.markdown(f"<small>{day} · {md(a.get('publisher'))}</small><br>"
                            f"[{md(a['title'])}]({a['link']})", unsafe_allow_html=True)
            for w in (c.get("ratios") or {}).get("data_warnings", []):
                st.caption(f"Data note: {md(w)}")
    if m.get("policy_headlines"):
        st.markdown("**RBI policy news**")
        for a in m["policy_headlines"][:6]:
            st.markdown(f"<small>{(a.get('published') or '')[:10]}</small> [{md(a['title'])}]({a['link']})",
                        unsafe_allow_html=True)


def about_tab(run: ComparisonRun):
    st.markdown(
        "1. **Planner** checks the companies are real and in the same sector (no AI).\n"
        "2. **Data** — statements, prices, news and the sector backdrop are fetched.\n"
        "3. **Specialist agents** (Financials, News, Market data, Macro, Social) each score the "
        "companies on their own lens and must cite a numbered fact for every claim.\n"
        "4. **Bull vs bear** argue for and against each company and rebut each other.\n"
        "5. **Judge** — code computes the scores and the head-to-head table; the AI explains them.\n\n"
        "Every AI claim is checked by code: claims without a source, with a number that isn't in "
        "the data, or worded like a buy/sell tip are removed.")
    tokens = sum(u["input_tokens"] + u["output_tokens"] for u in run.usage)
    models = sorted({u["model"] for u in run.usage})
    when = " when first run (reused since, at no cost)" if run.from_cache else ""
    st.caption(f"This comparison{when}: {len(run.usage)} AI calls · {tokens:,} tokens · "
               f"{', '.join(models) or 'no AI'} · {run.timings.get('total', 0):.0f} s · "
               f"started {run.started_at[:16].replace('T', ' ')} UTC")


def results(run: ComparisonRun):
    banner(run)
    if run.verdict is None:
        if run.reports and run.status not in ("blocked", "data_error"):
            agents_tab(run)
        return
    verdict_section(run)
    tabs = st.tabs(["📊 Head-to-head", "⚖️ Bull vs bear", "🧑‍💼 Analysts", "📰 News & context",
                    "ℹ️ How this works"])
    with tabs[0]:
        head_to_head_tab(run)
    with tabs[1]:
        debate_tab(run)
    with tabs[2]:
        agents_tab(run)
    with tabs[3]:
        context_tab(run)
    with tabs[4]:
        about_tab(run)


# ---------------------------------------------------------------- page

def main():
    st.markdown("# 🔍 EquityLens")
    st.markdown("**Compare, don't predict.** An AI research team compares Indian listed companies "
                "and explains which looks fundamentally stronger — with every number sourced.")

    demo = sys.argv[sys.argv.index("--demo") + 1] if "--demo" in sys.argv[:-1] else None
    if demo and "run" not in st.session_state and Path(demo).is_file():
        st.session_state.run = ComparisonRun.model_validate(json.loads(Path(demo).read_text()))

    submitted = input_section()
    if submitted or st.session_state.pop("pending", False):
        inputs = [st.session_state.get(k, "") for k in INPUT_KEYS]
        refresh = st.session_state.pop("refresh", False)
        st.session_state.run = run_with_progress(inputs, st.session_state.get("allow", False),
                                                 refresh=refresh)

    with st.sidebar:
        u = runlog.usage_today()
        st.markdown("**Today's usage**")
        st.caption(f"{u['comparisons']} comparison(s) · {u['from_cache']} from saved results  \n"
                   f"{u['ai_calls']} AI calls · {u['tokens']:,} tokens")
        if u["limit_hits"]:
            st.caption(f"⏳ Daily AI limit reached {u['limit_hits']} time(s) today.")
        st.caption("Same companies on the same day reuse the saved result.")
        st.caption(f"Free demo limits: {limits.MAX_RUNS_PER_VISITOR_HOUR} new comparisons per "
                   f"visitor per hour, {limits.MAX_RUNS_PER_DAY} per day for the whole site.")

    if "run" in st.session_state:
        results(st.session_state.run)

    st.divider()
    st.caption("EquityLens is a research prototype. It compares companies using public data "
               "(Yahoo Finance, Google News); it does not predict share prices and is not "
               "investment advice. Figures may be incomplete — check the cited sources.")


main()
