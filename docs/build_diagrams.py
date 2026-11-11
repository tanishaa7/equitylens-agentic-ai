"""Builds docs/how-equitylens-works.html: four hand-laid SVG diagrams + tables
(architecture, data flow, scoring, weightage). Edit page_template.html for prose and
this file for the diagrams, then run:

    python3 docs/build_diagrams.py     (prints warnings for labels that may overflow a box)

The output is a standalone HTML page — open it in any browser. No dependencies."""

from html import escape as E
from pathlib import Path

OUT = Path(__file__).with_name("how-equitylens-works.html")
WARN = []


def tw(text, size=11, bold=False, mono=False):
    per = 6.7 if mono else (7.3 if bold and size >= 13 else 6.25 if size <= 11 else 7.0)
    return len(text) * per * (size / 11 if size > 11 and not bold else 1)


class Fig:
    def __init__(self, fid, w, h, label):
        self.fid, self.w, self.h, self.label = fid, w, h, label
        self.parts = []

    def box(self, x, y, w, h, kind, title, lines=(), mono_lines=False, dashed=False):
        cls = f"n {kind}" + (" dashed" if dashed else "")
        self.parts.append(f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/>')
        cx = x + w / 2
        if not lines:
            self.parts.append(f'<text class="t-title" x="{cx}" y="{y + h / 2 + 4.5}" text-anchor="middle">{E(title)}</text>')
        else:
            self.parts.append(f'<text class="t-title" x="{cx}" y="{y + 22}" text-anchor="middle">{E(title)}</text>')
            for i, ln in enumerate(lines):
                c = "t-sub mono" if mono_lines else "t-sub"
                self.parts.append(f'<text class="{c}" x="{cx}" y="{y + 40 + 16 * i}" text-anchor="middle">{E(ln)}</text>')
        for t, sz, b in [(title, 13, True)] + [(l, 11, False) for l in lines]:
            if tw(t, sz, b, mono_lines and sz == 11) > w - 14:
                WARN.append(f"{self.fid}: '{t}' may overflow box w={w}")

    def card(self, x, y, w, title, lines):
        h = 30 + 16 * len(lines) + 8
        self.parts.append(f'<rect class="card" x="{x}" y="{y}" width="{w}" height="{h}" rx="6"/>')
        self.parts.append(f'<text class="t-cardtitle" x="{x + 12}" y="{y + 20}">{E(title)}</text>')
        for i, ln in enumerate(lines):
            self.parts.append(f'<text class="t-sub mono" x="{x + 12}" y="{y + 40 + 16 * i}">{E(ln)}</text>')
            if tw(ln, 11, mono=True) > w - 20:
                WARN.append(f"{self.fid}: card line '{ln}' may overflow w={w}")
        return h

    def diamond(self, cx, cy, w, h, kind, title, sub=None):
        pts = f"{cx},{cy - h / 2} {cx + w / 2},{cy} {cx},{cy + h / 2} {cx - w / 2},{cy}"
        self.parts.append(f'<polygon class="n {kind}" points="{pts}"/>')
        dy = -3 if sub else 4.5
        self.parts.append(f'<text class="t-title" x="{cx}" y="{cy + dy}" text-anchor="middle">{E(title)}</text>')
        if sub:
            self.parts.append(f'<text class="t-sub" x="{cx}" y="{cy + 14}" text-anchor="middle">{E(sub)}</text>')

    def arrow(self, pts, label=None, lx=None, ly=None, anchor="start", dashed=False, both=False, accent=False):
        p = " ".join(f"{a},{b}" for a, b in pts)
        cls = "edge" + (" dash" if dashed else "") + (" accent" if accent else "")
        start = f' marker-start="url(#{self.fid}-s)"' if both else ""
        self.parts.append(f'<polyline class="{cls}" points="{p}" marker-end="url(#{self.fid}-a)"{start}/>')
        if label:
            self.parts.append(f'<text class="t-label mono" x="{lx}" y="{ly}" text-anchor="{anchor}">{E(label)}</text>')

    def line(self, pts, dashed=True):
        p = " ".join(f"{a},{b}" for a, b in pts)
        self.parts.append(f'<polyline class="edge{" dash" if dashed else ""}" points="{p}"/>')

    def text(self, x, y, s, cls="t-note", anchor="start"):
        self.parts.append(f'<text class="{cls}" x="{x}" y="{y}" text-anchor="{anchor}">{E(s)}</text>')

    def raw(self, s):
        self.parts.append(s)

    def svg(self):
        defs = (f'<defs><marker id="{self.fid}-a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
                f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="currentColor"/></marker>'
                f'<marker id="{self.fid}-s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
                f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="currentColor"/></marker>'
                f'<pattern id="{self.fid}-hatch" width="8" height="8" patternUnits="userSpaceOnUse" '
                f'patternTransform="rotate(45)"><rect width="8" height="8" fill="none"/>'
                f'<line x1="0" y1="0" x2="0" y2="8" stroke="currentColor" stroke-width="2" opacity="0.45"/></pattern></defs>')
        return (f'<svg class="dg" viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{E(self.label)}" '
                f'xmlns="http://www.w3.org/2000/svg">{defs}{"".join(self.parts)}</svg>')


# ============================================================ 1. Architecture
f1 = Fig("f1", 1120, 770, "Architecture: a visitor's browser talks to one Streamlit Cloud process. The orchestrator "
         "runs the planner, data collector, five specialist agents, the bull-vs-bear debate and the judge. AI steps "
         "go through one Gemini wrapper; data goes through a disk cache to Yahoo Finance, Google News and Reddit.")
f1.box(60, 20, 320, 56, "user", "Visitor's browser", ["opens the .streamlit.app link"])
f1.box(760, 20, 320, 56, "ext", "GitHub repo", ["tanishaa7/equitylens-agentic-ai"])
f1.raw('<rect class="boundary" x="24" y="110" width="1072" height="525" rx="12"/>')
f1.text(44, 134, "Streamlit Community Cloud · one Python process", "t-group")
f1.arrow([(220, 76), (220, 150)], "HTTPS", 228, 100)
f1.arrow([(920, 76), (920, 110)], "redeploys on git push", 912, 98, "end")
f1.box(60, 150, 320, 62, "code", "Website · app.py", ["form · live progress · verdict + 5 tabs"])
f1.box(420, 150, 240, 62, "code", "Usage limits · limits.py", ["3 new / visitor / hour · 40 / day"])
f1.box(700, 150, 380, 62, "code", "Run log · runlog.py", ["logs/runs.jsonl · logs/equitylens.log"])
f1.arrow([(380, 181), (420, 181)], "gate", 400, 174, "middle")
f1.box(60, 262, 1020, 62, "code", "Orchestrator · agents/orchestrator.py",
       ["plan → saved result? → limit → fetch data → specialists (parallel) → bull vs bear → judge → save + log"])
f1.arrow([(220, 212), (220, 262)], "run_comparison()", 228, 242)
f1.arrow([(890, 262), (890, 212)], "1 line per run", 898, 242)
row_d = [(60, 170, "code", "Planner", ["names → NSE tickers", "sector check · weights"], "1 plan"),
         (280, 180, "code", "Data collector", ["data/collect.py", "5 sources, in parallel"], "2 data"),
         (500, 200, "ai", "Specialist agents ×5", ["Financials · News · Market", "Macro · Social"], "3 analyse"),
         (740, 160, "ai", "Bull vs bear", ["agents/debate.py", "cases + rebuttals"], "4 debate"),
         (920, 160, "mixed", "Judge", ["scores: code", "explanation: AI"], "5 decide")]
for x, w, k, t, ls, lab in row_d:
    cx = x + w / 2 if t != "Data collector" else 370
    f1.box(x, 376, w, 74, k, t, ls)
    f1.arrow([(cx, 324), (cx, 376)], lab, cx + 8, 354)
f1.box(280, 490, 180, 74, "store", "Disk cache · store.py", ["saved results: same day", "data: 30 min – 24 h"])
f1.arrow([(255, 324), (255, 527), (280, 527)], "saved?", 247, 470, "end")
f1.arrow([(370, 450), (370, 490)], "read / write", 378, 474, both=True)
f1.box(500, 490, 200, 74, "code", "Facts + checks", ["agents/common.py", "used by every AI step"])
f1.box(740, 490, 340, 74, "code", "Gemini wrapper · agents/llm.py",
       ["2 calls at once · retries · fallback model", "10-min cool-down · key from Secrets"])
f1.arrow([(600, 450), (600, 490)], "build facts · verify", 608, 474)
f1.arrow([(700, 527), (740, 527)], "JSON", 720, 520, "middle")
f1.arrow([(820, 450), (820, 490)], "AI calls", 828, 474)
f1.arrow([(1000, 450), (1000, 490)], "AI call", 1008, 474)
f1.box(60, 680, 250, 62, "ext", "Yahoo Finance", ["symbol search · prices · statements"])
f1.box(330, 680, 190, 62, "ext", "Google News RSS", ["India edition headlines"])
f1.box(540, 680, 160, 62, "ext", "Reddit API", ["optional · needs keys"], dashed=True)
f1.box(740, 680, 340, 62, "ext ai-edge", "Google Gemini API · free tier", ["gemini-3.5-flash-lite · gemini-3.8-flash"])
f1.arrow([(145, 450), (145, 680)], "search (cached 24 h)", 153, 612)
f1.arrow([(290, 564), (290, 680)])
f1.arrow([(410, 564), (410, 680)])
f1.arrow([(450, 564), (450, 600), (620, 600), (620, 680)], "fetch on cache miss", 462, 592, dashed=True)
f1.arrow([(910, 564), (910, 680)], "HTTPS · JSON schema", 918, 612)

# ============================================================ 2. Data flow
TX, TW_, PX, PW, CX, CW = 20, 230, 300, 300, 650, 450
rows = [
    dict(t="Planner", k="code", l=["names → NSE tickers (Yahoo search)", "same-sector check · picks agents"],
         card=("Plan", ["companies: TCS.NS, INFY.NS — name, sector,", "  industry, market cap",
                        "agents: financials, news, market, macro", "weights: 44% · 22% · 17% · 17%",
                        "  (social skipped: no Reddit keys)", "data needed: financials, news, macro"]),
         term=("Stop with a message", ["ambiguous name → pick one", "not found · different sector"], "blocked"),
         down="Plan"),
    dict(t="Saved result today?", k="store", l=["key: date + sorted tickers + settings"],
         card=("cache/store/result/*.json", ["complete verdicts only", "kept until midnight, India time"]),
         term=("Show saved result", ["0 AI calls · loads in seconds"], "yes"), down="no"),
    dict(t="Usage limit", k="code", l=["3 new per visitor per hour", "40 per day for the whole site"],
         card=("In memory only", ["hashed visitor id → times of new runs", "resets when the app restarts"]),
         term=("Show limit message", ["nothing fetched, no AI used"], "over"), down="allowed"),
    dict(t="Fetch data", k="code", l=["one thread per company + macro", "every source goes through the cache"],
         card=("Per company (cache time)", [
             "market (30 min): price, 52-week range, returns",
             "  1m / 6m / 1y / 3y, volatility, max drawdown",
             "financials (12 h): 4 years + quarters, in ₹",
             "  (USD reporters converted at period-end rate)",
             "→ ratios (code): margins, ROE, ROCE, D/E, cover,",
             "  FCF, cash conversion, CAGR, P/E, P/B, EV/EBITDA,",
             "  latest quarter, data warnings",
             "news (3 h): up to 30 headlines, last 12 months",
             "social (3 h): Reddit posts (only with keys)",
             "macro, shared (3 h): Nifty 50, sector index,",
             "  USD/INR, Brent, sector + RBI headlines"]),
         term=("Data error message", ["Yahoo unreachable → try again"], "fails"), down="company data + macro"),
    dict(t="Fact sheets", k="code", l=["each agent's slice → numbered facts", "source + formula on every fact"],
         card=("One Financials fact (real example)", ["[F9] ROCE: FY23 57.6% → FY24 62.6% →",
                                                      "     FY25 63.9% → FY26 58.5%",
                                                      "source: Yahoo annual statements ·",
                                                      "        EBIT / avg capital employed"]),
         down="fact sheets"),
    dict(t="Specialists ask Gemini", k="ai", l=["4–5 in parallel, 2 calls at a time", "answer must fit a JSON schema"],
         card=("AgentFindings (raw AI answer)", ["summary", "per company: score 0–10, key points,",
                                                 "  red flags, evidence {claim, \"F9\"}",
                                                 "confidence 0–1 · data gaps"]),
         ext=("Google Gemini API", ["flash-lite, falls back to flash"], "JSON"), down="raw JSON"),
    dict(t="Check every claim", k="code", l=["drops claims with no source, a number", "not in the facts, or buy/sell words"],
         card=("AgentReport", ["status: ok | failed | skipped", "findings (cleaned) · score clamped 0–10",
                               "confidence ≤ ceiling set by data quality", "checks: \"Removed 1 claim(s) …\""]),
         term=("No verdict", ["Financials failed", "(retried once if AI was busy)"], "fails"), down="verified reports"),
    dict(t="Bull vs bear", k="ai", l=["reports → facts [E1…]", "2 opening cases + 2 rebuttals"],
         card=("Debate", ["arguments BULL-1 … BEAR-6:", "  company, point, strength, facts",
                          "rebuttals: answers BEAR-2, concedes y/n", "every argument checked like step 7"]),
         down="verified debate"),
    dict(t="Judge", k="mixed", l=["code: table, scores, winner, confidence", "AI: headline + reasons from [J1…]"],
         card=("Verdict", ["winner (or too close) · weighted scores", "six-lens table with edges · confidence",
                           "headline · why · things to watch", "per company: bull, bear, red flags,",
                           "  what changed last quarter"]),
         term=("Numbers-only verdict", ["AI busy or daily limit:", "same table + scores, template text"], "AI down"),
         down="Verdict"),
    dict(t="Deliver", k="code", l=["website · cache · log"],
         card=("ComparisonRun", ["→ website: verdict + 5 tabs", "→ cache: saved for the day (if complete)",
                                 "→ logs/runs.jsonl: 1 line, no keys or IPs"])),
]
f2 = Fig("f2", 1120, 100, "Data flow: what exists after each step, from the typed names to the verdict.")
f2.box(PX, 16, PW, 44, "user", "You type: TCS · Infosys")
y = 104
prev_bottom = 60
for i, r in enumerate(rows):
    ph = 26 + 16 * len(r["l"]) + 14
    f2.arrow([(450, prev_bottom), (450, y)], rows[i - 1]["down"] if i else "names", 458, (prev_bottom + y) / 2 + 4)
    f2.box(PX, y, PW, ph, r["k"], r["t"], r["l"])
    ch = f2.card(CX, y, CW, *r["card"])
    f2.line([(PX + PW, y + ph / 2), (CX, y + ph / 2)])
    th = 0
    side = r.get("term") or r.get("ext")
    if side:
        title, lines, lab = side
        th = 26 + 16 * len(lines) + 14
        f2.box(TX, y, TW_, th, "ext" if "ext" in r else "term", title, lines)
        mid = y + min(ph, th) / 2
        if "ext" in r:
            f2.arrow([(PX, mid), (TX + TW_, mid)], lab, (PX + TX + TW_) / 2, mid - 7, "middle", both=True)
        else:
            f2.arrow([(PX, mid), (TX + TW_, mid)], lab, (PX + TX + TW_) / 2, mid - 7, "middle")
    prev_bottom = y + ph
    y += max(ph, ch, th) + 46
f2.h = y - 30

# ============================================================ 3. Scoring
f3 = Fig("f3", 1120, 1225, "Scoring: lane A turns analyst scores into a weighted score and a winner or "
         "too-close call; lane B builds the six-lens head-to-head table. Both feed the judge, whose headline must "
         "pass code checks or is replaced by a template built from the numbers.")
f3.text(40, 26, "A · analyst scores → weighted score", "t-lane")
f3.text(610, 26, "B · six-lens head-to-head (code only)", "t-lane")
A = [(44, 62, "ai", "Each analyst scores each company 0–10",
      ["5 = in line with peers in this comparison"]),
     (136, 62, "code", "Clean the answer", ["score clamped to 0–10 · bad claims removed"]),
     (228, 62, "code", "Cap each analyst's confidence", ["ceiling set by data quality (see 3.1)"]),
     (320, 86, "code", "Weighted score per company",
      ["Σ(weight × confidence × score) ÷ Σ(weight × confidence)", "example: TCS 5.93 · Infosys 5.55"])]
for i, (yy, h, k, t, ls) in enumerate(A):
    f3.box(40, yy, 470, h, k, t, ls)
    if i:
        f3.arrow([(275, A[i - 1][0] + A[i - 1][1]), (275, yy)])
f3.arrow([(275, 406), (275, 430)])
f3.diamond(275, 475, 300, 90, "code", "Top two within 0.5?", "example gap: 0.38")
f3.box(40, 560, 220, 62, "code", "Too close to call", ["no winner named"])
f3.box(290, 560, 220, 62, "code", "Winner = top score", ["named in the headline"])
f3.arrow([(125, 475), (125, 560)], "yes", 133, 520)
f3.arrow([(425, 475), (425, 560)], "no", 433, 520)
B = [(44, 118, "code", "14 metrics from the statements",
      ["Growth: 3-year revenue & profit CAGR", "Returns: ROE, ROCE", "Profitability: EBITDA & net margin",
       "Safety: debt/equity, interest cover", "Cash: OCF/profit, FCF margin, receivable days"]),
     (192, 102, "code", "Edge per metric",
      ["better value wins only if ahead by more than 5%", "merger-inflated growth: starred, no edge",
       "banks: ROCE, D/E, EBITDA etc. marked n/a", "valuation (P/E, P/B, EV/EBITDA): lower wins"]),
     (334, 102, "code", "Edge per lens",
      ["the company with the most metric wins", "tie → split", "example: TCS → Returns, Profitability",
       "Infosys → Growth, Safety, Cash, Valuation"])]
for i, (yy, h, k, t, ls) in enumerate(B):
    f3.box(610, yy, 470, h, k, t, ls)
    if i:
        f3.arrow([(845, B[i - 1][0] + B[i - 1][1]), (845, yy)])
f3.text(620, 466, "Shown and explained, but it never changes", "t-note")
f3.text(620, 482, "the weighted score in lane A.", "t-note")
f3.box(240, 680, 640, 86, "code", "Overall confidence",
       ["average analyst confidence (by weight) × (0.5 + 0.5 × share of weight that ran)",
        "− 0.1 if the debate failed · High ≥ 70% · Medium ≥ 45% · Low below",
        "example: 82% → High"])
f3.arrow([(150, 622), (150, 652), (300, 652), (300, 680)])
f3.arrow([(400, 622), (400, 680)], "winner / too close", 408, 656)
f3.box(240, 800, 640, 70, "ai", "Judge AI writes the explanation",
       ["reads facts [J1…]: result, scores, lens table, red flags, debate",
        "writes headline · 3–5 reasons · things to watch · bull/bear per company"])
f3.arrow([(560, 766), (560, 800)], "confidence", 568, 788)
f3.arrow([(1000, 436), (1000, 835), (880, 835)], "lens table + edges", 992, 640, "end")
f3.diamond(430, 958, 360, 96, "code", "Headline passes checks?")
f3.arrow([(560, 870), (560, 890), (430, 890), (430, 910)], "AI headline", 568, 886)
f3.box(680, 900, 400, 118, "code", "Code checks the headline",
       ["names the computed winner", "(or no “stronger:” when too close)", "every number appears in the facts",
        "no buy / sell / target-price wording", "no metric that is n/a (e.g. debt for banks)"])
f3.line([(610, 958), (680, 958)])
f3.box(40, 1050, 260, 52, "ai", "Show the AI's headline")
f3.box(330, 1050, 420, 68, "code", "Template headline from the numbers",
       ["e.g. “Too close to call (weighted scores", "TCS 5.9 vs Infosys 5.5 out of 10).”"])
f3.arrow([(250, 958), (170, 958), (170, 1050)], "yes", 178, 1010)
f3.arrow([(430, 1006), (430, 1050)], "no", 438, 1032)
f3.box(240, 1150, 640, 52, "code", "Verdict: winner or too close · scores · confidence · six-lens table · cited reasons")
f3.arrow([(170, 1102), (170, 1176), (240, 1176)])
f3.arrow([(540, 1118), (540, 1150)])

# ============================================================ 4. Weightage
f4 = Fig("f4", 1120, 520, "Weightage: base weights 40/20/15/15/10; Social is skipped and the rest re-balanced to "
         "44.4/22.2/16.7/16.7; each is multiplied by the analyst's confidence (0.90, 0.75, 0.85, 0.70), giving 82.5% "
         "in total; dividing by 0.825 gives effective weights 48.4/20.2/17.2/14.2.")
BX, BW, BH = 250, 780, 40
AG = [("fin", "Financials"), ("news", "News"), ("mkt", "Market"), ("mac", "Macro"), ("soc", "Social")]
bars = [
    (40, "Base weights", "planner.py WEIGHTS", [40, 20, 15, 15, 10], None),
    (160, "Planned", "agents that will run", [44.4, 22.2, 16.7, 16.7, 0], None),
    (280, "× confidence", "each analyst's, capped", [40.0, 16.7, 14.2, 11.7, 0],
     ["0.444 × 0.90", "0.222 × 0.75", "0.167 × 0.85", "0.167 × 0.70"]),
    (420, "Effective weights", "÷ 0.825 → 100% again", [48.4, 20.2, 17.2, 14.2, 0], None),
]
for yy, name, sub, vals, under in bars:
    f4.text(235, yy + 17, name, "t-title", "end")
    f4.text(235, yy + 33, sub, "t-sub", "end")
    x = BX
    for (key, label), v in zip(AG, vals):
        if not v:
            continue
        w = BW * v / 100
        f4.raw(f'<rect class="seg {key}" x="{x:.1f}" y="{yy}" width="{w:.1f}" height="{BH}"/>')
        txt = f"{label} {v:g}%" if w > tw(f"{label} {v:g}%") + 12 else f"{v:g}%"
        f4.raw(f'<text class="t-seg" x="{x + w / 2:.1f}" y="{yy + 25}" text-anchor="middle">{E(txt)}</text>')
        x += w
    if sum(vals) < 99:
        rest = BW * (100 - sum(vals)) / 100
        f4.raw(f'<rect class="hatch" x="{x:.1f}" y="{yy}" width="{rest:.1f}" height="{BH}" fill="url(#f4-hatch)"/>')
        f4.raw(f'<text class="t-sub" x="{x + rest / 2:.1f}" y="{yy + 25}" text-anchor="middle">−{100 - sum(vals):.1f}% (confidence)</text>')
    if under:
        x = BX
        for (key, _), v, u in zip(AG, vals, under):
            w = BW * v / 100
            f4.raw(f'<text class="t-label mono" x="{x + w / 2:.1f}" y="{yy + BH + 16}" text-anchor="middle">{E(u)}</text>')
            x += w
ops = [(80, 160, "Social skipped (no Reddit keys) → divide the other four by 0.90 so they add up to 100%"),
       (200, 280, "multiply each by that analyst's confidence: Financials 0.90 · News 0.75 · Market 0.85 · Macro 0.70"),
       (336, 420, "add up (0.825) and divide by it, so the weights add up to 100% again")]
for top, bottom, label in ops:
    f4.arrow([(BX + 6, top + 8), (BX + 6, bottom - 8)], accent=True)
    f4.text(BX + 20, (top + bottom) / 2 + 4, label, "t-op")
f4.text(BX, 490, "Weighted score = Σ effective weight × analyst score  →  "
        "TCS 0.484×6.0 + 0.202×6.0 + 0.172×6.0 + 0.142×5.5 = 5.93", "t-formula mono")

print("\n".join(WARN) or "no overflow warnings")

HTML = Path(__file__).with_name("page_template.html").read_text()
HTML = (HTML.replace("{{FIG1}}", f1.svg()).replace("{{FIG2}}", f2.svg())
        .replace("{{FIG3}}", f3.svg()).replace("{{FIG4}}", f4.svg()))
# standalone document: the template is a fragment (title, style, main)
HTML = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        + HTML.replace("<main>", "</head>\n<body>\n<main>", 1) + "\n</body>\n</html>\n")
OUT.write_text(HTML)
print("wrote", OUT, f"{len(HTML) // 1024} KB")
