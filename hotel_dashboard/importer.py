"""
Reads a monthly hotel Excel file, checks it, and turns it into clean rows.

The file must follow the template in sample_data/TEMPLATE_monthly_hotel_report.xlsx:
  - a sheet named "Actual" (closed monthly results) and/or "Budget"
  - one row per hotel per month, with the column headings listed in definitions.py

Every problem found is reported as an Issue:
  error   - the file can't be trusted as it is, so NOTHING from it is imported
  warning - the figures are imported, but flagged on the dashboard
"""
from __future__ import annotations

import calendar
import hashlib
import io
import numbers
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from . import definitions as d
from .formatting import money, pct

ERROR = "error"
WARNING = "warning"

# Words that mark a subtotal row copied from a report (the app calculates its own totals).
SUBTOTAL_WORDS = ("total", "subtotal", "portfolio", "stabilized", "all properties")


@dataclass
class Issue:
    severity: str          # "error" or "warning"
    check: str             # e.g. "Missing field", "Duplicate month", "Does not reconcile"
    message: str
    sheet: str = ""
    excel_row: int | None = None
    property_id: str = ""
    month: str = ""

    @property
    def scenario(self) -> str:
        return self.sheet if self.sheet in d.SCENARIO_SHEETS else ""


@dataclass
class ImportResult:
    file_name: str
    file_hash: str
    rows: pd.DataFrame     # one row per scenario + hotel + month, numbers already cleaned
    issues: list[Issue]

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def can_import(self) -> bool:
        return not self.errors and not self.rows.empty


# ------------------------------------------------------------------------------------
# Reading single values
# ------------------------------------------------------------------------------------

def _header_key(text) -> str:
    """'Rooms Sold', 'rooms_sold' and 'ROOMS SOLD ' all become 'roomssold'."""
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


_HEADER_LOOKUP = {}
for _field, _label in d.ALL_FIELDS.items():
    _HEADER_LOOKUP[_header_key(_field)] = _field
    _HEADER_LOOKUP[_header_key(_label)] = _field
for _alias, _field in d.HEADER_ALIASES.items():
    _HEADER_LOOKUP[_header_key(_alias)] = _field

_DAILY_HEADERS = {_header_key("as_of_date"), _header_key("As of"), _header_key("Business Date")}


def _is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def parse_month(value) -> str | None:
    """Turns an Excel date, '2026-08', 'Aug 2026' or 'August 2026' into '2026-08'."""
    if _is_blank(value):
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return f"{value.year:04d}-{value.month:02d}"
    text = str(value).strip()
    for fmt in ("%Y-%m", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%b %Y", "%B %Y", "%m/%Y", "%Y/%m"):
        try:
            parsed = datetime.strptime(text, fmt)
            return f"{parsed.year:04d}-{parsed.month:02d}"
        except ValueError:
            pass
    return None


def parse_number(value) -> tuple[float | None, bool]:
    """Returns (number, ok). Blank -> (None, True). Text that isn't a number -> (None, False)."""
    if _is_blank(value):
        return None, True
    if isinstance(value, bool):
        return None, False
    if isinstance(value, numbers.Real):
        return float(value), True
    text = str(value).strip().replace(",", "").replace("$", "")
    negative = text.startswith("(") and text.endswith(")")   # accounting style (1,234)
    text = text.strip("()")
    is_percent = text.endswith("%")
    text = text.rstrip("%").strip()
    try:
        number = float(text)
    except ValueError:
        return None, False
    if is_percent:
        number /= 100
    return (-number if negative else number), True


def _days_in_month(month: str) -> int:
    return calendar.monthrange(int(month[:4]), int(month[5:7]))[1]


# ------------------------------------------------------------------------------------
# Reading a whole file
# ------------------------------------------------------------------------------------

def read_monthly_workbook(source, file_name: str | None, properties: pd.DataFrame,
                          today: date | None = None) -> ImportResult:
    """
    source      - a file path, or the raw bytes of an uploaded file
    properties  - the hotel list (from properties.csv), used to recognise property IDs
    today       - used to spot "actual" figures for a month that hasn't closed yet
    """
    raw = Path(source).read_bytes() if isinstance(source, (str, Path)) else bytes(source)
    file_name = file_name or Path(str(source)).name
    file_hash = hashlib.sha256(raw).hexdigest()
    today = today or date.today()
    issues: list[Issue] = []

    try:
        sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None, header=0, dtype=object)
    except Exception as exc:  # not an Excel file, password-protected, corrupt...
        issues.append(Issue(ERROR, "Unreadable file", f"Could not open the file as an Excel workbook ({exc})."))
        return ImportResult(file_name, file_hash, _empty_rows(), issues)

    by_lower_name = {name.strip().lower(): name for name in sheets}
    found = [s for s in d.SCENARIO_SHEETS if s.lower() in by_lower_name]

    if not found:
        if any(_header_key(c) in _DAILY_HEADERS for df in sheets.values() for c in df.columns):
            issues.append(Issue(ERROR, "Wrong file type",
                                "This looks like a DAILY export (it has an as-of date column). Daily 'Today's signals' "
                                "imports are planned but not built yet, and they will be kept separate from closed "
                                "monthly results."))
        else:
            issues.append(Issue(ERROR, "Missing field",
                                f"No 'Actual' or 'Budget' sheet found. Sheets in this file: {', '.join(sheets)}."))
        return ImportResult(file_name, file_hash, _empty_rows(), issues)

    known = properties.set_index("property_id")
    all_rows: list[dict] = []
    for scenario in found:
        df = sheets[by_lower_name[scenario.lower()]]
        all_rows += _read_sheet(df, scenario, known, today, issues)

    rows = pd.DataFrame(all_rows) if all_rows else _empty_rows()
    _check_duplicates(rows, issues)
    rows = rows.drop(columns=["_excel_row"], errors="ignore")
    return ImportResult(file_name, file_hash, rows, issues)


def _empty_rows() -> pd.DataFrame:
    return pd.DataFrame(columns=["scenario", "property_id", "month", *d.NUMERIC_FIELDS])


def _read_sheet(df: pd.DataFrame, scenario: str, known: pd.DataFrame, today: date,
                issues: list[Issue]) -> list[dict]:
    # 1. Match the column headings to our field names.
    columns: dict = {}
    for col in df.columns:
        key = _header_key(col)
        if key in _DAILY_HEADERS:
            issues.append(Issue(ERROR, "Wrong file type",
                                f"The {scenario} sheet has an as-of date column, so it looks like daily data. "
                                "Daily figures must not be mixed into closed monthly results.", sheet=scenario))
            return []
        field = _HEADER_LOOKUP.get(key)
        if field is None:
            if not str(col).startswith("Unnamed"):
                issues.append(Issue(WARNING, "Column ignored",
                                    f"Column '{col}' is not part of the template and was ignored.", sheet=scenario))
        elif field in columns.values():
            issues.append(Issue(ERROR, "Duplicate column", f"'{d.label(field)}' appears twice.", sheet=scenario))
            return []
        else:
            columns[col] = field
    df = df.rename(columns=columns)[list(columns.values())]

    missing_required = [f for f in d.REQUIRED_FIELDS if f not in df.columns]
    for field in missing_required:
        issues.append(Issue(ERROR, "Missing field",
                            f"The {scenario} sheet has no '{d.label(field)}' column.", sheet=scenario))
    if missing_required:
        return []

    missing_optional = [f for f in d.FINANCIAL_FIELDS if f not in df.columns]
    if missing_optional:
        issues.append(Issue(WARNING, "Missing field",
                            "Optional columns not provided: " + ", ".join(d.label(f) for f in missing_optional)
                            + ". Related variances and checks will be unavailable.", sheet=scenario))

    # 2. Check and clean each row.
    current_month = f"{today.year:04d}-{today.month:02d}"
    rows = []
    for index, record in df.iterrows():
        excel_row = int(index) + 2          # +1 for the heading row, +1 because Excel counts from 1
        values = record.to_dict()
        if all(_is_blank(v) for v in values.values()):
            continue

        def flag(severity, check, message, _row=excel_row):
            issues.append(Issue(severity, check, message, sheet=scenario, excel_row=_row,
                                property_id=pid, month=month or ""))

        pid = "" if _is_blank(values.get("property_id")) else str(values["property_id"]).strip()
        month = parse_month(values.get("month"))
        row_ok = True

        if not pid:
            flag(ERROR, "Missing field", "Property ID is blank.")
            row_ok = False
        elif pid not in known.index:
            if any(word in pid.lower() for word in SUBTOTAL_WORDS):
                flag(ERROR, "Subtotal row", f"'{pid}' looks like a subtotal row. Remove it: the app adds up "
                                            "hotels itself, and keeping it would count those hotels twice.")
            else:
                flag(ERROR, "Unknown property", f"'{pid}' is not in the property list (properties.csv).")
            row_ok = False
        if month is None:
            if _is_blank(values.get("month")):
                flag(ERROR, "Missing field", "Month is blank.")
            else:
                flag(ERROR, "Invalid value", f"Month '{values.get('month')}' is not a recognisable month "
                                             "(use an Excel date or text like 2026-08).")
            row_ok = False

        numbers = {}
        for field in d.NUMERIC_FIELDS:
            if field not in df.columns:
                numbers[field] = None
                continue
            number, ok = parse_number(values.get(field))
            numbers[field] = number
            if not ok:
                flag(ERROR, "Invalid value", f"'{d.label(field)}' is not a number ({values.get(field)!r}).")
                row_ok = False
            elif number is None and field in d.REQUIRED_FIELDS:
                flag(ERROR, "Missing field", f"'{d.label(field)}' is blank.")
                row_ok = False
            elif number is None and field in d.FINANCIAL_FIELDS:
                flag(WARNING, "Missing field", f"'{d.label(field)}' is blank.")

        if not row_ok:
            continue

        occ = numbers["reported_occupancy"]
        if occ is not None and occ > 1.5:          # accept 76 as well as 0.76
            numbers["reported_occupancy"] = occ / 100

        _check_rooms(numbers, known.loc[pid], month, flag)
        _check_subtotals(numbers, flag)
        _check_reported_rates(numbers, flag)
        if scenario == "Actual" and month >= current_month:
            flag(WARNING, "Month not closed", f"Actual figures for {month}, which has not closed yet.")

        rows.append({"scenario": scenario, "property_id": pid, "month": month, **numbers,
                     "_excel_row": excel_row})
    return rows


def _check_rooms(n: dict, prop: pd.Series, month: str, flag) -> None:
    avail, sold, revenue = n["rooms_available"], n["rooms_sold"], n["rooms_revenue"]
    if avail < 0 or sold < 0:
        flag(ERROR, "Invalid value", "Rooms available and rooms sold cannot be negative.")
    elif sold > avail:
        flag(ERROR, "Does not reconcile", f"Rooms sold ({sold:,.0f}) is more than rooms available ({avail:,.0f}).")
    elif sold == 0 and revenue:
        flag(WARNING, "Does not reconcile", "Rooms revenue is recorded but rooms sold is zero, so ADR can't be calculated.")

    keys = prop.get("keys")
    if not _is_blank(keys):
        capacity = float(keys) * _days_in_month(month)
        if avail > capacity + 0.5:
            flag(WARNING, "Does not reconcile",
                 f"Rooms available ({avail:,.0f}) is more than keys x days in month "
                 f"({float(keys):,.0f} x {_days_in_month(month)} = {capacity:,.0f}).")


def _check_subtotals(n: dict, flag) -> None:
    for total, plus, minus in d.SUBTOTAL_RULES:
        parts = plus + minus
        if n.get(total) is None or any(n.get(p) is None for p in parts):
            continue                            # can't check without all the pieces
        expected = sum(n[p] for p in plus) - sum(n[p] for p in minus)
        difference = n[total] - expected
        if abs(difference) > d.TOTAL_TOLERANCE:
            formula = " + ".join(d.label(p) for p in plus) + "".join(f" - {d.label(p)}" for p in minus)
            flag(WARNING, "Does not reconcile",
                 f"{d.label(total)} is {money(n[total])} but {formula} = {money(expected)} "
                 f"(difference {money(difference)}).")


def _check_reported_rates(n: dict, flag) -> None:
    avail, sold, revenue = n["rooms_available"], n["rooms_sold"], n["rooms_revenue"]
    if n["reported_occupancy"] is not None and avail:
        calculated = sold / avail
        if abs(n["reported_occupancy"] - calculated) * 100 > d.OCCUPANCY_TOLERANCE_PP:
            flag(WARNING, "Does not reconcile",
                 f"Reported occupancy {pct(n['reported_occupancy'], 2)} does not match rooms sold / rooms "
                 f"available = {pct(calculated, 2)}.")
    if n["reported_adr"] is not None and sold:
        calculated = revenue / sold
        if abs(n["reported_adr"] - calculated) > d.RATE_TOLERANCE + 1e-9:
            flag(WARNING, "Does not reconcile",
                 f"Reported ADR {money(n['reported_adr'])} does not match rooms revenue / rooms sold = "
                 f"{money(calculated)}.")
    if n["reported_revpar"] is not None and avail:
        calculated = revenue / avail
        if abs(n["reported_revpar"] - calculated) > d.RATE_TOLERANCE + 1e-9:
            flag(WARNING, "Does not reconcile",
                 f"Reported RevPAR {money(n['reported_revpar'])} does not match rooms revenue / rooms "
                 f"available = {money(calculated)}.")


def _check_duplicates(rows: pd.DataFrame, issues: list[Issue]) -> None:
    """The same hotel and month twice on one sheet would be counted twice, so it blocks the import."""
    if rows.empty:
        return
    key = ["scenario", "property_id", "month"]
    dupes = rows[rows.duplicated(key, keep=False)]
    for (scenario, pid, month), group in dupes.groupby(key):
        excel_rows = ", ".join(str(r) for r in group["_excel_row"])
        issues.append(Issue(ERROR, "Duplicate month",
                            f"{pid} {month} appears more than once on the {scenario} sheet (rows {excel_rows}).",
                            sheet=scenario, property_id=pid, month=month))
