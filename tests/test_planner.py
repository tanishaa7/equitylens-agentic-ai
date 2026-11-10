"""Offline tests for the Planner — fake Yahoo lookups, no network.

Run:  .venv/bin/python -m tests.test_planner
"""

from __future__ import annotations

import agents.planner as planner
from agents.planner import make_plan

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
}
SEARCH = {
    "infosys": [("INFY.NS", True), ("HCL-INSYS.NS", False)],
    "tata motors": [("TMCV.NS", True), ("TMPV.NS", True)],
    "tata consultancy": [("TCS.NS", True)],
    "infosis": [("INFY.NS", False)],     # typo: no exact match → "did you mean"
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
             "industry": PROFILES[t][2], "exchange": "NSE", "exact": exact}
            for t, exact in SEARCH.get(query.lower().strip(), [])]


def main():
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

    print("\nAll Planner tests passed.")


if __name__ == "__main__":
    main()
