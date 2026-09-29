"""
The fictional demo workspace: it is loaded from sample_data/ into memory and never saved.

It starts with the 2026 budget and January-July actuals already imported, so the
August close can be imported on screen (see SAMPLE_IMPORTS) to show the monthly routine.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from .importer import read_monthly_workbook
from .store import DataStore, load_properties

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"
PRELOADED = ["budget_2026.xlsx", "actuals_2026-01_to_2026-07.xlsx"]
SAMPLE_IMPORTS = {
    "monthly_2026-08.xlsx": "August 2026 close (clean file)",
    "monthly_2026-08_with_errors.xlsx": "August 2026 with deliberate errors",
}
TEMPLATE_FILE = SAMPLE_DIR / "TEMPLATE_monthly_hotel_report.xlsx"
# Fictional demo files for the separate sources. Not preloaded: the overview first shows each
# source's honest "data needed" state, and these can be imported on Data Imports & Checks.
SOURCE_DEMOS = {
    "channel_mix": "DEMO_channel_mix_2026.xlsx",
    "pace": "DEMO_pace_2026-09-01.xlsx",
    "str": "DEMO_str_2026.xlsx",
    "debt": "DEMO_debt_service.xlsx",
}


def build_demo_store(today: date | None = None) -> DataStore:
    properties, _ = load_properties(SAMPLE_DIR / "properties.csv")
    store = DataStore(properties, folder=None)
    for file_name in PRELOADED:
        store.add_import(read_monthly_workbook(SAMPLE_DIR / file_name, file_name, properties, today))
    return store
