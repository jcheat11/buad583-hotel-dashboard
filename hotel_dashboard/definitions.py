"""
Metric definitions and data rules.

This is the ONE place to change how figures are calculated or checked. Every other
file reads its rules from here, and the app's "Definitions" page displays them, so
the written definitions and the running code can't drift apart.

Plain-English summary
---------------------
    Occupancy = rooms sold / rooms available
    ADR       = rooms revenue / rooms sold
    RevPAR    = rooms revenue / rooms available      (the same as Occupancy x ADR)

Rates are NEVER averaged. For a group of hotels we first add up the underlying
rooms and revenue, then divide. Averaging three hotels' occupancy percentages
would give a small hotel the same weight as a large one.
"""

# --- Fields in the monthly Excel template ------------------------------------------
# Each entry: field name used in code -> column heading shown in the Excel template.
# The importer accepts either form (case and spacing don't matter).

ID_FIELDS = {
    "property_id": "Property ID",
    "month": "Month",
}

ROOM_FIELDS = {
    "rooms_available": "Rooms Available",
    "rooms_sold": "Rooms Sold",
    "rooms_revenue": "Rooms Revenue",
}

# Summary P&L lines. These mirror the summary P&L columns of a typical monthly
# management package, so an existing report can be copied into the template.
FINANCIAL_FIELDS = {
    "other_revenue": "Other Revenue",
    "total_revenue": "Total Revenue",
    "labor_expense": "Labor & Benefits",
    "rooms_expense": "Rooms Expense",
    "ga_expense": "G&A Expense",
    "maintenance_expense": "Maintenance Expense",
    "sales_marketing_expense": "Sales & Marketing",
    "utilities_expense": "Utilities",
    "mgmt_fees_royalties": "Mgmt Fees & Royalties",
    "total_operating_expenses": "Total Operating Expenses",
    "gop": "GOP",
    "other_expenses": "Other Expenses (below GOP)",
    "ebitda": "EBITDA",
    "capital_reserve": "Capital Reserve",
    "noi": "NOI",
}

# Optional detail of "Other Expenses (below GOP)", one column per group in the source P&L
# (the property P&L sheets list these groups between Gross Operating Profit and Total Other Expense).
# Files without these columns still import cleanly; the EBITDA waterfall then shows one combined step.
OTHER_EXPENSE_DETAIL_FIELDS = {
    "insurance_expense": "Insurance",
    "real_estate_tax": "Real Estate Tax",
    "personal_property_tax": "Personal Property Tax",
    "other_taxes": "Other Taxes & Assessments",
    "misc_other_expense": "Miscellaneous Other Expense",
}

# Optional: the rates exactly as printed in the source report. They are NOT used for
# the dashboard. They are only compared with our own calculation to catch errors.
REPORTED_FIELDS = {
    "reported_occupancy": "Reported Occupancy",
    "reported_adr": "Reported ADR",
    "reported_revpar": "Reported RevPAR",
}

ALL_FIELDS = {**ID_FIELDS, **ROOM_FIELDS, **FINANCIAL_FIELDS, **OTHER_EXPENSE_DETAIL_FIELDS, **REPORTED_FIELDS}

# Other headings the importer also accepts: the subtotal labels used in the source P&L sheets.
# (Management Fees and Royalties are separate lines in the source; the template adds them together.)
HEADER_ALIASES = {
    "Occupied Rooms": "rooms_sold",
    "Period": "month",
    "Total Rooms Revenue": "rooms_revenue",
    "Labor and Benefits": "labor_expense",
    "Room Expense": "rooms_expense",
    "General & Administrative": "ga_expense",
    "General and Administrative": "ga_expense",
    "Maintenance": "maintenance_expense",
    "Sales and Marketing": "sales_marketing_expense",
    "Total Expense": "total_operating_expenses",
    "Gross Operating Profit": "gop",
    "Total Other Expense": "other_expenses",
    "Tax Other": "other_taxes",
    "Other Taxes and Assessments": "other_taxes",
    "Miscellaneous Expense": "misc_other_expense",
}

# Short explanations, shown in the template's ReadMe sheet and on the Definitions page.
FIELD_NOTES = {
    "property_id": "Code from properties.csv, e.g. H01. One row per hotel per month. No subtotal rows.",
    "month": "The month the figures cover: an Excel date or text such as 2026-08.",
    "rooms_available": "Room nights available to sell: keys x days in the month.",
    "rooms_sold": "Occupied room nights sold to guests.",
    "rooms_revenue": "The room revenue used for ADR and RevPAR (see pending question 1).",
    "other_revenue": "All non-room revenue.",
    "total_revenue": "Rooms revenue + other revenue.",
    "total_operating_expenses": "Sum of the seven operating expense lines above it.",
    "gop": "Gross operating profit = total revenue - total operating expenses.",
    "other_expenses": "Costs below GOP, such as property tax and insurance.",
    "ebitda": "GOP - other expenses.",
    "capital_reserve": "FF&E / capital reserve set aside.",
    "noi": "EBITDA - capital reserve (= GOP - other expenses - capital reserve). The dashboard recalculates it.",
    "insurance_expense": "Optional. Source P&L group 'Insurance' (property, flood, liability, D&O ...).",
    "real_estate_tax": "Optional. Source P&L group 'Real Estate Tax'.",
    "personal_property_tax": "Optional. Source P&L group 'Personal Property Tax'.",
    "other_taxes": "Optional. Source P&L group 'Tax Other' (special assessments, franchise, business tax ...).",
    "misc_other_expense": "Optional. Source P&L group 'Miscellaneous Expense' (pre-opening, settlements ...).",
    "reported_occupancy": "Optional. Occupancy as printed in the source report (0.76 or 76). Used only as a check.",
    "reported_adr": "Optional. ADR as printed in the source report. Used only as a check.",
    "reported_revpar": "Optional. RevPAR as printed in the source report. Used only as a check.",
}

# Definitions still to be confirmed by the asset management team. Shown on the Definitions page.
PENDING_CONFIRMATION = [
    "ADR and RevPAR numerator: should 'Rooms Revenue' include no-show revenue and other rooms revenue, "
    "or only revenue from occupied room nights (the usual STR convention)?",
    "Occupancy: is it rooms sold / (keys x days), or are out-of-order rooms removed from rooms available? "
    "Do complimentary and house-use rooms count as sold?",
    "Review status: the 'Review' flag still uses GOP (more than 10% below budget). Should it use NOI, "
    "now that NOI is the headline profit card?",
    "Review thresholds: are 'RevPAR more than 5% below budget', 'GOP more than 10% below budget' and "
    "'expense line more than 10% and $2,500 over budget' the right triggers?",
    "Stabilization stage: what moves a hotel from ramp-up to stabilized, and should past months use the "
    "stage the hotel had at the time? (v0 applies today's stage to every month.)",
    "Budget version: which budget or reforecast should variances use, and should its version be shown?",
    "EBITDA waterfall: it follows the source P&L: Total Revenue, the seven operating expense groups "
    "(Management Fees and Royalties combined), GOP, the five below-GOP groups, EBITDA. Should Management "
    "Fees and Royalties be separate steps, and is 'EBITDA = GOP - Total Other Expense' the approved definition?",
    "Revenue Mix channels: the monthly report's Channel Mix block uses Property, OTA, Call Center, Website "
    "and GDS; the dashboard asks for SellAway Revenue, Direct Booking, OTA and Other. What is SellAway "
    "revenue, and can it overlap with Direct Booking or OTA? Which report channels count as Direct Booking "
    "(Property, Call Center, Website?) and is GDS 'Other'? Is the Channel Mix total rooms revenue or total revenue?",
    "DSCR: which cash-flow figure is the numerator (NOI, EBITDA less reserve, or the lender's definition), "
    "and what minimum DSCR applies to each loan? The dashboard uses only figures supplied in the debt file.",
    "Labor performance: is labor cost vs budget in dollars the right test, or should it be labor cost per "
    "occupied room (which adjusts for occupancy)?",
    "Exception map bands: RevPAR or GOP within 1% of budget, and labor up to 2% over budget, count as "
    "'On track'. Are those the right tolerances?",
]

# Numbers that can be added up across hotels (rates cannot).
ADDITIVE_FIELDS = list(ROOM_FIELDS) + list(FINANCIAL_FIELDS) + list(OTHER_EXPENSE_DETAIL_FIELDS)
NUMERIC_FIELDS = ADDITIVE_FIELDS + list(REPORTED_FIELDS)

# A row without these values cannot be used, so the whole file is rejected.
REQUIRED_FIELDS = [
    "property_id", "month",
    "rooms_available", "rooms_sold", "rooms_revenue",
    "total_revenue", "total_operating_expenses", "gop",
]

# Sheets the importer reads. "Actual" = closed monthly results; "Budget" = the plan.
SCENARIO_SHEETS = ["Actual", "Budget"]

# --- Arithmetic that must hold inside every row ----------------------------------
# (total, parts added, parts subtracted). Checked only when all the columns exist.
EXPENSE_LINES = [
    "labor_expense", "rooms_expense", "ga_expense", "maintenance_expense",
    "sales_marketing_expense", "utilities_expense", "mgmt_fees_royalties",
]
SUBTOTAL_RULES = [
    ("total_revenue", ["rooms_revenue", "other_revenue"], []),
    ("total_operating_expenses", EXPENSE_LINES, []),
    ("gop", ["total_revenue"], ["total_operating_expenses"]),
    ("other_expenses", list(OTHER_EXPENSE_DETAIL_FIELDS), []),
    ("ebitda", ["gop"], ["other_expenses"]),
    ("noi", ["ebitda"], ["capital_reserve"]),
]

# Lines where a HIGHER actual than budget is unfavorable.
COST_FIELDS = (set(EXPENSE_LINES) | set(OTHER_EXPENSE_DETAIL_FIELDS)
               | {"total_operating_expenses", "other_expenses", "capital_reserve"})

# --- EBITDA waterfall -----------------------------------------------------------------
# Total Revenue, then each expense group as a downward step, then EBITDA, in source P&L order:
#   Total Revenue - operating expense groups = GOP (subtotal) - below-GOP groups = EBITDA
# The ending bar is the EBITDA SUPPLIED in the file. If the steps don't arrive there (or at the
# supplied GOP), the gap is shown as its own labelled "difference" step; no bar is adjusted.
WATERFALL_OPERATING_LINES = EXPENSE_LINES                       # else one "Total Operating Expenses" step
WATERFALL_BELOW_GOP_LINES = list(OTHER_EXPENSE_DETAIL_FIELDS)   # else one "Other Expenses (below GOP)" step

# --- Tolerances for "does not reconcile" warnings -------------------------------
OCCUPANCY_TOLERANCE_PP = 0.1   # reported vs calculated occupancy, in percentage points
RATE_TOLERANCE = 0.01          # dollars, for reported ADR and RevPAR (reports round to cents)
TOTAL_TOLERANCE = 1.00         # dollars, for P&L subtotals (allows rounding)

# --- When a hotel is marked "Review" on the dashboard ----------------------------
REVIEW_REVPAR_VS_BUDGET = -0.05   # RevPAR more than 5% below budget
REVIEW_GOP_VS_BUDGET = -0.10      # GOP more than 10% below budget

# Expense lines mentioned under "Needs attention" in the briefing.
ATTENTION_EXPENSE_OVER_BUDGET = 0.10   # more than 10% over budget ...
ATTENTION_EXPENSE_MIN_DOLLARS = 2_500  # ... and at least $2,500 over

# Which profit line the fourth dashboard card shows (and the briefing leads with). Confirmed: NOI.
# The dashboard calculates NOI from its parts (see metrics.net_operating_income):
#     NOI = GOP - other expenses (below GOP) - capital reserve
HEADLINE_PROFIT_FIELD = "noi"
PROFIT_CARD_LABELS = {
    "gop": "Gross Operating Profit (GOP)",
    "ebitda": "EBITDA",
    "noi": "Net Operating Income (NOI)",
}

# Exception map "Watch" bands: small misses count as on track, so real exceptions stand out.
WATCH_BELOW_BUDGET = -0.01        # RevPAR or GOP more than 1% below budget -> Watch
WATCH_LABOR_OVER_BUDGET = 0.02    # labor more than 2% over budget -> Watch

# Operating profit used by the profit bridge: GOP = total revenue - total operating expenses.
# (Pending confirmation that GOP is the approved "operating profit" for the bridge.)
BRIDGE_PROFIT_FIELD = "gop"

# --- Revenue Mix (separate channel import, see sources.py) ---------------------------------
# NOT CONFIRMED: whether SellAway revenue overlaps with Direct Booking or OTA. Until this is set to
# True, the four channels are shown side by side WITHOUT shares of a total (overlaps would double count).
CHANNELS_MUTUALLY_EXCLUSIVE = None
# Once confirmed exclusive, the channel total is checked against this monthly line (pending question).
CHANNEL_TOTAL_FIELD = "rooms_revenue"

# --- DSCR (separate debt import, see sources.py) ---------------------------------------------
# DSCR = cash flow available for debt service / debt service. Both figures, and the minimum DSCR,
# come from the debt file; no numerator (such as EBITDA or NOI) and no universal threshold is assumed.
DSCR_PREFERRED_PERIOD_MONTHS = 12     # trailing 12 months when all 12 are supplied; else the month alone

# --- Property list (properties.csv) ------------------------------------------------
# Fixed columns. EVERY OTHER column is treated as a grouping ("classification"),
# e.g. brand, asset_cohort, stabilization_stage. Each hotel has exactly one value per
# classification, so it can sit in several groups (one per classification) without
# being counted twice inside any single classification.
PROPERTY_FIXED_COLUMNS = ["property_id", "property_name", "keys"]


def classification_columns(columns) -> list[str]:
    """The grouping columns of properties.csv (everything except the fixed columns)."""
    return [c for c in columns if c not in PROPERTY_FIXED_COLUMNS]


def label(field: str) -> str:
    """Human-readable name for a field, e.g. 'rooms_sold' -> 'Rooms Sold'."""
    if field in ALL_FIELDS:
        return ALL_FIELDS[field]
    return field.replace("_", " ").title()
