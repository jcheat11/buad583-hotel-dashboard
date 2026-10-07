"""
Creates the FICTIONAL sample files in sample_data/.

Every hotel, name and number here is made up, so these files are safe to share (for
example in a hosted demo). Run it again at any time:

    python scripts/make_sample_data.py
"""
import calendar
import copy
import csv
import random
import sys
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from hotel_dashboard import definitions as d  # noqa: E402
from hotel_dashboard import sources  # noqa: E402

OUT = ROOT / "sample_data"
YEAR = 2026
LAST_ACTUAL_IN_HISTORY = 7       # January-July go in the "history" file; August is the new monthly close
CLOSE_MONTH = 8

# id, name, keys, brand, asset cohort, stabilization stage, base occupancy, base ADR
# (Group names are fictional demo labels; all figures are made up.)
HOTELS = [
    ("H01", "Harbor House Hotel", 140, "Harborline Hotels", "Legacy Assets", "Stabilized Properties", 0.80, 196.0),
    ("H02", "Mesa Suites", 110, "LivAway Suites", "Legacy Assets", "Stabilized Properties", 0.77, 172.0),
    ("H03", "Parkside Inn", 96, "Harborline Hotels", "2023 Acquisitions", "Stabilized Properties", 0.73, 165.0),
    ("H04", "Riverbend Lodge", 124, "LivAway Suites", "2023 Acquisitions", "Stabilized Properties", 0.75, 158.0),
    ("H05", "Summit Point Hotel", 132, "Harborline Hotels", "2023 Acquisitions", "Ramp-up Properties", 0.64, 182.0),
    ("H06", "Canyon Gate Suites", 104, "LivAway Suites", "2025 Developments", "Ramp-up Properties", 0.56, 149.0),
]
SEASON = {1: 0.88, 2: 0.93, 3: 0.99, 4: 1.01, 5: 1.04, 6: 1.07, 7: 1.09, 8: 1.07,
          9: 1.01, 10: 0.99, 11: 0.92, 12: 0.86}
COLUMNS = (list(d.ID_FIELDS) + list(d.ROOM_FIELDS) + list(d.FINANCIAL_FIELDS) + list(d.OTHER_EXPENSE_DETAIL_FIELDS)
           + list(d.REPORTED_FIELDS))
# How the fictional "other expenses (below GOP)" splits into the source P&L groups (last one = remainder).
BELOW_GOP_SPLIT = {"insurance_expense": 0.38, "real_estate_tax": 0.34, "personal_property_tax": 0.08,
                   "other_taxes": 0.06, "misc_other_expense": None}


def build_row(hotel, month, occupancy, adr, noise, year=YEAR):
    """One hotel-month of figures. Totals are built from the rounded lines, so they reconcile exactly."""
    pid, _, keys, *_ = hotel
    days = calendar.monthrange(year, month)[1]
    available = keys * days
    sold = round(available * occupancy)
    rooms_revenue = round(sold * adr, 2)
    other_revenue = round(rooms_revenue * 0.055 * noise(), 2)
    total_revenue = round(rooms_revenue + other_revenue, 2)
    expenses = {
        "labor_expense": round((available * 18 + sold * 20) * noise(), 2),
        "rooms_expense": round(sold * 14 * noise(), 2),
        "ga_expense": round(available * 9 * noise(), 2),
        "maintenance_expense": round(available * 5 * noise(), 2),
        "sales_marketing_expense": round(total_revenue * 0.045 * noise(), 2),
        "utilities_expense": round(available * 5.5 * noise(), 2),
        "mgmt_fees_royalties": round(total_revenue * 0.06, 2),
    }
    total_opex = round(sum(expenses.values()), 2)
    gop = round(total_revenue - total_opex, 2)
    other_expenses = round(keys * 420 * noise(), 2)
    ebitda = round(gop - other_expenses, 2)
    capital_reserve = round(total_revenue * 0.04, 2)
    return {
        "property_id": pid, "month": date(year, month, 1),
        "rooms_available": available, "rooms_sold": sold, "rooms_revenue": rooms_revenue,
        "other_revenue": other_revenue, "total_revenue": total_revenue, **expenses,
        "total_operating_expenses": total_opex, "gop": gop, "other_expenses": other_expenses,
        **split_below_gop(other_expenses),
        "ebitda": ebitda, "capital_reserve": capital_reserve, "noi": round(ebitda - capital_reserve, 2),
        "reported_occupancy": round(sold / available, 4),
        "reported_adr": round(rooms_revenue / sold, 2),
        "reported_revpar": round(rooms_revenue / available, 2),
    }


def split_below_gop(other_expenses):
    parts = {f: round(other_expenses * share, 2) for f, share in BELOW_GOP_SPLIT.items() if share}
    parts["misc_other_expense"] = round(other_expenses - sum(parts.values()), 2)
    return parts


def budget_rates(hotel, month):
    *_, stage, base_occ, base_adr = hotel
    occupancy = base_occ * SEASON[month]
    if stage == "Ramp-up Properties":
        occupancy += 0.012 * (month - 6)          # still ramping up: occupancy climbs through the year
    return min(occupancy, 0.93), round(base_adr * (0.94 + 0.06 * SEASON[month]), 2)


def make_data():
    rng = random.Random(583)
    budget, actual = [], []
    for month in range(1, 13):
        for hotel in HOTELS:
            occ_b, adr_b = budget_rates(hotel, month)
            budget.append(build_row(hotel, month, occ_b, adr_b, noise=lambda: 1.0))
            if month > CLOSE_MONTH:
                continue
            occ_a = occ_b + rng.uniform(-0.035, 0.010)
            adr_a = adr_b * (1 + rng.uniform(0.005, 0.040))
            if month == CLOSE_MONTH and hotel[0] == "H06":
                occ_a = occ_b - 0.075                 # the ramp-up hotel falls behind plan in August
            row = build_row(hotel, month, occ_a, adr_a, noise=lambda: rng.uniform(0.97, 1.04))
            if month == CLOSE_MONTH and hotel[0] == "H02":
                row = _raise_labor(row, 1.21)         # Mesa Suites overspends on labor in August
            actual.append(row)
    return budget, actual


def _raise_labor(row, factor):
    row = dict(row)
    extra = round(row["labor_expense"] * (factor - 1), 2)
    row["labor_expense"] = round(row["labor_expense"] + extra, 2)
    row["total_operating_expenses"] = round(row["total_operating_expenses"] + extra, 2)
    for field in ("gop", "ebitda", "noi"):         # the extra cost flows down to every profit line
        row[field] = round(row[field] - extra, 2)
    return row


# --- Writing the files -----------------------------------------------------------------

HEADER_FILL = PatternFill("solid", fgColor="DDE7EA")


def write_workbook(path, sheets, readme=False):
    wb = Workbook()
    wb.remove(wb.active)
    if readme:
        _add_readme(wb)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        ws.append([d.ALL_FIELDS[f] for f in COLUMNS])
        for row in rows:
            ws.append([row.get(f) for f in COLUMNS])
        for i, field in enumerate(COLUMNS, start=1):
            cell = ws.cell(row=1, column=i)
            cell.font, cell.fill = Font(bold=True), HEADER_FILL
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            ws.column_dimensions[cell.column_letter].width = 14 if i > 2 else 12
            number_format = ("mmm yyyy" if field == "month" else "0.00%" if field == "reported_occupancy"
                             else "#,##0" if field in ("rooms_available", "rooms_sold") else "#,##0.00")
            for r in range(2, len(rows) + 2):
                ws.cell(row=r, column=i).number_format = number_format
        ws.freeze_panes = "C2"
        ws.row_dimensions[1].height = 32
    wb.save(path)


def _add_readme(wb):
    ws = wb.create_sheet("ReadMe")
    ws.append(["Monthly hotel report template (v1)"])
    ws.append(["Fill in the 'Actual' sheet with closed monthly results and/or the 'Budget' sheet with the plan."])
    ws.append(["One row per hotel per month. Do not add subtotal or portfolio rows; the app adds hotels up itself."])
    ws.append(["The app ignores this ReadMe sheet."])
    ws.append([])
    ws.append(["Column heading", "Required?", "What it means"])
    for field in COLUMNS:
        ws.append([d.ALL_FIELDS[field], "Required" if field in d.REQUIRED_FIELDS else "Optional",
                   d.FIELD_NOTES.get(field, "")])
    ws["A1"].font = Font(bold=True, size=14)
    for cell in ws[6]:
        cell.font, cell.fill = Font(bold=True), HEADER_FILL
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 30, 12
    ws.column_dimensions["C"].width = 100


def write_properties(path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["property_id", "property_name", "keys", "brand", "asset_cohort", "stabilization_stage"])
        for pid, name, keys, brand, cohort, stage, *_ in HOTELS:
            writer.writerow([pid, name, keys, brand, cohort, stage])


# --- Fictional demo files for the separate sources (Revenue Mix, Pace, STR, Debt) ---------------
DEMO_NOTE = "FICTIONAL DEMO DATA: made-up hotels and figures to exercise the dashboard. Not company results."


def write_source_file(path, source, rows):
    """A demo source file: the data sheet first, then a ReadMe saying it is fictional."""
    spec = sources.SOURCES[source]
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append([c.heading for c in spec.columns])
    for row in rows:
        ws.append([row.get(c.field) for c in spec.columns])
    for cell in ws[1]:
        cell.font, cell.fill = Font(bold=True), HEADER_FILL
        ws.column_dimensions[cell.column_letter].width = 18
    note = wb.create_sheet("ReadMe")
    note.append([DEMO_NOTE])
    note["A1"].font = Font(bold=True, color="8E1D16")
    wb.save(path)


def make_source_demos(budget, actual):
    rng = random.Random(584)                       # separate from the monthly figures, which stay unchanged
    ids = [h[0] for h in HOTELS]
    budget_by = {(r["property_id"], r["month"].month): r for r in budget}

    channel = []
    for r in actual:
        rooms = r["rooms_revenue"]
        sellaway, direct, ota = (round(rooms * rng.uniform(*band), 2)
                                 for band in ((0.10, 0.18), (0.24, 0.31), (0.36, 0.44)))
        channel.append({"property_id": r["property_id"], "month": r["month"], "sellaway_revenue": sellaway,
                        "direct_revenue": direct, "ota_revenue": ota,
                        "other_channel_revenue": round(rooms - sellaway - direct - ota, 2)})

    pace = []
    for as_of, lead_shift, hotels in ((date(YEAR, 8, 1), 1, ids), (date(YEAR, 9, 1), 0, ids[:5])):
        for pid in hotels:
            for stay in range(as_of.month, 13):
                b = budget_by[(pid, stay)]
                share = max(0.05, 0.9 - 0.22 * (stay - as_of.month + lead_shift)) * rng.uniform(0.93, 1.05)
                nights = round(b["rooms_sold"] * share)
                pace.append({"property_id": pid, "as_of_date": as_of, "stay_month": date(YEAR, stay, 1),
                             "room_nights_otb": nights,
                             "room_revenue_otb": round(nights * b["reported_adr"] * rng.uniform(0.97, 1.03), 2)})

    strs = []
    for r in actual:
        if r["property_id"] == "H06":
            continue                               # the newest hotel has no STR subscription yet
        occ, adr = r["rooms_sold"] / r["rooms_available"], r["rooms_revenue"] / r["rooms_sold"]
        comp_occ, comp_adr = occ * rng.uniform(0.94, 1.07), adr * rng.uniform(0.95, 1.06)
        month = r["month"]
        strs.append({"property_id": r["property_id"], "report_type": "Historical", "period_type": "Month",
                     "period": month, "report_date": date(YEAR, month.month + 1, 17),
                     "occupancy": round(occ, 4), "adr": round(adr, 2), "revpar": round(occ * adr, 2),
                     "comp_occupancy": round(comp_occ, 4), "comp_adr": round(comp_adr, 2),
                     "comp_revpar": round(comp_occ * comp_adr, 2), "comp_set": "Demo comp set"})
    for pid in ids[:5]:
        for stay in (9, 10, 11):
            otb = rng.uniform(0.35, 0.7) * (1 - 0.15 * (stay - 9))
            strs.append({"property_id": pid, "report_type": "Forward", "period_type": "Stay Month",
                         "period": date(YEAR, stay, 1), "report_date": date(YEAR, 9, 8),
                         "occupancy": round(otb, 4), "comp_occupancy": round(otb * rng.uniform(0.9, 1.1), 4),
                         "comp_set": "Demo comp set"})

    write_source_file(OUT / "DEMO_channel_mix_2026.xlsx", "channel_mix", channel)
    write_source_file(OUT / "DEMO_pace_2026-09-01.xlsx", "pace", pace)
    write_source_file(OUT / "DEMO_str_2026.xlsx", "str", strs)
    make_debt_demo(budget, actual)
    for source in sources.SOURCES:
        (OUT / f"TEMPLATE_{source}.xlsx").write_bytes(sources.template_bytes(source))


def make_debt_demo(budget, actual):
    """
    The debt file, consistent with the monthly figures: the cash flow available for debt service is each
    hotel's NOI from the actuals (2026 months); the 2025 months, which have no monthly file in the demo,
    use 97% of the same calendar month's 2026 budget NOI. Debt service is sized from the 2026 budget NOI.
    """
    minimums = {"H01": 1.25, "H02": 1.30, "H03": 1.20, "H04": 1.25, "H05": 1.35, "H06": None}
    cover = {"H05": 1.18, "H02": 1.34, "H06": 1.05}          # H05 falls short of its minimum; H06 is ramping up
    budget_noi = {(r["property_id"], r["month"].month): r["noi"] for r in budget}
    actual_noi = {(r["property_id"], r["month"].month): r["noi"] for r in actual}
    debt = []
    for pid, *_ in HOTELS:
        annual = sum(budget_noi[(pid, m)] for m in range(1, 13))
        service = round(annual / 12 / cover.get(pid, 1.55), -2)
        # H06 (opened 2025) has negative NOI until March, so its loan file starts in April.
        month = date(YEAR, 4, 1) if pid == "H06" else date(YEAR - 1, 8, 1)
        while month <= date(YEAR, CLOSE_MONTH, 1):
            cash = (actual_noi[(pid, month.month)] if month.year == YEAR
                    else budget_noi[(pid, month.month)] * 0.97)
            debt.append({"property_id": pid, "month": month, "debt_service": service, "cash_flow": round(cash, 2),
                         "dscr_minimum": minimums[pid], "lender": "Demo Lender"})
            month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    write_source_file(OUT / "DEMO_debt_service.xlsx", "debt", debt)


def make_2027_budgets():
    """Two separate fictional 2027 budget workbooks (one per property group), for the separate-budgets demo."""
    legacy = [h for h in HOTELS if h[4] == "Legacy Assets"]
    others = [h for h in HOTELS if h[4] != "Legacy Assets"]
    for name, hotels in (("budget_2027_legacy_assets.xlsx", legacy), ("budget_2027_other_properties.xlsx", others)):
        rows = []
        for month in range(1, 13):
            for hotel in hotels:
                occupancy, adr = budget_rates(hotel, month)
                rows.append(build_row(hotel, month, min(occupancy + 0.01, 0.93), round(adr * 1.03, 2),
                                      noise=lambda: 1.0, year=YEAR + 1))
        write_workbook(OUT / name, {"Budget": rows})


def make_combined_sample():
    """One fictional workbook with BOTH sheets (August actuals + August budget), for the combined route."""
    import pandas as pd
    actual = pd.read_excel(OUT / "monthly_2026-08.xlsx", sheet_name="Actual")
    budget = pd.read_excel(OUT / "budget_2026.xlsx", sheet_name="Budget")
    budget = budget[pd.to_datetime(budget["Month"]).dt.month == CLOSE_MONTH]
    with pd.ExcelWriter(OUT / "combined_2026-08_actual_and_budget.xlsx", engine="openpyxl") as writer:
        actual.to_excel(writer, sheet_name="Actual", index=False)
        budget.to_excel(writer, sheet_name="Budget", index=False)


def main():
    OUT.mkdir(exist_ok=True)
    budget, actual = make_data()
    history = [r for r in actual if r["month"].month <= LAST_ACTUAL_IN_HISTORY]
    close = [r for r in actual if r["month"].month == CLOSE_MONTH]

    write_properties(OUT / "properties.csv")
    write_workbook(OUT / "budget_2026.xlsx", {"Budget": budget})
    write_workbook(OUT / "actuals_2026-01_to_2026-07.xlsx", {"Actual": history})
    write_workbook(OUT / "monthly_2026-08.xlsx", {"Actual": close})
    write_workbook(OUT / "TEMPLATE_monthly_hotel_report.xlsx", {"Actual": [], "Budget": []}, readme=True)

    # A deliberately broken copy of the August file, to demonstrate the data checks.
    broken = copy.deepcopy(close)
    broken[1]["rooms_sold"] = None                                   # missing field (Mesa Suites)
    broken.append(dict(broken[3]))                                   # duplicate month (Riverbend Lodge)
    broken.append({**broken[0], "property_id": "TOTAL"})             # subtotal row copied from a report
    broken[0]["reported_adr"] = round(broken[0]["reported_adr"] + 3, 2)        # ADR does not reconcile
    broken[2]["total_revenue"] = round(broken[2]["total_revenue"] + 1250, 2)   # total does not reconcile
    write_workbook(OUT / "monthly_2026-08_with_errors.xlsx", {"Actual": broken})
    make_source_demos(budget, actual)
    make_2027_budgets()
    make_combined_sample()
    print(f"Wrote fictional sample files to {OUT}")


if __name__ == "__main__":
    main()
