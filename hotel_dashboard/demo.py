"""
The fictional demo workspace: it is loaded from sample_data/ into memory and never saved.

It starts with the 2026 budget and January-July actuals already imported, so the
August close can be imported on screen (see SAMPLE_IMPORTS) to show the monthly routine.
"""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from .importer import read_monthly_workbook
from .store import DataStore, load_properties

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"
PRELOADED = ["budget_2026.xlsx", "actuals_2026-01_to_2026-07.xlsx"]
SAMPLE_IMPORTS = {
    "monthly_2026-08.xlsx": "August 2026 close (clean file)",
    "monthly_2026-08_with_errors.xlsx": "August 2026 with deliberate errors",
}
# Fictional budget workbooks for the Budgets section: two separate per-group files for 2027, and the
# 2026 portfolio budget again (already loaded, so it demonstrates the replacement confirmation).
BUDGET_SAMPLES = {
    "budget_2027_legacy_assets.xlsx": "2027 budget: Legacy Assets",
    "budget_2027_other_properties.xlsx": "2027 budget: other properties",
    "budget_2026.xlsx": "2026 portfolio budget (already loaded)",
}
COMBINED_SAMPLES = {"combined_2026-08_actual_and_budget.xlsx": "August 2026 actuals + budget (one workbook)"}
TEMPLATE_FILE = SAMPLE_DIR / "TEMPLATE_monthly_hotel_report.xlsx"


def monthly_template_bytes(sheets: list[str] | None = None) -> bytes:
    """The monthly template, optionally with only some of its sheets (e.g. ["Actual"]) plus the ReadMe."""
    workbook = load_workbook(TEMPLATE_FILE)
    for name in list(workbook.sheetnames):
        if sheets is not None and name in ("Actual", "Budget") and name not in sheets:
            workbook.remove(workbook[name])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
# Fictional demo files for the separate sources. Not preloaded: the overview first shows each
# source's honest "data needed" state, and these can be imported on Data Imports & Checks.
SOURCE_DEMOS = {
    "channel_mix": "DEMO_channel_mix_2026.xlsx",
    "pace": "DEMO_pace_2026-09-01.xlsx",
    "str": "DEMO_str_2026.xlsx",
    "debt": "DEMO_debt_service.xlsx",
}


# The sample tiles on Data Imports & Checks: one tile per import, each with its fictional file(s) as
# (file, label, upload route). The first file is the default; the rest are offered in the review dialog.
DEMO_SAMPLES = {
    "actuals": [("monthly_2026-08.xlsx", "August 2026 close (clean file)", "actuals"),
                ("monthly_2026-08_with_errors.xlsx", "August 2026 with deliberate errors", "actuals"),
                ("combined_2026-08_actual_and_budget.xlsx", "August 2026 actuals + budget in one workbook",
                 "combined")],
    "budget": [(file_name, label, "budget") for file_name, label in BUDGET_SAMPLES.items()],
    "pace": [(SOURCE_DEMOS["pace"], "Pace reports as of 1 Aug and 1 Sep 2026", "pace")],
    "debt": [(SOURCE_DEMOS["debt"], "Debt service and cash flow, Aug 2025 – Aug 2026", "debt")],
    "str": [(SOURCE_DEMOS["str"], "STR results Jan – Aug 2026, forward Sep – Nov", "str")],
    "channel_mix": [(SOURCE_DEMOS["channel_mix"], "Room revenue by channel, Jan – Aug 2026", "channel_mix")],
}


def build_demo_store(today: date | None = None) -> DataStore:
    properties, _ = load_properties(SAMPLE_DIR / "properties.csv")
    store = DataStore(properties, folder=None)
    for file_name in PRELOADED:
        store.add_import(read_monthly_workbook(SAMPLE_DIR / file_name, file_name, properties, today))
    return store
