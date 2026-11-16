"""Offline tests for the Planner — fake Yahoo lookups, no network.

Run:  .venv/bin/python -m tests.test_planner
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import agents.planner as planner
from agents.planner import make_plan
from data import lookup, store

PROFILES = {
    "TCS.NS": ("Tata Consultancy Services", "Technology", "Information Technology Services", 9e12),
    "INFY.NS": ("Infosys", "Technology", "Information Technology Services", 4.7e12),
    "TINY.NS": ("Tiny Tech", "Technology", "Information Technology Services", 1e10),
    "HCL-INSYS.NS": ("HCL Infosystems", "Technology", "Electronics & Computer Distribution", 3e9),
    "HDFCBANK.NS": ("HDFC Bank", "Financial Services", "Banks - Regional", 1.2e13),
    "BAJFINANCE.NS": ("Bajaj Finance", "Financial Services", "Credit Services", 6.6e12),
    "BSE.NS": ("BSE", "Financial Services", "Financial Data & Stock Exchanges", 1e12),
    "TMCV.NS": ("Tata Motors", "Consumer Cyclical", "Auto Manufacturers", 1e12),
    "TMPV.NS": ("Tata Motors Passenger Vehicles", "Consumer Cyclical", "Auto Manufacturers", 1e12),
    "SUNPHARMA.NS": ("Sun Pharmaceutical Industries", "Healthcare", "Drug Manufacturers - Specialty & Generic", 4.5e12),
    "SPARC.NS": ("Sun Pharma Advanced Research Company", "Healthcare", "Biotechnology", 6.4e10),
    "CIPLA.NS": ("Cipla", "Healthcare", "Drug Manufacturers - Specialty & Generic", 1.2e12),
    "M&M.NS": ("Mahindra & Mahindra", "Consumer Cyclical", "Auto Manufacturers", 4e12),
    "M&MFIN.NS": ("Mahindra & Mahindra Financial Services", "Financial Services", "Credit Services", 4e11),
    "SBIN.NS": ("State Bank of India", "Financial Services", "Banks - Regional", 1e13),
    "SBILIFE.NS": ("SBI Life Insurance Company", "Financial Services", "Insurance - Life", 1.8e12),
}
# query -> [(ticker, exact, extra)]: extra = name words the user didn't type
SEARCH = {
    "infosys": [("INFY.NS", True, 0), ("HCL-INSYS.NS", False, 2)],
    "tata motors": [("TMCV.NS", True, 0), ("TMPV.NS", True, 2)],
    "tata consultancy": [("TCS.NS", True, 1)],
    "infosis": [("INFY.NS", False, 1)],     # typo: no exact match → "did you mean"
    "sun pharma": [("SUNPHARMA.NS", True, 1), ("SPARC.NS", True, 3)],
    "cipla": [("CIPLA.NS", True, 0)],
    "mahindra & mahindra": [("M&M.NS", True, 0), ("M&MFIN.NS", True, 2)],
    "tata": [("TCS.NS", True, 2), ("TMCV.NS", True, 1)],   # TCS is 9x bigger but a looser fit
    "sbi": [("SBILIFE.NS", True, 3)],
}


def fake_lookup(symbol):
    t = symbol.upper() if "." in symbol else symbol.upper() + ".NS"
    if t not in PROFILES:
        return None
    name, sector, industry, mcap = PROFILES[t]
    return {"ticker": t, "name": name, "sector": sector, "industry": industry,
            "exchange": "NSE", "market_cap": mcap}


def fake_search(query):
    return [{"ticker": t, "name": PROFILES[t][0], "sector": PROFILES[t][1],
             "industry": PROFILES[t][2], "exchange": "NSE", "exact": exact, "extra": extra}
            for t, exact, extra in SEARCH.get(query.lower().strip(), [])]


def _quote(symbol, longname, sector="Healthcare"):
    return {"symbol": symbol, "exchange": "NSI" if symbol.endswith(".NS") else "BSE",
            "quoteType": "EQUITY", "longname": longname, "sector": sector, "industry": "x"}


# Raw Yahoo search replies, shaped like the real ones for these names (Nov 2026)
YAHOO = {
    "Sun Pharma": [_quote("SUNPHARMA.NS", "Sun Pharmaceutical Industries Limited"),
                   _quote("SPARC.NS", "Sun Pharma Advanced Research Company Limited"),
                   {**_quote("6887.HK", "Sunshine Lake Pharma Co., Ltd."), "exchange": "HKG"},
                   _quote("SUNPHARMA.BO", "Sun Pharmaceutical Industries Limited")],
    "Nestle India": [_quote("NESTLEIND.NS", "Nestlé India Limited", "Consumer Defensive")],
    "Mahindra & Mahindra": [_quote("M&M.NS", "Mahindra & Mahindra Limited", "Consumer Cyclical"),
                            _quote("M&MFIN.NS", "Mahindra & Mahindra Financial Services Limited",
                                   "Financial Services")],
    "Dr Reddys": [],                                  # Yahoo only knows "Dr. Reddy's"
    "Dr Reddy": [_quote("DRREDDY.NS", "Dr. Reddy's Laboratories Limited")],
    "Larsen and Toubro": [],
    "Larsen & Toubro": [_quote("LT.NS", "Larsen & Toubro Limited", "Industrials")],
    "Infosys": [_quote("INFY.NS", "Infosys Limited", "Technology"),
                _quote("HCL-INSYS.NS", "HCL Infosystems Limited", "Technology")],
    "Infosis": [], "Infosi": [],
}


def lookup_tests():
    """search_company's name matching, on canned Yahoo replies (no network)."""
    store.ROOT = Path(tempfile.mkdtemp())
    asked = []
    lookup._search_quotes = lambda q, limit: asked.append(q) or YAHOO.get(q, [])

    def found(query):
        return {c["ticker"]: (c["exact"], c["extra"]) for c in lookup.search_company(query)}

    assert found("Sun Pharma") == {"SUNPHARMA.NS": (True, 1), "SPARC.NS": (True, 3)}
    print("[ok] 'pharma' matches 'Pharmaceutical' (start of a word); BSE twin and non-Indian dropped")

    assert found("Nestle India") == {"NESTLEIND.NS": (True, 0)}
    print("[ok] accents ignored: 'Nestle India' is an exact match for 'Nestlé India'")

    assert found("Mahindra & Mahindra") == {"M&M.NS": (True, 0), "M&MFIN.NS": (True, 2)}
    print("[ok] both Mahindras match; the closer fit has fewer extra words")

    asked.clear()
    assert found("Dr Reddys") == {"DRREDDY.NS": (True, 1)}
    assert asked == ["Dr Reddys", "Dr Reddy"], asked
    assert lookup._match(lookup._words("Dr. Reddy's"), "Dr. Reddy's Laboratories Limited") == (True, 1)
    print("[ok] 'Dr Reddys': retried as 'Dr Reddy', then matched with apostrophe/dot ignored")

    asked.clear()
    assert found("Larsen and Toubro") == {"LT.NS": (True, 0)} and asked[-1] == "Larsen & Toubro"
    print("[ok] 'and' retried as '&'")

    assert found("Infosys") == {"INFY.NS": (True, 0), "HCL-INSYS.NS": (True, 1)}
    assert found("Infosis") == {}
    assert lookup._match({"co"}, "Coal India Limited") == (False, 2)    # short words: whole only
    assert lookup._match(set(), "Coal India Limited")[0] is False
    print("[ok] typos and 1-2 letter fragments don't count as matches")

    assert lookup.alias_for(" sbi ") == "SBIN" and lookup.alias_for("l & t") == "LT"
    assert lookup.alias_for("HUL") == "HINDUNILVR" and lookup.alias_for("Infosys") is None
    print("[ok] short-name aliases: SBI→SBIN, HUL→HINDUNILVR, L&T→LT")


def main():
    lookup_tests()
    planner.lookup.lookup_symbol = fake_lookup
    planner.lookup.search_company = fake_search
    planner.lookup.LookupUnavailable = __import__('data.lookup', fromlist=['x']).LookupUnavailable
    planner.config.REDDIT_CLIENT_ID = planner.config.REDDIT_CLIENT_SECRET = ""

    p = make_plan(["tcs", "Infosys"])
    assert p.ok and p.match == "same_industry"
    assert [c.ticker for c in p.companies] == ["TCS.NS", "INFY.NS"]
    assert "social" not in p.agents and "API keys" in p.skipped["social"]
    assert abs(sum(p.weights.values()) - 1) < 0.01 and p.weights["financials"] == 0.444
    assert p.data_parts == ["financials", "news"] and p.needs_macro
    print("[ok] symbol + name resolve; social skipped without Reddit keys; weights re-balanced to 100%")

    planner.config.REDDIT_CLIENT_ID = planner.config.REDDIT_CLIENT_SECRET = "set"
    p = make_plan(["TCS", "INFY"])
    assert p.weights == {"financials": 0.4, "news": 0.2, "social": 0.1, "market": 0.15, "macro": 0.15}
    assert "social" in p.data_parts
    planner.config.REDDIT_CLIENT_ID = planner.config.REDDIT_CLIENT_SECRET = ""
    print("[ok] with Reddit keys: all 5 agents, default weights")

    for inputs in (["TCS"], ["TCS", "INFY", "TINY", "HDFCBANK"], ["TCS", "  "]):
        p = make_plan(inputs)
        assert not p.ok and p.blocking == ["Please enter 2 or 3 companies to compare."]
    print("[ok] fewer than 2 or more than 3 companies → blocked")

    p = make_plan(["Tata Motors", "TCS"])
    assert not p.ok and p.problems[0].kind == "ambiguous"
    assert [c.ticker for c in p.problems[0].candidates] == ["TMCV.NS", "TMPV.NS"]
    print(f"[ok] ambiguous name → user picks: \"{p.problems[0].message}\"")

    p = make_plan(["infosis", "TCS"])
    assert p.problems[0].kind == "ambiguous" and "isn't an exact match" in p.problems[0].message
    print("[ok] typo → 'did you mean' with candidates")

    p = make_plan(["zzzz", "TCS"])
    assert p.problems[0].kind == "not_found"
    print("[ok] unknown company → not found message")

    real_search = planner.lookup.search_company

    def down(query):
        raise planner.lookup.LookupUnavailable("timeout")
    planner.lookup.search_company = down
    p = make_plan(["Infosys", "TCS"])
    planner.lookup.search_company = real_search
    assert not p.ok and p.problems[0].kind == "unavailable" and "try again" in p.blocking[0]
    print(f"[ok] Yahoo search down → \"{p.blocking[0]}\" (not 'not found')")

    p = make_plan(["TCS", "Tata Consultancy"])
    assert not p.ok and p.problems[0].kind == "duplicate"
    print(f"[ok] same company twice → \"{p.problems[0].message}\"")

    p = make_plan(["TCS", "HDFCBANK"])
    assert not p.ok and p.match == "different_sector" and "different sectors" in p.blocking[0]
    p = make_plan(["TCS", "HDFCBANK"], allow_cross_sector=True)
    assert p.ok and any("different sectors" in w for w in p.warnings)
    assert any("financial company" in w for w in p.warnings)
    print("[ok] different sectors → blocked (allowed with override, plus a warning)")

    p = make_plan(["HDFCBANK", "BAJFINANCE"])
    assert p.ok and p.match == "same_sector" and "less like-for-like" in p.warnings[0]
    p = make_plan(["HDFCBANK", "BSE"])
    assert any("BSE" not in w.split(" is a")[0] and "HDFC Bank is a financial" in w for w in p.warnings)
    print("[ok] same sector, different industry → allowed with warning; lender vs non-lender warned")

    p = make_plan(["TCS", "TINY"])
    assert p.ok and any("900 times larger" in w for w in p.warnings)
    print("[ok] very different sizes → warning")

    # Yahoo's details call fails (rate limit on a shared server) but search knows the company
    planner.lookup.lookup_symbol = lambda s: None
    SEARCH["hdfc bank"] = [("HDFCBANK.NS", True, 0)]
    SEARCH["hdfcbank.ns"] = [("HDFCBANK.NS", False, 2)]        # typed symbol: not a name match
    SEARCH["bajaj finance"] = [("BAJFINANCE.NS", True, 0)]
    for typed in (["HDFC Bank", "Bajaj Finance"], ["HDFCBANK.NS", "Bajaj Finance"]):
        p = make_plan(typed)
        assert not p.problems, (typed, [x.message for x in p.problems])
        assert [c.ticker for c in p.companies] == ["HDFCBANK.NS", "BAJFINANCE.NS"]
    assert p.companies[0].industry == "Banks - Regional" and p.companies[0].market_cap is None
    planner.lookup.lookup_symbol = fake_lookup
    print("[ok] Yahoo details call failing → company taken from search instead of 'pick one'")

    p = make_plan(["Sun Pharma", "Cipla"])
    assert p.ok and [c.ticker for c in p.companies] == ["SUNPHARMA.NS", "CIPLA.NS"], p.blocking
    p = make_plan(["Mahindra & Mahindra", "Tata Motors"])
    assert [c.ticker for c in p.companies] == ["M&M.NS"]
    assert p.problems[0].input == "Tata Motors" and p.problems[0].kind == "ambiguous"
    print("[ok] name fits several: the far bigger, equally close one wins (Sun Pharma, M&M); "
          "similar sizes stay a choice (Tata Motors)")

    p = make_plan(["Tata", "Infosys"])
    assert p.problems[0].kind == "ambiguous" and "more than one" in p.problems[0].message
    print("[ok] 'Tata': TCS is biggest but Tata Motors fits closer → user picks")

    planner.lookup.lookup_symbol = lambda s: None if s.startswith("SPARC") else fake_lookup(s)
    p = make_plan(["Sun Pharma", "Cipla"])
    assert p.problems and p.problems[0].kind == "ambiguous"
    planner.lookup.lookup_symbol = fake_lookup
    print("[ok] sizes unknown (Yahoo details call failed) → user picks, no guessing")

    p = make_plan(["SBI", "HDFC Bank"])
    assert [c.ticker for c in p.companies] == ["SBIN.NS", "HDFCBANK.NS"], p.blocking
    planner.lookup.lookup_symbol = lambda s: None
    SEARCH["sbin"] = [("SBIN.NS", False, 4)]
    p = make_plan(["sbi", "HDFC Bank"])
    assert [c.ticker for c in p.companies] == ["SBIN.NS", "HDFCBANK.NS"], p.blocking
    planner.lookup.lookup_symbol = fake_lookup
    print("[ok] 'SBI' → State Bank of India (not SBI Life), even when the details call fails")

    print("\nAll Planner tests passed.")


if __name__ == "__main__":
    main()
