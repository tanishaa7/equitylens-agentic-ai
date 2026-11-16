"""Quarterly results from the companies' own XBRL filings (NSE/BSE "Financial
Results" and, from 2025, "Integrated Filing – Financials"), uploaded in the app
and kept on the repo's `data` branch (results/<SYMBOL>.json).

    parse_xbrl(raw_bytes, filename) -> one filing: quarter, standalone/consolidated,
                                       results lines, bank ratios, arithmetic checks
    add_filing(parsed)              -> save it next to the company's other filings
    get_exchange_results("HDFCBANK") -> {"status": "ok", "filings": [...newest first]}

Amounts are in rupees, as filed. Each file says itself which company, quarter and
basis it is, so the person uploading doesn't have to.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import date

import pandas as pd

from data.common import now_iso, quarter_label
from data import repo_store

XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"
MAX_BYTES = 15 * 1024 * 1024


class XBRLError(ValueError):
    """The file can't be used; the message is safe to show to the user."""


# Results lines, by template. Banks file in the RBI format, other companies in Ind AS.
BANK_LINES = {
    "interest_earned": "InterestEarned",
    "other_income": "OtherIncome",
    "total_income": "Income",
    "interest_expended": "InterestExpended",
    "employee_cost": "EmployeesCost",
    "operating_expenses": "OperatingExpenses",
    "operating_profit": "OperatingProfitBeforeProvisionAndContingencies",
    "provisions": "ProvisionsOtherThanTaxAndContingencies",
    "exceptional_items": "ExceptionalItems",
    "profit_before_tax": "ProfitLossFromOrdinaryActivitiesBeforeTax",
    "tax": "TaxExpense",
    "profit_after_tax": "ProfitLossFromOrdinaryActivitiesAfterTax",
    "net_profit": "ProfitLossForThePeriod",
    "net_profit_owners": "ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates",
    "eps_basic": "BasicEarningsPerShareAfterExtraordinaryItems",
}
BANK_RATIOS = {
    "gross_npa_pct": "PercentageOfGrossNpa",
    "net_npa_pct": "PercentageOfNpa",
    "cet1_ratio": "CET1Ratio",
    "at1_ratio": "AdditionalTier1Ratio",
    "roa_quarter": "ReturnOnAssets",          # for the quarter, not annualised
    "gross_npa": "GrossNonPerformingAssets",
    "net_npa": "NonPerformingAssets",
}
BANK_BALANCES = {"deposits": "Deposits", "advances": "Advances"}

COMPANY_LINES = {
    "revenue": "RevenueFromOperations",
    "other_income": "OtherIncome",
    "total_income": "Income",
    "finance_costs": "FinanceCosts",
    "depreciation": "DepreciationDepletionAndAmortisationExpense",
    "employee_cost": "EmployeeBenefitExpense",
    "total_expenses": "Expenses",
    "exceptional_items": "ExceptionalItemsBeforeTax",
    "profit_before_tax": "ProfitBeforeTax",
    "tax": "TaxExpense",
    "net_profit": "ProfitLossForPeriod",
    "net_profit_owners": "ProfitOrLossAttributableToOwnersOfParent",
    "eps_basic": "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
}


def _num(text: str | None) -> float | None:
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


def _load(raw: bytes):
    if len(raw) > MAX_BYTES:
        raise XBRLError("That file is too large to be a results XBRL file.")
    head = raw[:4096]
    if b"<!DOCTYPE" in head or b"<!ENTITY" in raw:
        raise XBRLError("That file isn't a results XBRL file.")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise XBRLError("That file isn't valid XML. Download the XBRL (.xml) file, not the PDF.")
    if root.tag != XBRLI + "xbrl":
        raise XBRLError("That file isn't an XBRL file.")

    contexts = {}
    for c in root.iter(XBRLI + "context"):
        p = c.find(XBRLI + "period")
        inst, start, end = (p.find(XBRLI + t) for t in ("instant", "startDate", "endDate"))
        dims = [m for m in c.iter() if m.tag in (XBRLDI + "explicitMember", XBRLDI + "typedMember")]
        contexts[c.get("id")] = {
            "start": start.text.strip() if start is not None else None,
            "end": (end if end is not None else inst).text.strip(),
            "instant": inst is not None,
            "dims": bool(dims),
        }
    facts: dict[str, dict[str, str]] = {}
    for el in root:
        ref = el.get("contextRef")
        if ref in contexts:
            facts.setdefault(ref, {})[el.tag.split("}")[-1]] = (el.text or "").strip()
    return contexts, facts


def _quarter_context(contexts, facts):
    """The context holding the current quarter's figures: no dimensions, about three
    months long, latest end date. Older files keep the quarter and year-to-date in
    contexts with the same dates, so the declared reporting period wins."""
    best = None
    for cid, c in contexts.items():
        if c["dims"] or c["instant"]:
            continue
        f = facts.get(cid, {})
        start = f.get("DateOfStartOfReportingPeriod") or c["start"]
        end = f.get("DateOfEndOfReportingPeriod") or c["end"]
        try:
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days
        except (TypeError, ValueError):
            continue
        if not 80 <= days <= 100 or not ({"InterestEarned", "RevenueFromOperations"} & f.keys()):
            continue
        if best is None or end > best[2]:
            best = (cid, start, end)
    return best


def _instant(contexts, facts, end: str, name: str) -> float | None:
    for cid, c in contexts.items():
        if c["instant"] and not c["dims"] and c["end"] == end and name in facts.get(cid, {}):
            return _num(facts[cid][name])
    return None


def _close(a, b, rel=0.002) -> bool:
    return abs(a - b) <= max(1e5, abs(b) * rel)     # ₹1 lakh or 0.2%


def checks(template: str, f: dict) -> list[str]:
    """Do the filed lines add up? Returns the ones that don't."""
    g = lambda k: f.get(k)
    bad = []

    def need(*keys):
        return all(g(k) is not None for k in keys)

    if template == "bank":
        if need("interest_earned", "other_income", "total_income") and not _close(
                g("interest_earned") + g("other_income"), g("total_income")):
            bad.append("interest earned + other income ≠ total income")
        if need("total_income", "interest_expended", "operating_expenses", "operating_profit") and not _close(
                g("total_income") - g("interest_expended") - g("operating_expenses"), g("operating_profit")):
            bad.append("income − interest − operating expenses ≠ operating profit")
        if need("operating_profit", "provisions", "profit_before_tax") and not _close(
                g("operating_profit") - g("provisions") + (g("exceptional_items") or 0), g("profit_before_tax")):
            bad.append("operating profit − provisions ≠ profit before tax")
        if need("profit_before_tax", "tax", "profit_after_tax") and not _close(
                g("profit_before_tax") - g("tax"), g("profit_after_tax")):
            bad.append("profit before tax − tax ≠ profit after tax")
    else:
        if need("revenue", "other_income", "total_income") and not _close(
                g("revenue") + g("other_income"), g("total_income")):
            bad.append("revenue + other income ≠ total income")
        if need("total_income", "total_expenses", "profit_before_tax") and not _close(
                g("total_income") - g("total_expenses") + (g("exceptional_items") or 0), g("profit_before_tax")):
            bad.append("income − expenses ≠ profit before tax")
        if need("profit_before_tax", "tax", "net_profit") and not _close(
                g("profit_before_tax") - g("tax"), g("net_profit"), rel=0.02):
            bad.append("profit before tax − tax ≠ net profit")
    return bad


def parse_xbrl(raw: bytes, filename: str = "") -> dict:
    contexts, facts = _load(raw)
    found = _quarter_context(contexts, facts)
    if not found:
        raise XBRLError("No quarterly results found in this file. Choose a quarterly "
                        "results filing (not annual, half-year or a board-meeting notice).")
    cid, start, end = found
    f = facts[cid]
    meta = {}
    for v in facts.values():                     # company details sit in any context
        for k in ("Symbol", "NameOfTheCompany", "NatureOfReportStandaloneConsolidated",
                  "WhetherResultsAreAuditedOrUnaudited", "DateOfEndOfFinancialYear",
                  "DateOfBoardMeetingWhenFinancialResultsWereApproved"):
            meta.setdefault(k, v.get(k))
    symbol = (f.get("Symbol") or meta.get("Symbol") or "").upper().strip()
    if not symbol:
        raise XBRLError("This file has no NSE symbol in it, so we can't tell which company it is.")
    basis = (f.get("NatureOfReportStandaloneConsolidated")
             or meta.get("NatureOfReportStandaloneConsolidated") or "").lower()
    basis = "consolidated" if basis.startswith("consol") else "standalone"

    template = "bank" if "InterestEarned" in f else "company"
    lines = BANK_LINES if template == "bank" else COMPANY_LINES
    figures = {k: _num(f.get(name)) for k, name in lines.items()}
    if template == "bank" and None not in (figures["interest_earned"], figures["interest_expended"]):
        figures["net_interest_income"] = figures["interest_earned"] - figures["interest_expended"]
    if template == "company" and None not in (figures["revenue"], figures["total_expenses"]):
        figures["operating_profit"] = (figures["revenue"] - figures["total_expenses"]
                                       + (figures["finance_costs"] or 0) + (figures["depreciation"] or 0))
    if all(v is None for v in figures.values()):
        raise XBRLError("This file has no results figures we can read.")

    bank = None
    if template == "bank":
        bank = {k: _num(f.get(name)) for k, name in BANK_RATIOS.items()}
        pct = [bank[k] for k in ("gross_npa_pct", "net_npa_pct", "cet1_ratio", "roa_quarter")]
        if all(not v for v in pct):              # consolidated statements file these as 0
            bank = None
        else:
            bank.update({k: _instant(contexts, facts, end, name) for k, name in BANK_BALANCES.items()})

    failed = checks(template, figures)
    return {
        "symbol": symbol,
        "company": meta.get("NameOfTheCompany") or f.get("NameOfTheCompany"),
        "template": template,
        "basis": basis,
        "period_type": "quarter",
        "period_start": start,
        "period_end": end,
        "fy_end": meta.get("DateOfEndOfFinancialYear"),
        "audited": (meta.get("WhetherResultsAreAuditedOrUnaudited") or "").lower() == "audited",
        "figures": figures,
        "bank": bank,
        "checks": {"passed": not failed, "failed": failed},
        "source": {
            "kind": "xbrl",
            "file": re.sub(r"[^\w.\-]", "_", filename)[:120],
            "format": "integrated filing" if b"IntegratedFinance" in raw[:6000] else "financial results",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "board_meeting": meta.get("DateOfBoardMeetingWhenFinancialResultsWereApproved"),
            "added_at": now_iso(),
        },
    }


def _path(symbol: str) -> str:
    return f"results/{re.sub(r'[^A-Z0-9&-]', '_', symbol.upper())}.json"


def add_filing(parsed: dict) -> dict:
    """Save one parsed filing. A newer upload of the same quarter and basis replaces
    the older one. Returns {"replaced": bool, "count": n}."""
    path = _path(parsed["symbol"])
    doc = repo_store.read_json(path) or {"symbol": parsed["symbol"], "filings": []}
    key = (parsed["period_end"], parsed["basis"], parsed["period_type"])
    kept = [x for x in doc["filings"] if (x["period_end"], x["basis"], x["period_type"]) != key]
    replaced = len(kept) != len(doc["filings"])
    doc["filings"] = sorted(kept + [parsed], key=lambda x: (x["period_end"], x["basis"]), reverse=True)
    doc["company"] = parsed.get("company") or doc.get("company")
    repo_store.write_json(path, doc, f"Results: {parsed['symbol']} {parsed['period_end']} "
                                     f"{parsed['basis']} (uploaded)")
    return {"replaced": replaced, "count": len(doc["filings"])}


def get_exchange_results(symbol: str) -> dict:
    symbol = symbol.upper().split(".")[0]
    try:
        doc = repo_store.read_json(_path(symbol))
    except Exception as ex:      # storage unreachable shouldn't stop a comparison
        return {"symbol": symbol, "status": f"unavailable: {ex}"[:120], "filings": []}
    filings = (doc or {}).get("filings", [])
    return {"symbol": symbol, "status": "ok", "filings": filings,
            "source": {"provider": "Company XBRL results filings (NSE), uploaded",
                       "url": f"https://www.nseindia.com/get-quotes/equity?symbol={symbol}",
                       "fetched_at": now_iso()}}


# ---------------------------------------------------------------- for the analysis

def _yoy(a, b):
    return None if a is None or not b else a / abs(b) - 1


def summarise(filings: list[dict], fy_end_month: int = 3, basis: str = "consolidated") -> dict | None:
    """What the uploaded filings add: the quarters on file (any basis), and the newest
    quarter on the chosen basis with its year-on-year change when the year-ago quarter
    is also on file. Bank ratios always come from the standalone statement (that's
    where banks report them). Only filings whose figures add up are used."""
    good = [f for f in filings if f.get("checks", {}).get("passed")]
    if not good:
        return None
    label = lambda f: quarter_label(pd.Timestamp(f["period_end"]), fy_end_month)
    by = {(f["period_end"], f["basis"]): f for f in good}
    quarters = {}
    for f in sorted(good, key=lambda f: f["period_end"], reverse=True):
        quarters.setdefault(label(f), set()).add(f["basis"])

    def pick(b):
        rows = sorted((f for f in good if f["basis"] == b), key=lambda f: f["period_end"], reverse=True)
        return rows[0] if rows else None

    def year_ago(f):
        prev = (pd.Timestamp(f["period_end"]) - pd.DateOffset(years=1)) + pd.offsets.MonthEnd(0)
        return by.get((prev.date().isoformat(), f["basis"]))

    cur = pick(basis) or pick("standalone" if basis == "consolidated" else "consolidated")
    latest = None
    if cur:
        prev = year_ago(cur)
        keys = ["total_income", "net_profit", "net_profit_owners", "net_interest_income",
                "revenue", "operating_profit", "provisions"]
        latest = {
            "quarter": label(cur), "period_end": cur["period_end"], "basis": cur["basis"],
            "template": cur["template"], "audited": cur.get("audited"),
            "figures": {k: cur["figures"].get(k) for k in keys if cur["figures"].get(k) is not None},
            "year_ago_quarter": label(prev) if prev else None,
            "yoy": ({k: _yoy(cur["figures"].get(k), prev["figures"].get(k)) for k in keys
                     if cur["figures"].get(k) is not None and prev["figures"].get(k) is not None}
                    if prev else {}),
            "source_file": cur["source"].get("file"),
        }

    bank = None
    sa = sorted((f for f in good if f.get("bank")), key=lambda f: f["period_end"], reverse=True)
    if sa:
        b, prev = sa[0], year_ago(sa[0])
        bank = {"quarter": label(b), "period_end": b["period_end"], **b["bank"],
                "year_ago_quarter": label(prev) if prev and prev.get("bank") else None,
                "year_ago": prev["bank"] if prev and prev.get("bank") else None}
    return {"quarters": {q: sorted(v) for q, v in quarters.items()}, "latest": latest, "bank": bank}
