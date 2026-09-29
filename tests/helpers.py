"""
Tiny fictional hotels used by the tests. The numbers are chosen so the expected answers
can be checked by hand (June has 30 days):

    Hotel A: 10 keys -> 300 rooms available, 270 sold, $27,000 rooms revenue  (90% occ, $100 ADR)
    Hotel B: 30 keys -> 900 rooms available, 450 sold, $90,000 rooms revenue  (50% occ, $200 ADR)

    A + B together: 720 / 1,200 = 60.0% occupancy (a simple average would wrongly say 70%)
                    $117,000 / 720 = $162.50 ADR, $117,000 / 1,200 = $97.50 RevPAR
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from hotel_dashboard import definitions as d

PROPERTIES = pd.DataFrame([
    {"property_id": "A", "property_name": "Alpha Hotel", "keys": 10, "brand": "Blue", "stage": "Stabilized"},
    {"property_id": "B", "property_name": "Beta Hotel", "keys": 30, "brand": "Blue", "stage": "Ramp-up"},
    {"property_id": "C", "property_name": "Gamma Hotel", "keys": 20, "brand": "Green", "stage": "Stabilized"},
])


def hotel_month(pid, month, available, sold, rooms_revenue, **other) -> dict:
    """One hotel-month with consistent totals (no other revenue, simple costs)."""
    labor = other.pop("labor_expense", rooms_revenue * 0.30)
    row = {
        "property_id": pid, "month": month,
        "rooms_available": available, "rooms_sold": sold, "rooms_revenue": rooms_revenue,
        "other_revenue": 0.0, "total_revenue": rooms_revenue,
        "labor_expense": labor, "rooms_expense": 0.0, "ga_expense": 0.0, "maintenance_expense": 0.0,
        "sales_marketing_expense": 0.0, "utilities_expense": 0.0, "mgmt_fees_royalties": 0.0,
        "total_operating_expenses": labor, "gop": rooms_revenue - labor,
    }
    row.update(other)
    return row


def facts_table(actual: list[dict], budget: list[dict] = ()) -> pd.DataFrame:
    rows = [{"scenario": "Actual", **r} for r in actual] + [{"scenario": "Budget", **r} for r in budget]
    return pd.DataFrame(rows).reindex(columns=["scenario", "property_id", "month", *d.NUMERIC_FIELDS])


ACTUAL_JUNE = [hotel_month("A", "2026-06", 300, 270, 27_000.0), hotel_month("B", "2026-06", 900, 450, 90_000.0)]
BUDGET_JUNE = [hotel_month("A", "2026-06", 300, 255, 25_500.0), hotel_month("B", "2026-06", 900, 495, 94_050.0)]


def write_workbook(path: Path, sheets: dict[str, list[dict]]) -> Path:
    """Writes rows to an .xlsx file using the template's human-readable column headings."""
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, rows in sheets.items():
            df = pd.DataFrame(rows)
            df.columns = [d.ALL_FIELDS.get(c, c) for c in df.columns]
            df.to_excel(writer, sheet_name=name, index=False)
    return path
