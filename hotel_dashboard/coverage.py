"""
What data is loaded and what is missing, for the Data Imports & Checks page.

Reads the data store only (imported figures, import logs, warnings); it never changes anything and
does no dashboard calculation. "Loaded" always means checked and imported, never just selected.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from . import metrics
from .formatting import month_label

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def years_available(store, today: date | None = None) -> list[int]:
    """Years with any imported actual or budget, plus the current year; newest first."""
    today = today or date.today()
    years = {int(m[:4]) for m in store.facts["month"].dropna()} | {today.year}
    return sorted(years, reverse=True)


def default_year(store, today: date | None = None) -> int:
    months = metrics.months_with_actuals(store.facts)
    return int(months[-1][:4]) if months else (today or date.today()).year


def _latest_import(store, import_ids) -> tuple[str, str]:
    log = store.imports[store.imports["import_id"].isin(set(import_ids))]
    if log.empty:
        return "", ""
    latest = log.sort_values("imported_at").iloc[-1]
    return str(latest["file_name"]), str(latest["imported_at"])


def actuals_status(store, year: int, today: date | None = None) -> pd.DataFrame:
    """One row per month of `year`: actuals imported for how many hotels, who is missing, source, warnings."""
    names = store.properties.set_index("property_id")["property_name"]
    total = len(store.properties)
    closed = metrics.latest_closed_month(today or date.today())
    warnings = store.active_warnings()
    actual = store.facts[store.facts["scenario"] == "Actual"]
    rows = []
    for number in range(1, 13):
        month = f"{year:04d}-{number:02d}"
        here = actual[actual["month"] == month]
        reported = sorted(here["property_id"])
        missing = [names.get(p, p) for p in store.properties["property_id"] if p not in set(reported)]
        file_name, imported_at = _latest_import(store, here["import_id"])
        open_warnings = 0
        if not warnings.empty:
            open_warnings = int(((warnings["month"] == month) & (warnings["sheet"] == "Actual")).sum())
        if month > closed:
            status = "Not closed yet" if not reported else "Imported early"
        elif not reported:
            status = "Not imported"
        elif len(reported) < total:
            status = "Partial"
        else:
            status = "Complete"
        rows.append({"month": month, "Month": month_label(month), "Status": status,
                     "Properties reported": f"{len(reported)} of {total}",
                     "Missing": ", ".join(missing) if reported or month <= closed else "",
                     "Latest file": file_name, "Imported": imported_at, "Open warnings": open_warnings})
    return pd.DataFrame(rows)


def month_details(store, month: str) -> dict:
    """Per-hotel detail for one month of actuals: which file supplied each hotel, and open warnings."""
    names = store.properties.set_index("property_id")["property_name"]
    log = store.imports.set_index("import_id") if not store.imports.empty else pd.DataFrame()
    here = store.facts[(store.facts["scenario"] == "Actual") & (store.facts["month"] == month)]
    hotels = []
    for pid in store.properties["property_id"]:
        row = here[here["property_id"] == pid]
        if row.empty:
            hotels.append({"Property": names.get(pid, pid), "Actuals": "Missing", "File": "", "Imported": ""})
        else:
            import_id = row["import_id"].iloc[0]
            entry = log.loc[import_id] if import_id in log.index else None
            hotels.append({"Property": names.get(pid, pid), "Actuals": "Imported",
                           "File": entry["file_name"] if entry is not None else "unknown",
                           "Imported": entry["imported_at"] if entry is not None else ""})
    warnings = store.active_warnings()
    if not warnings.empty:
        warnings = warnings[(warnings["month"] == month) & (warnings["sheet"] == "Actual")]
    return {"hotels": pd.DataFrame(hotels), "warnings": warnings}


def budget_coverage(store, year: int, property_ids=None) -> pd.DataFrame:
    """One row per hotel: which months of `year` have a budget, what's missing, and the latest source file."""
    names = store.properties.set_index("property_id")["property_name"]
    ids = list(property_ids) if property_ids is not None else list(store.properties["property_id"])
    budget = store.facts[(store.facts["scenario"] == "Budget") & store.facts["month"].str.startswith(f"{year:04d}-")]
    rows = []
    for pid in ids:
        mine = budget[budget["property_id"] == pid]
        have = {int(m[5:7]) for m in mine["month"]}
        file_name, imported_at = _latest_import(store, mine["import_id"])
        row = {"Property": names.get(pid, pid), **{MONTH_ABBR[n - 1]: "✓" if n in have else "–" for n in range(1, 13)},
               "Months": f"{len(have)} of 12",
               "Missing months": ", ".join(MONTH_ABBR[n - 1] for n in range(1, 13) if n not in have),
               "Latest file": file_name, "Imported": imported_at}
        rows.append(row)
    return pd.DataFrame(rows)


def replacement_preview(store, rows: pd.DataFrame) -> pd.DataFrame:
    """Exactly which stored hotel-months a new file would replace, with their current source and figures."""
    duplicates = store.duplicates_of(rows)
    if duplicates.empty:
        return pd.DataFrame()
    key = ["scenario", "property_id", "month"]
    names = store.properties.set_index("property_id")["property_name"]
    log = store.imports.set_index("import_id")
    current = duplicates[key].merge(store.facts, on=key)
    incoming = rows.set_index(key)
    out = []
    for _, old in current.iterrows():
        new = incoming.loc[(old["scenario"], old["property_id"], old["month"])]
        entry = log.loc[old["import_id"]] if old["import_id"] in log.index else None
        out.append({
            "Scenario": old["scenario"], "Property": names.get(old["property_id"], old["property_id"]),
            "Month": month_label(old["month"], short=True),
            "Currently from": f"{entry['file_name']} (import #{int(old['import_id'])})" if entry is not None else "unknown",
            "Imported": entry["imported_at"] if entry is not None else "",
            "Total revenue now": old["total_revenue"], "Total revenue in new file": new["total_revenue"],
            "GOP now": old["gop"], "GOP in new file": new["gop"],
        })
    return pd.DataFrame(out).sort_values(["Scenario", "Property", "Month"]).reset_index(drop=True)


PERIOD_FIELD = {"channel_mix": "month", "pace": "as_of_date", "str": "period", "debt": "month"}


def source_status(store, name: str) -> dict:
    """Latest imported period and import, property coverage and warnings for one supporting source."""
    table = store.sources[name]
    log = store.source_imports[store.source_imports["source"] == name]
    names = store.properties.set_index("property_id")["property_name"]
    if table.empty:
        return {"loaded": False, "latest_period": "", "latest_file": "", "imported": "", "covered": [],
                "missing": list(names), "warnings": 0, "imports": 0}
    period = table[PERIOD_FIELD[name]].dropna()
    latest = log.sort_values("imported_at").iloc[-1] if not log.empty else None
    covered = sorted(set(table["property_id"]))
    return {"loaded": True, "latest_period": str(period.max()) if not period.empty else "",
            "latest_file": latest["file_name"] if latest is not None else "",
            "imported": latest["imported_at"] if latest is not None else "",
            "covered": [names.get(p, p) for p in covered],
            "missing": [names.get(p, p) for p in store.properties["property_id"] if p not in covered],
            "warnings": int(pd.to_numeric(log["warnings"], errors="coerce").fillna(0).sum()),
            "imports": len(log)}
