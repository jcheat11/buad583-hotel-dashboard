"""
The current-view calculation and evidence trace shown on the Review Log page.

Everything here READS results from metrics.py (the single calculation path) and records from the
data store. Nothing is recalculated: the 'formula' lines put the inputs metrics.py used next to the
result metrics.py produced. A missing input stays missing (None), never zero.

This is a trace of what the app shows for the current selection, not a permanent audit trail:
the app keeps which import supplied each hotel-month, but not the original files or row numbers.
"""
from __future__ import annotations

import pandas as pd

from . import definitions as d
from . import metrics
from .formatting import count, money, month_label, pct

# How each key result is produced. "inputs" are the totals metrics.combine() added up across the
# selected hotels; "result" is the field metrics.py calculated (or added up) from them.
TRACED = [
    {"key": "occupancy", "name": "Occupancy", "formula": "Rooms sold ÷ rooms available",
     "inputs": ["rooms_sold", "rooms_available"], "kind": "pct"},
    {"key": "adr", "name": "ADR", "formula": "Rooms revenue ÷ rooms sold",
     "inputs": ["rooms_revenue", "rooms_sold"], "kind": "rate"},
    {"key": "revpar", "name": "RevPAR", "formula": "Rooms revenue ÷ rooms available",
     "inputs": ["rooms_revenue", "rooms_available"], "kind": "rate"},
    {"key": "total_revenue", "name": "Total revenue", "formula": "Sum of hotels' reported totals",
     "inputs": ["total_revenue"], "kind": "money"},
    {"key": "total_operating_expenses", "name": "Total operating expenses",
     "formula": "Sum of hotels' reported totals", "inputs": ["total_operating_expenses"],
     "kind": "money"},
    {"key": "gop", "name": "GOP", "formula": "Sum of hotels' reported GOP",
     "inputs": ["gop"], "kind": "money"},
    {"key": "ebitda", "name": "EBITDA", "formula": "Sum of hotels' reported EBITDA",
     "inputs": ["ebitda"], "kind": "money"},
    {"key": "noi", "name": "NOI", "formula": "GOP − other expenses − reserve",
     "inputs": ["gop", "other_expenses", "capital_reserve"], "kind": "money"},
]


def fmt(value, kind: str) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "missing"
    return {"pct": lambda v: pct(v, 1), "rate": lambda v: money(v, 2), "money": lambda v: money(v, 0),
            "count": count}[kind](value)


def _input_kind(field: str) -> str:
    return "count" if field in ("rooms_sold", "rooms_available") else "money"


def _expression(spec: dict, totals: dict | None) -> str:
    """'16,922 ÷ 21,886 = 77.3%' from the totals metrics.py used and the result it produced."""
    if not totals:
        return "missing"
    values = [fmt(totals.get(f), _input_kind(f)) for f in spec["inputs"]]
    result = fmt(totals.get(spec["key"]), spec["kind"])
    if spec["key"] in ("occupancy", "adr", "revpar"):
        return f"{values[0]} ÷ {values[1]} = {result}"
    if spec["key"] == "noi":
        if None in (totals.get("gop"), totals.get("other_expenses"), totals.get("capital_reserve")):
            return f"{result} (the files' NOI line: a part of the formula is missing)"
        return f"{values[0]} − {values[1]} − {values[2]} = {result}"
    if totals.get(spec["key"]) is None:
        return "missing (not supplied by every hotel, so no total is shown)"
    hotels = len(totals.get("property_ids", []))
    return f"{result} (sum of {hotels} hotel{'s' if hotels != 1 else ''})"


def calculation_trace(facts: pd.DataFrame, properties: pd.DataFrame, ids, month: str) -> dict:
    """The key results for the selection, with inputs, results and anything missing."""
    summary = metrics.month_summary(facts, ids, month)
    a, b = summary["actual"], summary["budget"]
    names = properties.set_index("property_id")["property_name"]
    if b is None:
        if not a:
            budget_note = "No actuals, so no budget comparison."
        elif summary["budget_missing"] and set(summary["budget_missing"]) != set(a["property_ids"]):
            budget_note = ("No budget for " + ", ".join(names.get(p, p) for p in summary["budget_missing"])
                           + ", so budget comparisons are hidden (a partial budget would mislead).")
        else:
            budget_note = "No budget imported for this month."
    else:
        budget_note = (f"Budget added up for the same {len(b['property_ids'])} hotel(s) that reported actuals "
                       "(like for like).")
    rows = []
    for spec in TRACED:
        missing = [d.label(f) for f in spec["inputs"] if not a or a.get(f) is None]
        v = metrics.variance(a, b, spec["key"])
        if v is None:
            variance = "missing (no budget comparison)" if b is None else "missing (input not supplied)"
        elif spec["kind"] == "pct":
            variance = f"{v['difference'] * 100:+.1f} pts"
        else:
            variance = (f"{'+' if v['difference'] >= 0 else '−'}{fmt(abs(v['difference']), spec['kind'])}"
                        + (f" ({v['percent'] * 100:+.1f}%)" if v["percent"] is not None else ""))
        rows.append({
            "key": spec["key"], "Result": spec["name"], "Formula": spec["formula"],
            "Actual (inputs → result)": _expression(spec, a),
            "Budget (inputs → result)": _expression(spec, b) if b else "missing",
            "Actual − budget": variance,
            "Missing inputs": ", ".join(missing) + " (not supplied by every hotel; shown as missing, not zero)"
            if missing else "",
        })
    return {"summary": summary, "rows": rows, "budget_note": budget_note,
            "bridge": metrics.revpar_bridge(a, b), "waterfall": metrics.ebitda_waterfall(a) if a else None}


INPUT_COLUMNS = ["rooms_available", "rooms_sold", "rooms_revenue", "total_revenue", "total_operating_expenses",
                 "gop", "other_expenses", "ebitda", "capital_reserve"]


def inputs_by_hotel(facts: pd.DataFrame, properties: pd.DataFrame, ids, month: str) -> pd.DataFrame:
    """The imported figures each selected hotel contributed (blank = not supplied, never zero)."""
    names = properties.set_index("property_id")["property_name"]
    rows = facts[(facts["month"] == month) & facts["property_id"].isin(set(ids))]
    table = rows[["scenario", "property_id", *INPUT_COLUMNS, "import_id"]].copy()
    table.insert(1, "property", table["property_id"].map(lambda p: names.get(p, p)))
    return table.drop(columns="property_id").sort_values(["scenario", "property"]).reset_index(drop=True)


def provenance(store, ids, month: str) -> dict:
    """Which import supplied each hotel's figures, coverage, warnings and reconciliation for the selection."""
    names = store.properties.set_index("property_id")["property_name"]
    ids = sorted(set(ids))
    rows = store.facts[(store.facts["month"] == month) & store.facts["property_id"].isin(ids)]
    log = store.imports.set_index("import_id") if not store.imports.empty else pd.DataFrame()

    files = []
    for (scenario, import_id), group in rows.groupby(["scenario", "import_id"]):
        entry = log.loc[import_id] if import_id in log.index else None
        files.append({
            "Scenario": scenario,
            "File": entry["file_name"] if entry is not None else "unknown (import record missing)",
            "Imported at": entry["imported_at"] if entry is not None else "",
            "Import #": int(import_id),
            "Months in the file": entry["months"] if entry is not None else "",
            "Hotels supplied for this month": ", ".join(names.get(p, p) for p in sorted(group["property_id"])),
            "File fingerprint": str(entry["file_hash"])[:12] if entry is not None else "",
        })

    coverage = []
    for pid in ids:
        mine = rows[rows["property_id"] == pid]
        entry = {"Property": names.get(pid, pid)}
        for scenario in d.SCENARIO_SHEETS:
            hit = mine[mine["scenario"] == scenario]
            if hit.empty:
                entry[scenario] = f"No {scenario.lower()}"
            else:
                import_id = hit["import_id"].iloc[0]
                file_name = log.loc[import_id, "file_name"] if import_id in log.index else "unknown file"
                entry[scenario] = f"{file_name} (import #{int(import_id)})"
        coverage.append(entry)

    warnings = store.active_warnings()
    if not warnings.empty:
        used = set(rows["import_id"])
        row_level = warnings["property_id"].fillna("") != ""
        scoped = warnings[(row_level & warnings["property_id"].isin(ids) & (warnings["month"] == month))
                          | (~row_level & warnings["import_id"].isin(used))]
    else:
        scoped = warnings
    return {"files": pd.DataFrame(files), "coverage": pd.DataFrame(coverage), "warnings": scoped,
            "months_label": month_label(month)}


UNTRACEABLE = [
    "**Excel row numbers** are kept only for figures that raised a warning; clean rows are traced to the "
    "file and import, not to a row.",
    "**The original files** are not kept by the app: it stores their name, import time and a fingerprint "
    "(hash) of their contents, so a file can be matched later but not re-opened here.",
    "**Figures that came from an earlier import replaced on re-import** are no longer stored; the trace shows "
    "the import that currently supplies each hotel-month.",
    "**Calculation history** is not recorded: this page recalculates the trace for the current selection each "
    "time it is opened. It is not a permanent audit trail.",
]


def source_imports_in_view(store, ids, month: str) -> pd.DataFrame:
    """The separate sources (channel mix, pace, STR, debt) that have data for this selection, with their files."""
    log = store.source_imports.set_index("import_id") if not store.source_imports.empty else pd.DataFrame()
    period_field = {"channel_mix": "month", "str": "period", "debt": "month", "pace": None}
    out = []
    for name, table in store.sources.items():
        if table.empty:
            continue
        mine = table[table["property_id"].isin(set(ids))]
        field = period_field.get(name)
        if field:
            mine = mine[mine[field] == month]
        elif not mine.empty:                                     # pace: the latest report
            mine = mine[mine["as_of_date"] == mine["as_of_date"].max()]
        for import_id, group in mine.groupby("import_id"):
            entry = log.loc[import_id] if import_id in log.index else None
            out.append({"Source": name.replace("_", " ").title(),
                        "File": entry["file_name"] if entry is not None else "unknown",
                        "Imported at": entry["imported_at"] if entry is not None else "",
                        "Rows used in this view": len(group)})
    return pd.DataFrame(out)
