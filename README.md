# EquityLens (prototype)

Multi-agent AI that compares 2–3 Indian listed companies in the same sector
and explains which looks fundamentally stronger, with sources. Research tool,
not investment advice.

**Live demo:** _add your `*.streamlit.app` link here after deploying (see "Deploy")._

> ⚠️ Research prototype. It compares companies using free public data (Yahoo Finance,
> Google News) and a free AI model. Figures can be incomplete or wrong — check the cited
> sources. It does not predict prices and is not investment advice or a SEBI-registered
> research service.

## Pipeline

User input → Planner → 5 specialist agents in parallel (Financials, News,
Social, Market data, Macro) → Bull vs bear debate → Father agent (judge)
→ Comparison report.

## Setup

```bash
cd Finance_agentic
python3.12 -m venv .venv          # already done
.venv/bin/pip install -r requirements.txt
cp .env.example .env              # then add your free GEMINI_API_KEY
.venv/bin/python check_setup.py
```

## AI provider: Gemini free tier

- All AI calls go through `agents/llm.py` — agents never call Gemini directly,
  so switching provider later means changing that one file.
- Specialists use `gemini-3.5-flash-lite` (higher free limits), the judge uses
  `gemini-3.8-flash`. Override in `.env`.
- Free-tier prompts may be used by Google to improve its products — fine here
  because we only send public company data.
- When the free limit is hit, the user sees a friendly message instead of a crash:
  - per-minute limit → short waits are retried automatically; otherwise
    "please wait N seconds and try again"
  - daily limit → "Today's free AI limit has been reached… try again after
    1:30 PM IST" (quota resets at midnight Pacific time)
  - server busy / bad key / broken AI output → each has its own message
- When a model is overloaded (503), rate-limited or hangs, the call falls back
  to the other model (judge ↔ specialist), and the struggling model is skipped
  for 10 minutes. Busy errors are retried after 5s and 10s; a hanging call is
  cut off after 45s when a backup exists (90s otherwise). If Financials fails
  because the AI was busy, it is retried once after the other agents finish.
- Wording that sounds like advice (buy/sell, "entry point", target price …) is
  removed from every agent, the debate and the judge.
- Live-tested (10 Nov 2026): TCS vs Infosys and HDFC Bank vs ICICI Bank —
  9 AI calls, ~25k tokens, 75–120 s per comparison (most of it waiting for the
  busy judge model before falling back).
- Test it offline: `.venv/bin/python -m tests.run_all` (all suites)

Tickers use Yahoo Finance format: NSE `TCS.NS`, BSE `500570.BO`.

## Try the data layer

```bash
.venv/bin/python try_data.py TCS INFY
.venv/bin/python try_data.py TITAN KALYANKJIL
```

## Website (Streamlit)

```bash
.venv/bin/streamlit run app.py --server.address localhost      # http://localhost:8501, this computer only
.venv/bin/streamlit run app.py --server.address localhost -- --demo cache/run_TCS.NS_INFY.NS_2026-11-10.json
                                                  # opens with a saved comparison (no AI quota)
```

- Type 2–3 companies (names or NSE symbols) or click an example; progress is
  shown live (checking companies → data → agents → debate → judge).
- Result: verdict headline + confidence, weighted scores, why, a bull / bear /
  red flags / what-changed card per company, things to watch — every point
  with numbered sources. Tabs: head-to-head table (edge highlighted, formulas),
  bull vs bear debate, each analyst's findings, news & sector context, how it works.
- Problems show as banners: daily AI limit ("try again after 1:30 PM IST"),
  AI busy, ambiguous names (click the company you meant), different sectors.
- `--server.address localhost` keeps a local run private to this computer;
  Streamlit usage stats are off (`.streamlit/config.toml`).
- Public-demo limits (`limits.py`): 3 new comparisons per visitor per hour and
  40 per day for the whole site; saved results are free. Change with
  `MAX_RUNS_PER_VISITOR_HOUR` / `MAX_RUNS_PER_DAY`. Visitors are told apart by a
  hash of their IP, kept in memory only.

## Deploy (public link) — Streamlit Community Cloud, free

GitHub only stores the code; Streamlit Community Cloud runs it and gives a link
anyone can open.

1. Go to https://share.streamlit.io and sign in with your GitHub account.
2. **Create app** → *Deploy a public app from GitHub* → repo
   `tanishaa7/equitylens-agentic-ai`, branch `main`, main file `app.py`.
3. **Advanced settings** → Python **3.12** → **Secrets**, paste (your key, in quotes):
   ```toml
   GEMINI_API_KEY = "AQ.your-key"
   # optional: MAX_RUNS_PER_VISITOR_HOUR = "3"
   #           MAX_RUNS_PER_DAY = "40"
   ```
4. **Deploy**. The first start takes a few minutes; then share the
   `https://<name>.streamlit.app` link.

Notes:
- Never commit `.env` or `.streamlit/secrets.toml` (both are git-ignored).
- Everyone using the link spends your free Gemini quota — the limits above
  protect it; when Google's daily limit is hit, visitors see the
  "try again after 1:30 PM IST" message.
- The free cloud app sleeps after a few days without visitors; the first
  visitor clicks "wake up" and waits about a minute. Cache and logs reset when
  it restarts.
- Yahoo Finance sometimes rate-limits cloud servers; the app then shows
  "Couldn't reach Yahoo Finance … try again". yfinance data is meant for
  personal / research use — check Yahoo's terms before any commercial use.

## Caching & logging (Step 10)

**Cache** (`data/store.py`, files in `cache/store/`, git-ignored) — saves the free
AI quota and Yahoo calls:

| What | Kept for |
|---|---|
| A finished comparison (complete verdict only) | rest of the day (IST), any order / spelling of the same companies |
| Share price & profile | 30 min |
| Financial statements | 12 h |
| News, Reddit, sector & macro | 3 h |
| Company name / symbol lookups (incl. "not found") | 24 h |

- Failures (news unavailable, numbers-only verdicts …) are never cached, so the
  next try can do better. A broken cache file is just a miss.
- Website: a reused result shows "Saved result from 3:42 PM IST" with a
  **Run fresh** button. Terminal: `compare.py … --refresh`.
- Switch off with `EQUITYLENS_CACHE=0`; bump `CACHE_VERSION` in
  `agents/orchestrator.py` when prompts or scoring change.

**Logs** (`logs/`, git-ignored; no keys or personal data):
- `logs/runs.jsonl` — one line per comparison: inputs, tickers, status, cache hit,
  timings, AI calls, tokens per model, agent statuses, verdict.
- `logs/equitylens.log` — every AI call (model, tokens, seconds) and every
  warning (busy, fallback, removed claims), rotating 3 × 1 MB.
- Today's usage (comparisons, cache hits, AI calls, tokens, limit hits) is shown
  in the website's sidebar.

## Run a comparison in the terminal

```bash
.venv/bin/python compare.py TCS Infosys              # plan → data → agents → debate → verdict
.venv/bin/python compare.py TITAN "kalyan jewellers" --details
```

The orchestrator (`agents/orchestrator.py`) never crashes the comparison:
- each agent runs in parallel; one that errors or takes > 4 min becomes
  "failed" and the rest carry on (status `partial`, with one banner message)
- if the AI is unusable for everyone (daily limit, bad key) the remaining
  agents are cancelled at once and the user sees ONE message
- a verdict needs Financials plus at least one other agent (`can_judge`)
- statuses: ok · partial · blocked · data_error · ai_unavailable · insufficient

## Bull vs bear debate (`agents/debate.py`)

Runs after the specialists, only when there's enough to judge.
- Round 1 (parallel): the **Bull** makes the strongest honest case FOR each
  company, the **Bear** the strongest case AGAINST each company.
- Round 2 (parallel): each side answers the other's key points and must
  **concede** fair ones. Set `DEBATE_ROUNDS=1` in `.env` to skip this round
  (saves 2 AI calls per comparison).
- Debaters only see the specialists' verified findings as facts `[E1]…`
  (with each agent's weight and confidence). Arguments that cite nothing,
  use a number not in the cited facts, or name an unknown company are
  dropped; rebuttals to non-existent points are dropped.
- If the debate fails, the analysis is kept and the judge can still use the
  specialists — unless the AI is unusable (daily limit / bad key).

## Judge — the final verdict (`agents/judge.py`)

Code decides, the AI explains.
- **Computed in Python:** head-to-head table over the deck's six lenses
  (Growth, Returns, Profitability, Safety, Cash quality, Valuation) with an
  Edge per metric (ties within 5% get none; merger-inflated growth is starred
  and gets none); weighted score per company = agent score × planner weight ×
  agent confidence; the stronger company, or "too close to call" if the gap is
  under 0.5; overall confidence (High / Medium / Low); what changed last quarter.
- **Written by the AI** (judge model): headline in the deck's style, 3–5
  reasons, things to watch, and a bull case, bear case and red flags per
  company — every point citing facts `[J…]`.
- **Checked by code:** points that cite nothing, use a number not in the
  cited facts, or use buy/sell-style wording are removed. A headline that
  names a different winner than the computed one is replaced.
- **Never empty-handed:** if the AI is unavailable (daily limit, busy) the user
  still gets the verdict, table, scores, bull/bear (from the debate) and what
  changed — built from the numbers — plus one message explaining why the
  written explanation is missing.

AI calls per comparison: 4 specialists + 4 debate + 1 judge = 9 (social adds 1
with Reddit keys; `DEBATE_ROUNDS=1` saves 2).

## Try the Planner (no AI, no quota)

```bash
.venv/bin/python try_plan.py TCS Infosys
.venv/bin/python try_plan.py "kalyan jewellers" TITAN     # names work; quote multi-word names
```

Before anything is fetched or any AI is called, the Planner:
- resolves each input to an NSE/BSE company (symbol first, then name search)
- **blocks** with a clear message: not 2–3 companies, not found, ambiguous name
  (e.g. "Tata Motors" → TMCV / TMPV, user picks), the same company twice,
  different sectors (override: `--allow-cross-sector`)
- **warns** but runs: same sector / different industry, lender vs non-lender,
  20x+ size gap
- picks agents (Social skipped without Reddit keys), the data to fetch, and
  each agent's weight in the final score: financials 40%, news 20%, market 15%,
  macro 15%, social 10% — re-balanced to 100% over the agents that run

## Try an agent

```bash
.venv/bin/python try_agent.py financials TCS INFY --dry-run   # see the AI prompt, no AI call
.venv/bin/python try_agent.py all TCS INFY --dry-run          # all 5 prompts (~3k tokens total)
.venv/bin/python try_agent.py all TCS INFY                    # needs GEMINI_API_KEY
```

| Agent | Looks at | Confidence ceiling |
|---|---|---|
| financials | statements, ratios, valuation, latest quarter | 100% minus data warnings |
| news | last 15 Google News headlines per company | 75% (headlines only) |
| social | Reddit posts (skipped without Reddit keys) | 50% (opinion, noisy) |
| market | returns, volatility, drawdown vs Nifty 50 + sector index | 85% (past prices) |
| macro | sector index, USD/INR, Brent, sector + RBI news, debt/FX exposure | 70% (indirect) |

How an agent keeps the AI honest (`agents/common.py`):
- Python does all maths; the AI gets the numbers as numbered facts `[F1]…`,
  each with a source and formula.
- Every claim must cite fact IDs. Code then removes claims that cite nothing,
  claims whose numbers don't match the cited facts, and points with numbers
  that aren't in the data. What was removed is listed under `checks`.
- Confidence is capped by code from data quality (warnings, banks, short history).
- If the AI is unavailable (e.g. daily limit), the agent returns status
  `failed` with the friendly message instead of crashing. With no data
  (e.g. no Reddit keys) it returns `skipped` with a short reason.
- Headlines and Reddit posts are third-party text: the AI is told to treat
  them as data only, and any invented number is removed anyway.

## Layout

```
data/
  financials.py   annual + quarterly statements (Yahoo), USD reporters converted to INR
  ratios.py       deterministic ratios, formulas, data-quality warnings, bank mode
  market.py       profile, market cap, returns, volatility, drawdown
  news.py         Google News RSS (India), no key needed
  social.py       Reddit (needs API keys in .env; public endpoint is blocked)
  macro.py        sector index vs Nifty 50, USD/INR, Brent, sector + RBI headlines
  collect.py      fetch everything (or only the parts an agent needs) for a ticker
  lookup.py       symbol / company-name search (NSE preferred)
  store.py        on-disk JSON cache with expiry
agents/
  llm.py          Gemini wrapper: JSON output, retries, friendly limit messages
  schemas.py      AgentFindings / AgentReport — shared output for every agent
  common.py       numbered facts, citation + number checks, run/retry/cap
  financials.py   Financials agent: growth, profitability, balance sheet,
                  cash quality, valuation, latest quarter
  news.py         News agent (headlines)
  social.py       Social agent (Reddit sentiment, low weight)
  market.py       Market data agent (price behaviour and risk)
  macro.py        Macro & sector agent
  registry.py     list of specialists + what data each needs
  planner.py      resolve inputs, sector check, agents, weights (no AI)
  orchestrator.py plan → parallel data fetch → parallel agents → debate → status
  debate.py       bull vs bear: opening cases + rebuttals, citation checks
  judge.py        six-lens table, weighted scores, verdict + checks, fallback
tests/            offline tests (no API key needed) — run all: python -m tests.run_all
                  python -m tests.test_llm_errors / test_financials_agent /
                  test_specialists / test_planner /
                  test_orchestrator / test_debate / test_judge /
                  test_cache_logging
cache/            saved API responses (git-ignored)
config.py         keys, model names, paths
try_data.py       Step 1 try-out: side-by-side table for 2–3 tickers
try_agent.py      try one agent (or --dry-run to see its prompt)
try_plan.py       try the Planner
compare.py        full comparison from the terminal
app.py            Streamlit website
runlog.py         logging setup, per-run log, today's usage
.streamlit/       theme + local-only server settings
```

## Known data limits (free Yahoo data)

- Not audited-filing grade: some figures (e.g. HDFC Bank equity) disagree with
  Yahoo's own summary stats. `data_warnings` flags what it can detect.
- Quarterly history has gaps, so YoY quarter comparisons are sometimes missing,
  and some companies' latest quarter is stale (flagged).
- Banks: EBITDA/ROCE/FCF/D/E are marked not applicable. Bank metrics (NIM, GNPA,
  CASA) need BSE/NSE filings — a later upgrade.

## Build progress

- [x] Step 0 – Setup
- [x] Step 1 – Data layer
- [x] Step 2 – Output schema (+ Gemini wrapper with limit messages)
- [x] Step 3 – Financials agent (live Gemini run pending API key)
- [x] Step 4 – Other specialists (live Gemini run pending API key)
- [x] Step 5 – Planner
- [x] Step 6 – Orchestrator
- [x] Step 7 – Bull vs bear debate
- [x] Step 8 – Judge
- [x] Step 9 – Streamlit UI
- [x] Step 10 – Caching & logging
- [ ] Step 11 – Sanity testing
