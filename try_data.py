"""Step 1 try-out: fetch all data for 2–3 companies and print a side-by-side summary.

Run:  .venv/bin/python try_data.py TCS INFY
      .venv/bin/python try_data.py TITAN KALYANKJIL
Full raw output is saved to cache/ as JSON for inspection.
"""

from __future__ import annotations

import json
import sys
import warnings
from datetime import date

import config
from data.common import fmt_cr, fmt_pct, normalize_ticker
from data.collect import collect_company
from data.macro import get_macro_data

warnings.filterwarnings("ignore")


def num(x, digits=1):
    return "—" if x is None else f"{x:.{digits}f}"


def print_table(companies: list[dict]):
    cols = [c["ticker"] for c in companies]
    rows = []

    def add(label, fn):
        vals = []
        for c in companies:
            try:
                vals.append(fn(c))
            except (KeyError, IndexError, TypeError):
                vals.append("—")
        rows.append((label, vals))

    y0 = lambda c: c["ratios"]["by_year"][0]  # latest fiscal year
    add("Name", lambda c: c["market"]["name"][:24])
    add("Industry", lambda c: (c["market"]["industry"] or "—")[:24])
    add("Latest FY", lambda c: y0(c)["period"])
    add("Revenue", lambda c: fmt_cr(y0(c)["revenue"]))
    add("Net income", lambda c: fmt_cr(y0(c)["net_income"]))
    add("Revenue growth (YoY)", lambda c: fmt_pct(y0(c)["revenue_growth"]))
    add("Revenue CAGR 3y", lambda c: fmt_pct(c["ratios"]["growth"]["revenue_cagr_3y"]))
    add("Profit CAGR 3y", lambda c: fmt_pct(c["ratios"]["growth"]["profit_cagr_3y"]))
    add("EBITDA margin", lambda c: fmt_pct(y0(c)["ebitda_margin"]))
    add("Net margin", lambda c: fmt_pct(y0(c)["net_margin"]))
    add("ROE", lambda c: fmt_pct(y0(c)["roe"]))
    add("ROCE", lambda c: fmt_pct(y0(c)["roce"]))
    add("Debt / Equity", lambda c: num(y0(c)["debt_to_equity"], 2))
    add("Free cash flow", lambda c: fmt_cr(y0(c)["free_cash_flow"]))
    add("Cash conversion (OCF/NI)", lambda c: num(y0(c)["cash_conversion"], 2))
    add("Market cap", lambda c: fmt_cr(c["ratios"]["valuation"]["market_cap"]))
    add("P/E (ours)", lambda c: num(c["ratios"]["valuation"]["pe"]))
    add("P/E (Yahoo, cross-check)", lambda c: num(c["market"]["yahoo_trailing_pe"]))
    add("EV / EBITDA", lambda c: num(c["ratios"]["valuation"]["ev_ebitda"]))
    add("1y price return", lambda c: fmt_pct(c["market"]["returns"].get("1y")))
    add("Below 52w high", lambda c: fmt_pct(c["market"]["below_52w_high"]))
    add("Latest quarter", lambda c: c["ratios"]["latest_quarter"]["current"]["period"])
    add("  Revenue QoQ", lambda c: fmt_pct(c["ratios"]["latest_quarter"]["revenue_qoq"]))
    add("  Revenue YoY", lambda c: fmt_pct(c["ratios"]["latest_quarter"]["revenue_yoy"]))
    add("  Net income QoQ", lambda c: fmt_pct(c["ratios"]["latest_quarter"]["net_income_qoq"]))
    add("News articles (1y)", lambda c: str(c["news"]["total_found"]))
    add("Reddit posts", lambda c: str(len(c["social"]["posts"])))

    w0 = max(len(r[0]) for r in rows) + 2
    w = 26
    print("".ljust(w0) + "".join(c.ljust(w) for c in cols))
    print("-" * (w0 + w * len(cols)))
    for label, vals in rows:
        print(label.ljust(w0) + "".join(str(v).ljust(w) for v in vals))


def main(argv: list[str]):
    if not 2 <= len(argv) <= 3:
        print("Usage: python try_data.py TICKER1 TICKER2 [TICKER3]   e.g. TCS INFY")
        sys.exit(1)
    tickers = [normalize_ticker(t) for t in argv]
    print(f"Fetching {', '.join(tickers)} ...\n")

    companies = []
    for t in tickers:
        c = collect_company(t)
        if "error" in c:
            print(f"!! {t}: {c['error']}")
            sys.exit(1)
        companies.append(c)

    print_table(companies)

    sectors = {(c["market"]["sector"], c["market"]["industry"]) for c in companies}
    if len({s for s, _ in sectors}) > 1:
        print(f"\n!! Different sectors: {sectors} — comparison may not be meaningful.")
    sector, industry = companies[0]["market"]["sector"], companies[0]["market"]["industry"]
    macro = get_macro_data(sector, industry)

    print("\n== Macro / sector")
    si, b = macro["sector_index"], macro["benchmark"]
    if si:
        print(f"{si['name']} 1y: {fmt_pct(si['returns'].get('1y'))}  vs  "
              f"{b['name']} 1y: {fmt_pct(b['returns'].get('1y'))}  "
              f"({num(si['vs_nifty_1y_pts'])} pts)")
    for s in macro["macro_series"].values():
        print(f"{s['name']}: {num(s['latest'], 2)}  (1y change {fmt_pct(s['change_1y'])})")

    for c in companies:
        r = c["ratios"]
        if r["is_financial"]:
            print(f"\n(i) {c['ticker']} is a financial company — EBITDA, ROCE, FCF, D/E etc. "
                  "marked not applicable. Bank metrics (NIM, GNPA, CASA) need filings data.")
        for w in r["data_warnings"]:
            print(f"(!) {c['ticker']}: {w}")

    for c in companies:
        print(f"\n== Latest news: {c['market']['name']}  [{c['news']['status']}]")
        for a in c["news"]["articles"][:5]:
            print(f"  {(a['published'] or '')[:10]}  {a['title'][:90]}  ({a['publisher']})")
        print(f"   Reddit: {c['social']['status']}")
        for p in c["social"]["posts"][:3]:
            print(f"  r/{p['subreddit']}  ▲{p['score']}  {p['title'][:80]}")

    out = config.CACHE_DIR / f"data_{'_'.join(argv).upper()}_{date.today()}.json"
    out.write_text(json.dumps({"companies": companies, "macro": macro}, indent=2, default=str))
    print(f"\nFull data saved to {out.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1:])
