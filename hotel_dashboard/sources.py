"""
Extra data sources, each with its OWN small import, kept apart from the monthly financial upload:

  channel_mix  Revenue Mix: room revenue by booking channel, one row per hotel + month
  pace         Forward Pace: business on the books for future stay months, as of a report date
  str          STR competitive-set results. "Historical" rows (closed periods) and "Forward" rows
               (on-the-books occupancy for future stay months) are stored together but never mixed.
  debt         Debt service and covenant minimums, for DSCR

Each file is one sheet (Excel) or a CSV, one row per record, with the headings listed in SOURCES.
Nothing here guesses a missing figure: blank stays blank, and screens show a "data needed" state.
Indexes and ratios are calculated per property and are never averaged across properties.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from . import definitions as d
from . import metrics
from .formatting import month_label
from .importer import ERROR, WARNING, SUBTOTAL_WORDS, ImportResult, Issue, _header_key, _is_blank, \
    parse_month, parse_number


@dataclass(frozen=True)
class Column:
    field: str
    heading: str
    kind: str                  # "text", "month", "date", "number", "rate" (0.76 or 76%), "choice"
    required: bool = True
    note: str = ""
    choices: tuple = ()


@dataclass(frozen=True)
class Source:
    name: str
    title: str
    file_hint: str
    columns: tuple
    key: tuple                 # fields that identify one record; a repeat is a duplicate
    note: str


CHANNEL_NAMES = {"sellaway_revenue": "SellAway Revenue", "direct_revenue": "Direct Booking", "ota_revenue": "OTA",
                 "other_channel_revenue": "Other"}          # as shown on the dashboard
CHANNELS = {                   # field -> file heading, in display order
    "sellaway_revenue": "SellAway Revenue",
    "direct_revenue": "Direct Booking Revenue",
    "ota_revenue": "OTA Revenue",
    "other_channel_revenue": "Other Channel Revenue",
}

SOURCES = {
    "channel_mix": Source(
        "channel_mix", "Revenue Mix (booking channels)", "Channel mix export (Excel or CSV)",
        (Column("property_id", "Property ID", "text", note="Code from properties.csv."),
         Column("month", "Month", "month", note="The closed month the revenue belongs to."),
         *(Column(f, h, "number", required=False, note="Room revenue from this channel, in US$.")
           for f, h in CHANNELS.items())),
        ("property_id", "month"),
        "One row per hotel per month. Leave a channel blank if it is not reported (it is NOT read as zero)."),
    "pace": Source(
        "pace", "Forward Pace", "Pace export (Excel or CSV)",
        (Column("property_id", "Property ID", "text", note="Code from properties.csv."),
         Column("as_of_date", "As Of Date", "date", note="The date the report was run. Every figure is 'as of' it."),
         Column("stay_month", "Stay Month", "month", note="The future month the rooms are booked for."),
         Column("room_nights_otb", "Room Nights On Books", "number", note="Room nights booked for that month."),
         Column("room_revenue_otb", "Room Revenue On Books", "number", note="Room revenue booked, US$.")),
        ("property_id", "as_of_date", "stay_month"),
        "One row per hotel, as-of date and future stay month. Budget comes from the imported monthly budget."),
    "str": Source(
        "str", "STR Competitive Set", "STR report, re-keyed or exported (Excel or CSV)",
        (Column("property_id", "Property ID", "text", note="Code from properties.csv."),
         Column("report_type", "Report Type", "choice", choices=("Historical", "Forward"),
                note="Historical = closed results; Forward = occupancy on the books for future months."),
         Column("period_type", "Period Type", "choice", required=False,
                choices=("Month", "Running 3", "Running 12", "Year to Date", "Stay Month"),
                note="Historical: Month, Running 3, Running 12 or Year to Date. Forward: Stay Month."),
         Column("period", "Period", "month", note="Historical: last month of the period. Forward: the stay month."),
         Column("report_date", "Report Date", "date", note="The date STR produced the report."),
         Column("occupancy", "Occupancy", "rate", note="The hotel's occupancy (0.76 or 76%)."),
         Column("adr", "ADR", "number", required=False, note="Historical: required."),
         Column("revpar", "RevPAR", "number", required=False, note="Historical: required."),
         Column("comp_occupancy", "Comp Set Occupancy", "rate", note="The competitive set's occupancy."),
         Column("comp_adr", "Comp Set ADR", "number", required=False, note="Historical: required."),
         Column("comp_revpar", "Comp Set RevPAR", "number", required=False, note="Historical: required."),
         Column("comp_set", "Comp Set Name", "text", required=False, note="Optional label for the comp set.")),
        ("property_id", "report_type", "period_type", "period", "report_date"),
        "One row per hotel and period. Indexes (MPI, ARI, RGI) are calculated here, per hotel."),
    "debt": Source(
        "debt", "Debt Service (DSCR)", "Debt schedule or lender report (Excel or CSV)",
        (Column("property_id", "Property ID", "text", note="Code from properties.csv."),
         Column("month", "Month", "month", note="The month the debt service was due."),
         Column("debt_service", "Debt Service", "number",
                note="Principal + interest due that month, all loans on the hotel added together, US$."),
         Column("cash_flow", "Cash Flow Available for Debt Service", "number", required=False,
                note="The DSCR numerator as defined by the loan agreement, US$. Needed until a numerator is agreed."),
         Column("dscr_minimum", "Minimum DSCR", "number", required=False,
                note="The covenant minimum for this hotel's loan, e.g. 1.25. No default is assumed."),
         Column("lender", "Lender", "text", required=False, note="Optional.")),
        ("property_id", "month"),
        "One row per hotel per month. DSCR uses only the figures supplied here."),
}


# ------------------------------------------------------------------------------------ reading
def _parse_date(value) -> str | None:
    if _is_blank(value):
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return f"{value.year:04d}-{value.month:02d}-{value.day:02d}"
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d %b %Y", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(str(value).strip(), fmt)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def empty_rows(source: str) -> pd.DataFrame:
    return pd.DataFrame(columns=[c.field for c in SOURCES[source].columns])


def read_source_file(source: str, raw: bytes, file_name: str, properties: pd.DataFrame) -> ImportResult:
    """Reads and checks one file for one source. Errors block the import; warnings are shown."""
    spec = SOURCES[source]
    file_hash = hashlib.sha256(raw).hexdigest()
    issues: list[Issue] = []
    try:
        if file_name.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(raw), dtype=object)
        else:
            sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None, dtype=object)
            data = [n for n in sheets if n.strip().lower() not in ("readme", "read me")]
            df = sheets[data[0]] if data else pd.DataFrame()
    except Exception as exc:
        issues.append(Issue(ERROR, "Unreadable file", f"Could not open the file ({exc})."))
        return ImportResult(file_name, file_hash, empty_rows(source), issues)

    lookup = {_header_key(c.heading): c for c in spec.columns} | {_header_key(c.field): c for c in spec.columns}
    columns = {}
    for col in df.columns:
        match = lookup.get(_header_key(col))
        if match is None:
            if not str(col).startswith("Unnamed"):
                issues.append(Issue(WARNING, "Column ignored", f"Column '{col}' is not part of the {spec.title} format."))
        else:
            columns[col] = match.field
    df = df.rename(columns=columns)
    missing = [c.heading for c in spec.columns if c.required and c.field not in df.columns]
    if missing:
        issues.append(Issue(ERROR, "Missing field", "Required columns not found: " + ", ".join(missing) + "."))
        return ImportResult(file_name, file_hash, empty_rows(source), issues)

    known = set(properties["property_id"])
    rows = []
    for index, record in df.iterrows():
        values = {c.field: record.get(c.field) for c in spec.columns}
        if all(_is_blank(v) for v in values.values()):
            continue
        excel_row = int(index) + 2
        pid = "" if _is_blank(values["property_id"]) else str(values["property_id"]).strip()

        def flag(severity, check, message, _row=excel_row, _pid=pid):
            issues.append(Issue(severity, check, message, sheet=spec.title, excel_row=_row, property_id=_pid))

        row, ok = {"_excel_row": excel_row}, True
        for column in spec.columns:
            value = values[column.field]
            if _is_blank(value):
                row[column.field] = None
                if column.required:
                    flag(ERROR, "Missing field", f"'{column.heading}' is blank.")
                    ok = False
                continue
            if column.kind == "text":
                row[column.field] = str(value).strip()
            elif column.kind == "choice":
                choice = next((c for c in column.choices if _header_key(c) == _header_key(value)), None)
                if choice is None:
                    flag(ERROR, "Invalid value", f"'{column.heading}' must be one of: {', '.join(column.choices)}.")
                    ok = False
                row[column.field] = choice
            elif column.kind == "month":
                row[column.field] = parse_month(value)
            elif column.kind == "date":
                row[column.field] = _parse_date(value)
            else:
                number, good = parse_number(value)
                if not good:
                    flag(ERROR, "Invalid value", f"'{column.heading}' is not a number ({value!r}).")
                    ok = False
                elif number is not None and number < 0:
                    flag(ERROR, "Invalid value", f"'{column.heading}' cannot be negative.")
                    ok = False
                elif column.kind == "rate" and number is not None and number > 1.5:
                    number /= 100                                  # accept 76 as well as 0.76
                row[column.field] = number
            if column.kind in ("month", "date") and row[column.field] is None:
                flag(ERROR, "Invalid value", f"'{column.heading}' is not a recognisable "
                                             f"{'month' if column.kind == 'month' else 'date'} ({value!r}).")
                ok = False
        if pid and pid not in known:
            if any(word in pid.lower() for word in SUBTOTAL_WORDS):
                flag(ERROR, "Subtotal row", f"'{pid}' looks like a subtotal row. Remove it; the app adds hotels up itself.")
            else:
                flag(ERROR, "Unknown property", f"'{pid}' is not in the property list (properties.csv).")
            ok = False
        if ok:
            ok = CHECKS[source](row, flag)
        if ok:
            rows.append(row)

    result = pd.DataFrame(rows) if rows else empty_rows(source).assign(_excel_row=None)
    if not result.empty:
        key = list(spec.key)
        dupes = result[result.duplicated(key, keep=False)]
        for _, group in dupes.groupby(key, dropna=False):
            issues.append(Issue(ERROR, "Duplicate record", "The same record appears more than once (rows "
                                + ", ".join(str(r) for r in group["_excel_row"]) + ").", sheet=spec.title,
                                property_id=str(group["property_id"].iloc[0])))
    return ImportResult(file_name, file_hash, result.drop(columns="_excel_row"), issues)


# Source-specific checks: return False to reject the row.
def _check_channel(row, flag) -> bool:
    if all(row.get(f) is None for f in CHANNELS):
        flag(ERROR, "Missing field", "No channel revenue on this row.")
        return False
    return True


def _check_pace(row, flag) -> bool:
    if row["stay_month"] < row["as_of_date"][:7]:
        flag(ERROR, "Not a future period", f"Stay month {row['stay_month']} is before the as-of date "
                                           f"{row['as_of_date']}; pace covers future stay periods only.")
        return False
    return True


def _check_str(row, flag) -> bool:
    if row["report_type"] == "Historical":
        row["period_type"] = row.get("period_type") or "Month"
        needed = [c for c in ("adr", "revpar", "comp_adr", "comp_revpar") if row.get(c) is None]
        if needed:
            flag(ERROR, "Missing field", "Historical rows need " + ", ".join(
                c.heading for c in SOURCES["str"].columns if c.field in needed) + ".")
            return False
        if row["period_type"] == "Stay Month":
            flag(ERROR, "Invalid value", "'Stay Month' is a Forward period type, not Historical.")
            return False
        for occ, adr, revpar, who in (("occupancy", "adr", "revpar", "Hotel"),
                                      ("comp_occupancy", "comp_adr", "comp_revpar", "Comp set")):
            expected = row[occ] * row[adr]
            if expected and abs(row[revpar] - expected) / expected > 0.005:
                flag(WARNING, "Does not reconcile", f"{who} RevPAR is not occupancy x ADR (within 0.5%).")
    else:
        if row.get("period_type") not in (None, "Stay Month"):
            flag(ERROR, "Invalid value", "Forward rows use Period Type 'Stay Month' (or leave it blank).")
            return False
        row["period_type"] = "Stay Month"
        if row["period"] < row["report_date"][:7]:
            flag(ERROR, "Not a future period", "Forward rows must be for stay months on or after the report date.")
            return False
    return True


def _check_debt(row, flag) -> bool:
    if not row["debt_service"]:
        flag(ERROR, "Invalid value", "Debt Service must be more than zero to calculate DSCR.")
        return False
    return True


CHECKS = {"channel_mix": _check_channel, "pace": _check_pace, "str": _check_str, "debt": _check_debt}


def template_bytes(source: str) -> bytes:
    """A blank Excel template: headings on the data sheet, and a ReadMe sheet explaining each column."""
    spec = SOURCES[source]
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(columns=[c.heading for c in spec.columns]).to_excel(writer, sheet_name="Data", index=False)
        pd.DataFrame([{"Column": c.heading, "Required": "Yes" if c.required else "Optional",
                       "Meaning": c.note + (f" Allowed: {', '.join(c.choices)}." if c.choices else "")}
                      for c in spec.columns]).to_excel(writer, sheet_name="ReadMe", index=False)
    return buffer.getvalue()


# ------------------------------------------------------------------------------------ calculations
def _in_scope(rows: pd.DataFrame, ids) -> pd.DataFrame:
    return rows[rows["property_id"].isin(set(ids))] if not rows.empty else rows


def channel_mix(rows: pd.DataFrame, facts: pd.DataFrame, ids, month: str) -> dict:
    """
    Channel revenue for the hotels in scope and month (added up, never averaged).
    Shares of the total are only calculated once the four channels are confirmed as mutually
    exclusive (definitions.CHANNELS_MUTUALLY_EXCLUSIVE); until then each channel stands alone.
    """
    scoped = _in_scope(rows, ids)
    scoped = scoped[scoped["month"] == month] if not scoped.empty else scoped
    reporting = sorted(scoped["property_id"]) if not scoped.empty else []
    table = []
    for field, heading in CHANNELS.items():
        values = pd.to_numeric(scoped[field], errors="coerce") if not scoped.empty else pd.Series(dtype=float)
        complete = bool(reporting) and values.notna().all()
        table.append({"channel": CHANNEL_NAMES[field],
                      "revenue": float(values.sum()) if complete else None})
    result = {"table": pd.DataFrame(table), "reporting": reporting,
              "missing": sorted(set(ids) - set(reporting)), "shares": False, "difference": None}
    known = [r["revenue"] for r in table]
    if d.CHANNELS_MUTUALLY_EXCLUSIVE and reporting and None not in known:
        total = sum(known)
        result["shares"] = True
        result["table"]["share"] = [v / total if total else None for v in known]
        actual = metrics.combine(facts, reporting, month, "Actual")
        if actual and actual.get(d.CHANNEL_TOTAL_FIELD) is not None:
            result["difference"] = total - actual[d.CHANNEL_TOTAL_FIELD]
    return result


def pace_view(rows: pd.DataFrame, facts: pd.DataFrame, ids) -> dict | None:
    """
    On-the-books room nights and revenue per future stay month, from the LATEST as-of date for the
    hotels in scope (hotels without a report on that date are listed, not estimated). Budget is the
    imported monthly budget for the same hotels, so '% of budget on the books' is like for like.
    """
    scoped = _in_scope(rows, ids)
    if scoped.empty:
        return None
    as_of = scoped["as_of_date"].max()
    latest = scoped[scoped["as_of_date"] == as_of]
    covered = sorted(latest["property_id"].unique())
    earlier = scoped[(scoped["as_of_date"] < as_of) & scoped["property_id"].isin(covered)]
    previous = earlier["as_of_date"].max() if not earlier.empty else None
    table = []
    for stay_month, group in latest.groupby("stay_month"):
        nights, revenue = group["room_nights_otb"].sum(), group["room_revenue_otb"].sum()
        budget = metrics.combine(facts, group["property_id"], stay_month, "Budget")
        complete_budget = budget and len(budget["property_ids"]) == group["property_id"].nunique()
        prior = earlier[(earlier["as_of_date"] == previous) & (earlier["stay_month"] == stay_month)] \
            if previous else pd.DataFrame()
        same_hotels = previous and set(prior["property_id"]) == set(group["property_id"])
        table.append({
            "stay_month": stay_month, "hotels": group["property_id"].nunique(),
            "room_nights": nights, "room_revenue": revenue, "adr": revenue / nights if nights else None,
            "budget_room_nights": budget["rooms_sold"] if complete_budget else None,
            "pct_of_budget_nights": nights / budget["rooms_sold"] if complete_budget and budget["rooms_sold"] else None,
            "pct_of_budget_revenue": revenue / budget["rooms_revenue"]
            if complete_budget and budget["rooms_revenue"] else None,
            "pickup_nights": nights - prior["room_nights_otb"].sum() if same_hotels else None,
        })
    return {"as_of": as_of, "previous_as_of": previous, "covered": covered,
            "missing": sorted(set(ids) - set(covered)), "table": pd.DataFrame(table)}


def str_view(rows: pd.DataFrame, ids, month: str) -> dict:
    """
    Per-hotel STR results. Historical (closed months) and Forward (on the books) are returned
    separately. Indexes = hotel / comp set x 100, per hotel; they are never averaged across hotels,
    because every hotel has its own competitive set.
    """
    scoped = _in_scope(rows, ids)
    empty = pd.DataFrame()
    if scoped.empty:
        return {"historical": empty, "forward": empty, "latest_month": None, "missing": sorted(set(ids))}
    hist = scoped[(scoped["report_type"] == "Historical") & (scoped["period_type"] == "Month")]
    latest_month = hist["period"].max() if not hist.empty else None
    hist = hist[hist["period"] == month].sort_values("report_date").drop_duplicates("property_id", keep="last")
    if not hist.empty:
        hist = hist.assign(mpi=hist["occupancy"] / hist["comp_occupancy"] * 100,
                           ari=hist["adr"] / hist["comp_adr"] * 100,
                           rgi=hist["revpar"] / hist["comp_revpar"] * 100)
    forward = scoped[scoped["report_type"] == "Forward"]
    if not forward.empty:
        newest = forward.groupby("property_id")["report_date"].transform("max")
        forward = forward[forward["report_date"] == newest]
        forward = forward.assign(occupancy_index=forward["occupancy"] / forward["comp_occupancy"] * 100)
    return {"historical": hist.reset_index(drop=True), "forward": forward.reset_index(drop=True),
            "latest_month": latest_month, "missing": sorted(set(ids) - set(hist["property_id"]))}


def dscr_view(debt: pd.DataFrame, ids, month: str) -> pd.DataFrame:
    """
    One row per hotel: DSCR = cash flow available for debt service / debt service, over the trailing
    12 months ending in `month` when all 12 months are supplied, otherwise for `month` alone (labelled).
    Only figures supplied in the debt file are used; no numerator or threshold is assumed.
    """
    rows = []
    t12 = [metrics.shift_month(month, -n) for n in range(11, -1, -1)]
    for pid in sorted(set(ids)):
        mine = debt[debt["property_id"] == pid] if not debt.empty else debt
        have = mine[mine["month"].isin(t12)] if not mine.empty else mine
        row = {"property_id": pid, "period": "", "months": len(have), "cash_flow": None, "debt_service": None,
               "dscr": None, "minimum": None, "status": "missing", "note": ""}
        if have.empty or month not in set(have["month"]):
            row["note"] = f"No debt service for {month_label(month, short=True)}"
        else:
            use = have if len(have) == 12 else have[have["month"] == month]
            row["period"] = (f"T12 {month_label(t12[0], short=True)} – {month_label(month, short=True)}"
                             if len(have) == 12 else f"{month_label(month, short=True)} only "
                                                     f"(T12 needs 12 months; {len(have)} supplied)")
            row["debt_service"] = float(use["debt_service"].sum())
            current = mine[mine["month"] == month].iloc[0]
            row["minimum"] = None if pd.isna(current["dscr_minimum"]) else float(current["dscr_minimum"])
            cash = pd.to_numeric(use["cash_flow"], errors="coerce")
            if cash.isna().any():
                row["note"] = "No agreed cash-flow figure supplied (Cash Flow Available for Debt Service)"
            else:
                row["cash_flow"] = float(cash.sum())
                row["dscr"] = row["cash_flow"] / row["debt_service"]
                if row["minimum"] is None:
                    row["status"], row["note"] = "no_threshold", "No minimum DSCR supplied"
                elif row["dscr"] < row["minimum"]:
                    row["status"], row["note"] = "review", f"Below the {row['minimum']:.2f}x minimum"
                else:
                    row["status"], row["note"] = "good", f"At or above the {row['minimum']:.2f}x minimum"
        rows.append(row)
    return pd.DataFrame(rows)
