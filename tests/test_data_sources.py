"""Offline tests for the Phase 1 data changes — no network needed.

Run:  .venv/bin/python -m tests.test_data_sources
Quarter labels for non-March years, data coverage and filing deadlines,
results-season caching, the three-feed news merge, sector notes, and the NSE
filing collector + reader.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

import config
import data.news as news
from agents.financials import build_prompt, coverage_text
from agents.sector_notes import sector_note
from data import filings
from data.collect import financials_ttl, in_results_season, TTL
from data.common import quarter_label
from data.ratios import coverage, results_deadline

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import collect_nse_filings as collector  # noqa: E402


def q(period_end: str, rev: float = 100.0) -> dict:
    return {"period": "?", "period_end": period_end, "revenue": rev}


def test_quarter_labels():
    ts = pd.Timestamp
    assert quarter_label(ts("2026-09-30")) == "Q2 FY27"
    assert quarter_label(ts("2026-03-31")) == "Q4 FY26"
    assert quarter_label(ts("2025-12-31")) == "Q3 FY26"
    # CRISIL: year ends in December
    assert quarter_label(ts("2026-09-30"), 12) == "Q3 CY26"
    assert quarter_label(ts("2025-12-31"), 12) == "Q4 CY25"
    assert quarter_label(ts("2026-03-31"), 12) == "Q1 CY26"
    assert quarter_label(ts("2026-09-30"), 6) == "Q1 FY27 (Jun year-end)"
    print("[ok] quarter labels follow each company's own financial year")


def test_coverage_and_deadlines():
    assert results_deadline(date(2026, 9, 30)) == date(2026, 11, 14)      # 45 days
    assert results_deadline(date(2026, 3, 31)) == date(2026, 5, 30)       # year-end: 60 days

    # ICRA-like: Q2 FY26 missing in the middle
    fin = {"fy_end_month": 3, "basis": "consolidated",
           "annual": [{"period": "FY26", "revenue": 1}, {"period": "FY25", "revenue": 1}],
           "quarterly": [q("2026-09-30"), q("2026-06-30"), q("2026-03-31"), q("2025-12-31"),
                         q("2025-06-30")]}
    cov = coverage(fin, date(2026, 11, 15))
    assert cov["missing_quarters"] == ["Q2 FY26"], cov
    assert cov["not_in_source_yet"] == [] and cov["pending_note"] is None
    assert cov["quarters_on_file"][0] == "Q2 FY27"

    # HDFC-like: source stops at Q1 FY26
    fin = {"fy_end_month": 3, "annual": [],
           "quarterly": [q("2025-06-30"), q("2025-03-31")]}
    cov = coverage(fin, date(2026, 11, 15))
    assert cov["not_in_source_yet"] == ["Q2 FY26", "Q3 FY26", "Q4 FY26", "Q1 FY27", "Q2 FY27"], cov
    # Before the Q2 deadline, Q2 is only "may not be published yet", not a gap
    cov = coverage(fin, date(2026, 11, 5))
    assert cov["not_in_source_yet"][-1] == "Q1 FY27"
    assert "Q2 FY27" in cov["pending_note"] and "14 Nov 2026" in cov["pending_note"]

    text = coverage_text(cov)
    assert "consolidated" in text and "not in our source yet" in text
    print("[ok] coverage: interior gaps, quarters past the SEBI deadline, pending quarters")


def test_results_season_ttl():
    assert in_results_season(date(2026, 11, 13)) and in_results_season(date(2026, 10, 20))
    assert not in_results_season(date(2026, 10, 5)) and not in_results_season(date(2026, 12, 15))
    assert in_results_season(date(2026, 5, 25)) and not in_results_season(date(2026, 6, 15))
    assert financials_ttl(date(2026, 11, 13)) == TTL["financials_results_season"]
    assert financials_ttl(date(2026, 12, 15)) == TTL["financials"]
    print("[ok] financials refresh every 2 h in results season, 12 h otherwise")


def test_news_merge():
    def art(title, day="2026-11-01"):
        return {"title": title, "publisher": "ET", "published": f"{day}T00:00:00+00:00", "link": "x"}

    def fake(query, days=365, limit=30, exact=True):
        if "site:" in query:
            arts = [art("HDFC Bank Q2 profit rises"), art("Bank holidays: SBI, ICICI shut")]
        elif "CEO OR" in query:
            arts = [art("HDFC Bank CEO faces board questions", "2026-11-10"),
                    art("HDFC Bank opens 50 branches"),               # not governance
                    art("RBI keeps repo rate unchanged")]             # doesn't name the company
        else:
            arts = [art("HDFC Bank Q2 profit rises"), art("Nifty ends higher")]
        return {"articles": arts, "status": "ok", "source": {"url": "u"}}

    real = news.get_news
    news.get_news = fake
    try:
        r = news.get_company_news("HDFC Bank", "HDFCBANK")
    finally:
        news.get_news = real
    titles = {a["title"]: a for a in r["articles"]}
    assert "Bank holidays: SBI, ICICI shut" not in titles           # press: must name company
    assert "HDFC Bank opens 50 branches" not in titles               # governance: must be governance
    assert "RBI keeps repo rate unchanged" not in titles
    assert titles["HDFC Bank Q2 profit rises"]["feeds"] == ["general", "press"]
    assert titles["HDFC Bank CEO faces board questions"]["feed"] == "governance"
    assert r["articles"][0]["title"] == "HDFC Bank CEO faces board questions"   # newest first
    assert r["status"] == "ok"
    print("[ok] news: three feeds merged, deduped, off-topic press/governance headlines dropped")


def test_sector_notes():
    assert "advance" in sector_note("Financial Data & Stock Exchanges", "Financial Services")
    assert "NPA" in sector_note("Banks - Regional", "Financial Services")
    assert "unbilled" in sector_note("Information Technology Services", "Technology")
    assert sector_note("Medical Care Facilities", "Healthcare") is None
    for _, note in __import__("agents.sector_notes", fromlist=["NOTES"]).NOTES:
        assert not any(ch.isdigit() for ch in note), "sector notes must not contain numbers"
    print("[ok] sector notes: matched by industry, contain no numbers")


def test_financials_prompt_has_coverage_and_norms():
    from tests.test_financials_agent import company
    c = company("ICRA.NS", "ICRA", 0.30)
    c["market"]["industry"] = "Financial Data & Stock Exchanges"
    c["ratios"]["coverage"] = {"basis": "consolidated", "fy_end_month": 3,
                               "annual_periods": ["FY26"], "quarters_on_file": ["Q2 FY27"],
                               "missing_quarters": ["Q2 FY26"], "not_in_source_yet": [],
                               "pending_note": None}
    _, prompt = build_prompt([c])
    assert "Sector norms" in prompt and "advance" in prompt
    assert "missing from source: Q2 FY26" in prompt
    assert "consolidated" in prompt
    print("[ok] Financials prompt: coverage fact, consolidated basis, sector norms")


ANN = b"""<rss version="2.0"><channel>
<item><title>Zensar Technologies Limited</title>
<link>https://nsearchives.nseindia.com/corporate/ZENSARTECH_15112026194718_SEintimationChangeinSMP.pdf</link>
<description>Zensar Technologies Limited has informed the Exchange about Cessation of Senior Management Personnel |SUBJECT: General Updates</description>
<pubDate>15-Nov-2026 19:47:59</pubDate></item>
<item><title>SBI Nifty 50 ETF</title><link>https://nsearchives.nseindia.com/corporate/SETFNIF50_15112026_x.pdf</link>
<description>NAV |SUBJECT: General Updates</description><pubDate>15-Nov-2026 10:00:00</pubDate></item>
<item><title>Zensar Technologies Limited</title><link>https://nsearchives.nseindia.com/corporate/ZENSARTECH_14112026101010_Newspaper.pdf</link>
<description>Zensar Technologies Limited has informed the Exchange about Copy of Newspaper Publication |SUBJECT: Copy of Newspaper Publication</description>
<pubDate>14-Nov-2026 10:10:10</pubDate></item>
</channel></rss>"""
ACT = b"""<rss version="2.0"><channel>
<item><title>Zensar Technologies Limited - Ex-Date: 13-Nov-2026</title>
<link>https://www.nseindia.com/companies-listing/corporate-filings-actions</link>
<description>SERIES:EQ |PURPOSE:INTERIM DIVIDEND - RS 2 PER SHARE |FACE VALUE:2</description>
<pubDate>13-Nov-2026 06:11:45</pubDate></item>
</channel></rss>"""


def test_filings_collector_and_reader():
    items = collector.parse_feed("announcement", ANN) + collector.parse_feed("corporate_action", ACT)
    assert len(items) == 3                                         # ETF skipped
    assert items[0]["symbol"] == "ZENSARTECH" and items[0]["subject"] == "General Updates"
    assert items[0]["published"] == "2026-11-15T19:47:59+05:30"
    assert items[2]["symbol"] is None and items[2]["company"] == "Zensar Technologies Limited"
    assert items[2]["subject"].startswith("INTERIM DIVIDEND")

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        assert collector.store(items, out)["added"] == 3
        assert collector.store(items, out)["added"] == 0           # second run: no duplicates
        (out / "nse" / "meta.json").write_text(json.dumps({"history_starts": "2026-11-13T00:00"}))
        assert (out / "nse" / "by_symbol" / "_name_ZENSAR_TECHNOLOGIES.jsonl").exists()

        old = config.FILINGS_DIR
        config.FILINGS_DIR = str(out / "nse")
        try:
            r = filings.get_filings("ZENSARTECH.NS", "Zensar Technologies", days=10_000)
            none = filings.get_filings("TCS", "Tata Consultancy Services", days=10_000)
            config.FILINGS_DIR = str(out / "missing")
            off = filings.get_filings("TCS")
        finally:
            config.FILINGS_DIR = old
    assert r["status"] == "ok" and r["total_found"] == 2 and r["routine_skipped"] == 1
    assert r["items"][0]["subject"] == "General Updates"           # newest first
    assert {i["kind"] for i in r["items"]} == {"announcement", "corporate_action"}
    assert none["status"] == "ok" and none["items"] == []
    assert off["status"].startswith("unavailable")
    print("[ok] NSE filings: parse, skip funds, dedupe, match by symbol or name, drop routine")


def main():
    test_quarter_labels()
    test_coverage_and_deadlines()
    test_results_season_ttl()
    test_news_merge()
    test_sector_notes()
    test_financials_prompt_has_coverage_and_norms()
    test_filings_collector_and_reader()
    print("\nAll data-source tests passed.")


if __name__ == "__main__":
    main()
