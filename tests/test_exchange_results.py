"""Offline tests for Phase 2.1/2.2: reading results XBRL files, saving them, and
using them in the analysis.

Run:  .venv/bin/python -m tests.test_exchange_results
Uses small hand-made XBRL files with the same structure as NSE's. If real files
are in xbrl_samples/ (git-ignored), they are checked too.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import config
from agents.financials import build_prompt
from agents.judge import what_changed
from data import exchange_results as er
from data.ratios import add_exchange_filings, add_exchange_results


def xbrl(symbol="HDFCBANK", basis="Standalone", start="2026-07-01", end="2026-09-30",
         legacy=False, bank=True, pat=19_510, npa_pct="0.0111", extra="", broken=False):
    """A results file in crores → rupees, shaped like NSE's banking XBRL."""
    cr = lambda x: f"{x * 1e7:.0f}"
    ie, oi, iexp, opex, prov = 83_427, 13_831, 48_807, 18_967, 3_574
    total, op = ie + oi, ie + oi - iexp - opex
    pbt = op - prov
    tax = pbt - pat + (2_000 if broken else 0)      # so the lines add up unless `broken`
    ytd_start = start[:4] + "-04-01"
    # the legacy format keeps quarter and year-to-date in contexts with the SAME dates
    ytd_period = (f"<xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>"
                  if legacy else
                  f"<xbrli:startDate>{ytd_start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>")
    lines = (f"<x:InterestEarned contextRef='Q' unitRef='INR' decimals='-5'>{cr(ie)}</x:InterestEarned>"
             f"<x:OtherIncome contextRef='Q' unitRef='INR'>{cr(oi)}</x:OtherIncome>"
             f"<x:Income contextRef='Q' unitRef='INR'>{cr(total)}</x:Income>"
             f"<x:InterestExpended contextRef='Q' unitRef='INR'>{cr(iexp)}</x:InterestExpended>"
             f"<x:OperatingExpenses contextRef='Q' unitRef='INR'>{cr(opex)}</x:OperatingExpenses>"
             f"<x:OperatingProfitBeforeProvisionAndContingencies contextRef='Q' unitRef='INR'>{cr(op)}</x:OperatingProfitBeforeProvisionAndContingencies>"
             f"<x:ProvisionsOtherThanTaxAndContingencies contextRef='Q' unitRef='INR'>{cr(prov)}</x:ProvisionsOtherThanTaxAndContingencies>"
             f"<x:ProfitLossFromOrdinaryActivitiesBeforeTax contextRef='Q' unitRef='INR'>{cr(pbt)}</x:ProfitLossFromOrdinaryActivitiesBeforeTax>"
             f"<x:TaxExpense contextRef='Q' unitRef='INR'>{cr(tax)}</x:TaxExpense>"
             f"<x:ProfitLossFromOrdinaryActivitiesAfterTax contextRef='Q' unitRef='INR'>{cr(pat)}</x:ProfitLossFromOrdinaryActivitiesAfterTax>"
             f"<x:ProfitLossForThePeriod contextRef='Q' unitRef='INR'>{cr(pat)}</x:ProfitLossForThePeriod>"
             f"<x:PercentageOfGrossNpa contextRef='Q' unitRef='pure'>{npa_pct if bank else 0}</x:PercentageOfGrossNpa>"
             f"<x:PercentageOfNpa contextRef='Q' unitRef='pure'>{'0.0037' if bank else 0}</x:PercentageOfNpa>"
             f"<x:CET1Ratio contextRef='Q' unitRef='pure'>{'0.1887' if bank else 0}</x:CET1Ratio>"
             f"<x:ReturnOnAssets contextRef='Q' unitRef='pure'>{'0.0045' if bank else 0}</x:ReturnOnAssets>"
             f"<x:Deposits contextRef='I' unitRef='INR'>{cr(3_327_449)}</x:Deposits>"
             # year-to-date figures that must NOT be picked
             f"<x:InterestEarned contextRef='Y' unitRef='INR'>{cr(ie * 2)}</x:InterestEarned>")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:x="http://example/in-capmkt">
<!-- {'legacy' if legacy else 'IntegratedFinance_Banking'} -->
<xbrli:context id="Q"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier></xbrli:entity>
 <xbrli:period><xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="Y"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier></xbrli:entity>
 <xbrli:period>{ytd_period}</xbrli:period></xbrli:context>
<xbrli:context id="I"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier></xbrli:entity>
 <xbrli:period><xbrli:instant>{end}</xbrli:instant></xbrli:period></xbrli:context>
<x:Symbol contextRef="Q">{symbol}</x:Symbol>
<x:NameOfTheCompany contextRef="Q">HDFC Bank Limited</x:NameOfTheCompany>
<x:NatureOfReportStandaloneConsolidated contextRef="Q">{basis}</x:NatureOfReportStandaloneConsolidated>
<x:DateOfStartOfReportingPeriod contextRef="Q">{start}</x:DateOfStartOfReportingPeriod>
<x:DateOfEndOfReportingPeriod contextRef="Q">{end}</x:DateOfEndOfReportingPeriod>
<x:DateOfStartOfReportingPeriod contextRef="Y">{ytd_start}</x:DateOfStartOfReportingPeriod>
<x:DateOfEndOfReportingPeriod contextRef="Y">{end}</x:DateOfEndOfReportingPeriod>
<x:DateOfEndOfFinancialYear contextRef="Q">{int(start[:4]) + 1}-03-31</x:DateOfEndOfFinancialYear>
<x:WhetherResultsAreAuditedOrUnaudited contextRef="Q">Unaudited</x:WhetherResultsAreAuditedOrUnaudited>
{lines}{extra}
</xbrli:xbrl>""".encode()


def test_parse():
    r = er.parse_xbrl(xbrl(), "a.xml")
    assert r["symbol"] == "HDFCBANK" and r["basis"] == "standalone" and r["template"] == "bank"
    assert r["period_end"] == "2026-09-30" and r["checks"]["passed"], r["checks"]
    assert round(r["figures"]["interest_earned"] / 1e7) == 83_427            # quarter, not YTD
    assert round(r["figures"]["net_interest_income"] / 1e7) == 34_620
    assert r["bank"]["gross_npa_pct"] == 0.0111 and round(r["bank"]["deposits"] / 1e7) == 3_327_449

    legacy = er.parse_xbrl(xbrl(legacy=True, basis="Consolidated", bank=False), "b.xml")
    assert round(legacy["figures"]["interest_earned"] / 1e7) == 83_427       # same dates, still the quarter
    assert legacy["basis"] == "consolidated" and legacy["bank"] is None      # ratios filed as 0

    bad = er.parse_xbrl(xbrl(broken=True), "c.xml")
    assert not bad["checks"]["passed"] and "profit after tax" in bad["checks"]["failed"][0]

    for raw, msg in [(b"<html></html>", "isn't an XBRL"), (b"not xml", "isn't valid XML"),
                     (b'<!DOCTYPE x [<!ENTITY a "b">]><x/>', "isn't a results"),
                     (xbrl(start="2026-04-01"), "No quarterly results")]:
        try:
            er.parse_xbrl(raw)
            raise AssertionError(f"accepted: {msg}")
        except er.XBRLError as ex:
            assert msg in str(ex), ex
    print("[ok] XBRL: quarter picked over year-to-date (both formats), checks, bad files refused")


def test_save_and_summarise():
    with tempfile.TemporaryDirectory() as tmp:
        old = config.DATA_DIR
        config.DATA_DIR = tmp
        try:
            er.add_filing(er.parse_xbrl(xbrl(), "q2fy27_sa.xml"))
            er.add_filing(er.parse_xbrl(xbrl(basis="Consolidated", bank=False, pat=20_700,
                                             extra=""), "q2fy27_cons.xml"))
            er.add_filing(er.parse_xbrl(xbrl(start="2025-07-01", end="2025-09-30", npa_pct="0.0124",
                                             pat=18_641), "q2fy26_sa.xml"))
            again = er.add_filing(er.parse_xbrl(xbrl(), "q2fy27_sa_again.xml"))
            assert again["replaced"] and again["count"] == 3
            got = er.get_exchange_results("HDFCBANK.NS")
        finally:
            config.DATA_DIR = old
    assert got["status"] == "ok" and len(got["filings"]) == 3
    s = er.summarise(got["filings"], 3, "consolidated")
    assert s["quarters"] == {"Q2 FY27": ["consolidated", "standalone"], "Q2 FY26": ["standalone"]}
    assert s["latest"]["basis"] == "consolidated" and s["latest"]["year_ago_quarter"] is None
    sa = er.summarise(got["filings"], 3, "standalone")
    assert sa["latest"]["year_ago_quarter"] == "Q2 FY26"
    assert round(sa["latest"]["yoy"]["net_profit"], 3) == round(19_510 / 18_641 - 1, 3)
    assert s["bank"]["year_ago"]["gross_npa_pct"] == 0.0124
    print("[ok] saved to the data store, newer upload replaces older, year-on-year from filings")
    return got


def test_analysis_uses_filings(got):
    from tests.test_financials_agent import company
    c = company("HDFCBANK.NS", "HDFC Bank", 0.25)
    r = c["ratios"]
    r["is_financial"] = True
    r["coverage"] = {"basis": "consolidated", "fy_end_month": 3, "annual_periods": ["FY26"],
                     "quarters_on_file": ["Q1 FY26"], "newest_quarter_end": "2025-06-30",
                     "missing_quarters": [], "not_in_source_yet": ["Q2 FY26", "Q3 FY26", "Q4 FY26",
                                                                     "Q1 FY27", "Q2 FY27"],
                     "pending_note": None}
    r["data_warnings"] = ["Our data source (Yahoo Finance) does not have Q2 FY26, … yet", "Other"]
    add_exchange_results(r, got, basis="standalone")
    cov = r["coverage"]
    assert cov["not_in_source_yet"] == ["Q3 FY26", "Q4 FY26", "Q1 FY27"], cov
    assert r["exchange"]["latest_is_newer"]
    assert r["data_warnings"][0].startswith("Our data source (Yahoo Finance) does not have Q3 FY26")
    assert "Other" in r["data_warnings"]

    # a filed-results notice for a quarter we already have from filings isn't repeated
    add_exchange_filings(r, {"results_filed": [{"quarter_end": "2026-09-30", "filed": "2026-10-17",
                                                "link": "x"}]})
    assert cov["filed_not_in_source"] == []

    _, prompt = build_prompt([c])
    assert "from the company's results filing" in prompt and "Q2 FY27 (standalone" in prompt
    assert "gross NPA 1.11% (Q2 FY26: 1.24%)" in prompt and "CET1 capital ratio 18.87%" in prompt
    assert "from the company's own results filings: Q2 FY27" in prompt
    lines = what_changed(c)
    assert lines[0].startswith("Q2 FY27 (company's results filing, standalone): net profit +4.7%"), lines
    assert any(l.startswith("Gross NPA 1.11% vs 1.24%") for l in lines), lines
    print("[ok] analysis: filed quarters fill gaps, bank facts reach the AI, what-changed uses them")


def test_real_samples():
    folder = Path(__file__).resolve().parents[1] / "xbrl_samples"
    files = sorted(folder.glob("*.xml")) if folder.exists() else []
    if not files:
        print("[skip] no real files in xbrl_samples/")
        return
    for f in files:
        r = er.parse_xbrl(f.read_bytes(), f.name)
        assert r["checks"]["passed"], (f.name, r["checks"])
    print(f"[ok] {len(files)} real NSE file(s) in xbrl_samples/ read and add up")


def main():
    test_parse()
    got = test_save_and_summarise()
    test_analysis_uses_filings(got)
    test_real_samples()
    print("\nAll exchange-results tests passed.")


if __name__ == "__main__":
    main()
