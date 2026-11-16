"""What is normal for a sector — background for the AI so it doesn't flag a
sector's ordinary features (e.g. a rating agency's receivables, a bank's
"debt") as company-specific problems.

These are general norms, not facts about any company. They deliberately
contain no numbers: every number the AI writes must come from the data.

    note = sector_note(industry="Financial Data & Stock Exchanges", sector="Financial Services")
"""

from __future__ import annotations

# (keywords matched against Yahoo's industry, then sector — lower case), note
NOTES: list[tuple[tuple[str, ...], str]] = [
    (("financial data", "stock exchange"),
     "Rating agencies, exchanges and data providers are asset-light, usually debt-free and "
     "cash-rich, with high margins. Rating agencies bill many fees (e.g. annual surveillance) in "
     "advance, so year-end receivables are partly matched by advance fees received, which this "
     "data does not show. Judge receivables by their trend and against peers, not on their own. "
     "Revenue follows the bond-issuance and bank-credit cycle. Figures are consolidated: group "
     "businesses such as analytics or consulting units can carry more receivables than the "
     "core ratings business."),
    (("banks",),
     "For banks, deposits and borrowings are raw material, not debt; EBITDA, ROCE and cash-flow "
     "ratios do not apply. What matters most is net interest margin, asset quality (gross and "
     "net NPA), capital adequacy (CRAR) and deposit franchise (CASA) — none of these are in this "
     "data, so say so rather than guessing. Large mergers can distort growth for a few years."),
    (("credit services", "mortgage", "financial conglomerates"),
     "For NBFCs and lenders, borrowing is the business model, so high debt/equity is normal; "
     "judge leverage against peers. Asset quality, cost of funds and capital adequacy matter "
     "most and are not in this data."),
    (("insurance",),
     "Insurers' accounts are driven by premiums, claims and investment income; margins and "
     "cash-flow ratios look unusual and are not comparable with other industries. Key metrics "
     "(solvency ratio, combined ratio, VNB margin) are not in this data."),
    (("asset management", "capital markets"),
     "Asset managers and brokers are asset-light and earn fees linked to market levels and "
     "trading volumes, so profits swing with the market cycle. Brokers may show large "
     "receivables and payables from client trades — this is normal for the business."),
    (("information technology services", "software"),
     "IT services firms are asset-light with high cash conversion. Receivables (including "
     "unbilled revenue) are naturally sizeable because clients pay on credit terms; rising "
     "receivable days versus peers can signal slower collections. Revenue is mostly in US "
     "dollars, so the rupee exchange rate affects reported growth and margins."),
    (("household", "personal products", "packaged foods", "beverages", "confectioners",
      "tobacco", "food distribution"),
     "Consumer staples companies often run on negative working capital (distributors pay "
     "upfront), so low receivables and very high ROCE are normal. Volume growth and pricing "
     "matter; raw-material costs drive margins."),
    (("luxury goods", "jewelry", "apparel", "footwear"),
     "Jewellery and fashion retailers carry heavy inventory (gold, stock in stores); inventory "
     "and working capital are normal here. Gold prices and store expansion drive revenue."),
    (("auto manufacturers", "auto parts", "farm & heavy construction"),
     "Auto and auto-component makers are cyclical and capital-intensive; margins move with "
     "commodity prices and volumes. Component makers often have higher receivables because "
     "carmakers pay on credit terms."),
    (("drug manufacturers", "pharmaceutical", "biotechnology"),
     "Pharma companies spend heavily on R&D; US FDA inspections and US generic pricing can "
     "swing results. Export receivables and inventory are naturally sizeable."),
    (("building materials", "cement", "steel", "aluminum", "chemicals", "metal"),
     "Cement, metals and chemicals are capital-intensive and cyclical; debt rises during "
     "capacity expansion. Margins follow commodity and energy prices."),
    (("oil & gas", "utilities", "telecom"),
     "Oil, power and telecom businesses are capital-intensive, so high debt and depreciation "
     "are normal; regulation and commodity prices drive profits. Judge leverage against peers."),
    (("real estate",),
     "Real-estate developers recognise revenue on project completion, so yearly results are "
     "lumpy; inventory, advances from customers and debt are large by nature."),
    (("specialty retail", "department stores", "grocery", "internet retail"),
     "Retailers have thin margins and high inventory turnover; store expansion drives growth "
     "and lease liabilities can show up as debt."),
]


def sector_note(industry: str | None, sector: str | None = None) -> str | None:
    """General norms for this industry (or sector), or None if we have none."""
    for text in ((industry or "").lower(), (sector or "").lower()):
        if not text:
            continue
        for keys, note in NOTES:
            if any(k in text for k in keys):
                return note
    return None
