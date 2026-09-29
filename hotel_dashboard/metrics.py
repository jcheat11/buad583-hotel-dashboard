"""
The calculations behind every number on the dashboard.

The golden rule: for the chosen hotels, add up the underlying figures (rooms, revenue,
costs) FIRST, then calculate the rates. Rates are never averaged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from . import definitions as d
from .formatting import money, money_short

RATES = ["occupancy", "adr", "revpar"]


@dataclass(frozen=True)
class Scope:
    """Which hotels to show: all of them, one hotel, or one group within a classification."""
    kind: str = "all"                  # "all", "property" or "group"
    property_id: str | None = None
    classification: str | None = None  # e.g. "brand"
    group: str | None = None           # e.g. "Coastline Collection"

    def label(self, properties: pd.DataFrame) -> str:
        if self.kind == "property":
            names = properties.loc[properties["property_id"] == self.property_id, "property_name"]
            return names.iloc[0] if len(names) else str(self.property_id)
        if self.kind == "group":
            return f"{d.label(self.classification)}: {self.group}"
        return "All properties"


def property_ids_in_scope(properties: pd.DataFrame, scope: Scope) -> list[str]:
    if scope.kind == "property":
        ids = [scope.property_id]
    elif scope.kind == "group":
        ids = properties.loc[properties[scope.classification] == scope.group, "property_id"]
    else:
        ids = properties["property_id"]
    return sorted(set(ids))   # a set, so every hotel appears exactly once


def calculate_rates(totals: dict) -> dict:
    available, sold, revenue = totals["rooms_available"], totals["rooms_sold"], totals["rooms_revenue"]
    return {
        "occupancy": sold / available if available else None,
        "adr": revenue / sold if sold else None,
        "revpar": revenue / available if available else None,
    }


def combine(facts: pd.DataFrame, property_ids, month: str, scenario: str) -> dict | None:
    """
    Adds up one scenario's figures (Actual or Budget) for the chosen hotels in one month,
    then calculates the rates from those totals. Returns None if no hotel has figures.
    """
    ids = set(property_ids)
    rows = facts[(facts["scenario"] == scenario) & (facts["month"] == month) & facts["property_id"].isin(ids)]
    if rows.empty:
        return None
    if rows["property_id"].duplicated().any():
        raise ValueError(f"A hotel appears twice in {scenario} {month}; refusing to double count.")

    totals: dict = {}
    for field in d.ADDITIVE_FIELDS:
        values = pd.to_numeric(rows[field], errors="coerce")
        # A total is only shown when EVERY hotel supplied the line; otherwise it would be understated.
        totals[field] = float(values.sum()) if values.notna().all() else None
    totals.update(calculate_rates(totals))
    totals["noi"] = net_operating_income(totals)
    totals["property_ids"] = sorted(rows["property_id"])
    return totals


def net_operating_income(totals: dict) -> float | None:
    """
    NOI = GOP - other expenses (below GOP) - capital reserve, from the added-up lines.
    Only if one of those lines is missing does it fall back to the NOI line in the imported file.
    (The importer already warns when a file's NOI doesn't match its own lines.)
    """
    gop, other, reserve = totals.get("gop"), totals.get("other_expenses"), totals.get("capital_reserve")
    if None in (gop, other, reserve):
        return totals.get("noi")
    return gop - other - reserve


def month_summary(facts: pd.DataFrame, property_ids, month: str) -> dict:
    """
    Actual and budget for one month, compared like for like: the budget is added up for
    exactly the hotels that reported actuals. If any of them has no budget, no budget
    comparison is shown (a partial budget would make the variance look better than it is).
    """
    actual = combine(facts, property_ids, month, "Actual")
    reporting = actual["property_ids"] if actual else []
    budget = combine(facts, reporting, month, "Budget") if reporting else None
    budget_missing = sorted(set(reporting) - set(budget["property_ids"] if budget else []))
    return {
        "actual": actual,
        "budget": budget if budget and not budget_missing else None,
        "missing_actual": sorted(set(property_ids) - set(reporting)),
        "budget_missing": budget_missing,
    }


def variance(actual: dict | None, budget: dict | None, field: str) -> dict | None:
    """Actual vs budget for one line. For occupancy, the difference is a fraction (0.02 = 2 points)."""
    if not actual or not budget or actual.get(field) is None or budget.get(field) is None:
        return None
    a, b = actual[field], budget[field]
    difference = a - b
    return {
        "actual": a,
        "budget": b,
        "difference": difference,
        "percent": difference / abs(b) if b else None,
        "favorable": difference <= 0 if field in d.COST_FIELDS else difference >= 0,
    }


def revpar_bridge(actual: dict | None, budget: dict | None) -> dict | None:
    """
    Splits the RevPAR variance into an occupancy effect and a rate effect, which add up exactly:
        occupancy effect = (actual occupancy - budget occupancy) x budget ADR
        rate effect      = (actual ADR - budget ADR) x actual occupancy
    """
    if not actual or not budget:
        return None
    if None in (actual["occupancy"], actual["adr"], budget["occupancy"], budget["adr"]):
        return None
    occupancy_effect = (actual["occupancy"] - budget["occupancy"]) * budget["adr"]
    rate_effect = (actual["adr"] - budget["adr"]) * actual["occupancy"]
    return {"occupancy_effect": occupancy_effect, "rate_effect": rate_effect,
            "total": occupancy_effect + rate_effect}


# --- Months --------------------------------------------------------------------------

def shift_month(month: str, n: int) -> str:
    """shift_month('2026-01', -1) -> '2025-12'."""
    index = int(month[:4]) * 12 + int(month[5:7]) - 1 + n
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def months_with_actuals(facts: pd.DataFrame) -> list[str]:
    return sorted(facts.loc[facts["scenario"] == "Actual", "month"].unique())


def latest_closed_month(today: date) -> str:
    """The most recent month that has ended."""
    return shift_month(f"{today.year:04d}-{today.month:02d}", -1)


# --- Tables and series for the dashboard -------------------------------------------------

def trend(facts: pd.DataFrame, property_ids, end_month: str, n_months: int = 6) -> pd.DataFrame:
    """Occupancy, ADR and RevPAR for the months up to end_month, actual and budget."""
    rows = []
    for back in range(n_months - 1, -1, -1):
        month = shift_month(end_month, -back)
        summary = month_summary(facts, property_ids, month)
        for series, totals in (("Actual", summary["actual"]), ("Budget", summary["budget"])):
            if totals:
                rows.append({"month": month, "series": series, **{r: totals[r] for r in RATES},
                             "hotels": len(totals["property_ids"])})
    return pd.DataFrame(rows, columns=["month", "series", *RATES, "hotels"])


def review_triggers(actual: dict | None, budget: dict | None) -> list[tuple[str, dict]]:
    """
    The thresholds from definitions.py that one hotel crossed this month, as (field, variance) pairs.
    Used by both the exception map ('Review' status) and the briefing ('What needs a decision'),
    so the two always agree.
    """
    triggers = []
    revpar = variance(actual, budget, "revpar")
    if revpar and revpar["percent"] is not None and revpar["percent"] < d.REVIEW_REVPAR_VS_BUDGET:
        triggers.append(("revpar", revpar))
    gop = variance(actual, budget, "gop")
    if gop and gop["percent"] is not None and gop["percent"] < d.REVIEW_GOP_VS_BUDGET:
        triggers.append(("gop", gop))
    for field in d.EXPENSE_LINES:
        line = variance(actual, budget, field)
        if (line and line["percent"] is not None and line["percent"] >= d.ATTENTION_EXPENSE_OVER_BUDGET
                and line["difference"] >= d.ATTENTION_EXPENSE_MIN_DOLLARS):
            triggers.append((field, line))
    return triggers


def trigger_reason(field: str) -> str:
    if field == "revpar":
        return "RevPAR below budget"
    if field == "gop":
        return "GOP below budget"
    return f"{d.label(field)} over budget"


# --- Exception map: one row per hotel, four indicators ------------------------------------

STATUS_WORDS = {"good": "On Plan", "watch": "Watch", "review": "Review", "missing": "No data"}
_SEVERITY = {"good": 0, "missing": 1, "watch": 1, "review": 2}


def _vs_budget_status(v: dict | None, review_limit: float) -> str:
    """good = at, above or within 1% of budget; watch = further below but within the limit; review = beyond it."""
    if not v or v["percent"] is None:
        return "missing"
    if v["percent"] >= d.WATCH_BELOW_BUDGET:
        return "good"
    return "watch" if v["percent"] >= review_limit else "review"


def _labor_status(v: dict | None) -> str:
    """good = under or slightly over budget; watch = more than 2% over; review = over by the review threshold."""
    if not v or v["percent"] is None:
        return "missing"
    if v["percent"] >= d.ATTENTION_EXPENSE_OVER_BUDGET and v["difference"] >= d.ATTENTION_EXPENSE_MIN_DOLLARS:
        return "review"
    return "watch" if v["percent"] > d.WATCH_LABOR_OVER_BUDGET else "good"


def _data_status(actual, budget, warning_count: int) -> tuple[str, str]:
    if actual is None:
        return "review", "No actuals"
    if warning_count:
        return "watch", f"{warning_count} warning{'s' if warning_count != 1 else ''}"
    if budget is None:
        return "watch", "No budget"
    return "good", "Clean"


def exception_map(facts: pd.DataFrame, properties: pd.DataFrame, property_ids, month: str,
                  warnings: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    One row per hotel with a status for RevPAR vs budget, GOP vs budget, labor vs budget and data
    quality. 'overall' is the worst of the four, and is always 'review' when any review threshold
    from definitions.py is crossed, so it agrees with the briefing's decisions.
    Rows are sorted with the hotels needing attention first.
    """
    names = properties.set_index("property_id")["property_name"]
    rows = []
    for pid in sorted(set(property_ids)):
        summary = month_summary(facts, [pid], month)
        a, b = summary["actual"], summary["budget"]
        revpar, gop, labor = (variance(a, b, f) for f in ("revpar", "gop", "labor_expense"))
        warning_count = 0
        if warnings is not None and not warnings.empty:
            warning_count = int(((warnings["property_id"] == pid) & (warnings["month"] == month)).sum())
        data_status, data_note = _data_status(a, b, warning_count)
        statuses = {"revpar_status": _vs_budget_status(revpar, d.REVIEW_REVPAR_VS_BUDGET),
                    "gop_status": _vs_budget_status(gop, d.REVIEW_GOP_VS_BUDGET),
                    "labor_status": _labor_status(labor), "data_status": data_status}
        triggers = review_triggers(a, b)
        overall = "review" if triggers else max(statuses.values(), key=_SEVERITY.get)
        details = []   # plain-English reasons for any indicator that is not on plan
        if statuses["revpar_status"] in ("watch", "review"):
            details.append(f"RevPAR {abs(revpar['percent']) * 100:.1f}% below budget "
                           f"({money(revpar['actual'])} vs {money(revpar['budget'])})")
        if statuses["gop_status"] in ("watch", "review"):
            details.append(f"GOP {abs(gop['percent']) * 100:.1f}% below budget "
                           f"({money(gop['actual'], 0)} vs {money(gop['budget'], 0)})")
        if statuses["labor_status"] in ("watch", "review"):
            details.append(f"Labor {money_short(labor['difference'])} over budget ({labor['percent'] * 100:+.1f}%; "
                           f"{money(labor['actual'], 0)} vs {money(labor['budget'], 0)})")
        for field, v in triggers:
            if field not in ("revpar", "gop", "labor_expense"):
                details.append(f"{d.label(field)} {money_short(v['difference'])} over budget ({v['percent'] * 100:+.1f}%)")
        if data_status != "good":
            details.append({"No actuals": "No actual figures imported for this month",
                            "No budget": "No budget for this month, so variances can't be shown"}
                           .get(data_note, f"{data_note} from the data checks (see Data Imports & Checks)"))
        rows.append({
            "property_id": pid, "property": names.get(pid, pid),
            "revpar": a["revpar"] if a else None, "revpar_pct": revpar["percent"] if revpar else None,
            "gop": a["gop"] if a else None, "gop_pct": gop["percent"] if gop else None,
            "labor_diff": labor["difference"] if labor else None, "labor_pct": labor["percent"] if labor else None,
            "data_note": data_note, **statuses, "overall": overall,
            "reasons": ", ".join(trigger_reason(f) for f, _ in triggers), "details": details,
        })
    table = pd.DataFrame(rows)
    if table.empty:
        return table
    table["_rank"] = table["overall"].map(_SEVERITY)
    table = table.sort_values(["_rank", "property"], ascending=[False, True])
    return table.drop(columns="_rank").reset_index(drop=True)


# --- Operating profit bridge -----------------------------------------------------------------

BRIDGE_REVENUE_LINES = ["rooms_revenue", "other_revenue"]


def gop_bridge(actual: dict | None, budget: dict | None) -> dict | None:
    """
    How budgeted GOP becomes actual GOP, one line at a time:
        budget GOP + (each revenue line: actual - budget) - (each expense line: actual - budget) = actual GOP
    The steps always add up to the actual GOP. If the imported lines don't fully explain the change
    (a file whose subtotals don't reconcile), the gap is shown as its own step instead of being hidden.
    Returns None when there is no complete budget.
    """
    if not actual or not budget or actual.get("gop") is None or budget.get("gop") is None:
        return None

    def complete(fields):
        return all(actual.get(f) is not None and budget.get(f) is not None for f in fields)

    revenue_lines = BRIDGE_REVENUE_LINES if complete(BRIDGE_REVENUE_LINES) else ["total_revenue"]
    expense_lines = d.EXPENSE_LINES if complete(d.EXPENSE_LINES) else ["total_operating_expenses"]
    steps = [(f, actual[f] - budget[f]) for f in revenue_lines]
    steps += [(f, -(actual[f] - budget[f])) for f in expense_lines]     # a higher cost lowers profit
    change = actual["gop"] - budget["gop"]
    unexplained = change - sum(amount for _, amount in steps)
    if abs(unexplained) >= 0.01:
        steps.append(("unexplained", unexplained))
    else:
        unexplained = 0.0

    rows = [{"step": "Budget GOP", "field": "budget_gop", "amount": budget["gop"], "start": 0.0,
             "end": budget["gop"], "kind": "budget"}]
    running = budget["gop"]
    for field, amount in steps:
        kind = "unexplained" if field == "unexplained" else ("favorable" if amount >= 0 else "unfavorable")
        name = "Not explained by the lines" if field == "unexplained" else d.label(field)
        rows.append({"step": name, "field": field, "amount": amount, "start": running,
                     "end": running + amount, "kind": kind})
        running += amount
    rows.append({"step": "Actual GOP", "field": "actual_gop", "amount": actual["gop"], "start": 0.0,
                 "end": actual["gop"], "kind": "actual"})
    return {"steps": pd.DataFrame(rows), "budget_gop": budget["gop"], "actual_gop": actual["gop"],
            "change": change, "unexplained": unexplained, "end_of_steps": running,
            "revenue_change": sum(a for f, a in steps if f in revenue_lines),
            "expense_change": -sum(a for f, a in steps if f in expense_lines)}


# --- EBITDA waterfall ---------------------------------------------------------------------------

def ebitda_waterfall(actual: dict | None) -> dict | None:
    """
    Total Revenue, then each expense group as a downward step (source P&L order), to EBITDA:
        Total Revenue - operating expense groups = GOP - below-GOP groups = EBITDA
    GOP and EBITDA are shown as the figures SUPPLIED in the file. Where the steps don't arrive at a
    supplied figure, the gap becomes its own "difference" step, so no bar is ever silently adjusted.
    Returns None when total revenue is missing (nothing to start from).
    """
    if not actual or actual.get("total_revenue") is None:
        return None

    def complete(fields):
        return all(actual.get(f) is not None for f in fields)

    operating = d.WATERFALL_OPERATING_LINES if complete(d.WATERFALL_OPERATING_LINES) else ["total_operating_expenses"]
    below_gop = d.WATERFALL_BELOW_GOP_LINES if complete(d.WATERFALL_BELOW_GOP_LINES) else ["other_expenses"]
    rows = [{"step": "Total Revenue", "field": "total_revenue", "amount": actual["total_revenue"], "start": 0.0,
             "end": actual["total_revenue"], "kind": "total"}]
    running = actual["total_revenue"]
    checks = []            # (name, calculated from the steps, supplied in the file, difference)

    def add_steps(fields, kind):
        nonlocal running
        for field in fields:
            if actual.get(field) is None:
                return False
            rows.append({"step": d.label(field), "field": field, "amount": -actual[field], "start": running,
                         "end": running - actual[field], "kind": kind})
            running -= actual[field]
        return True

    def checkpoint(field, name):
        """A subtotal bar. If the file supplies the figure and it differs, show the gap as a step first."""
        nonlocal running
        supplied = actual.get(field)
        if supplied is None:
            checks.append((name, running, None, None))
            rows.append({"step": f"{name} (calculated)", "field": field, "amount": running, "start": 0.0,
                         "end": running, "kind": "total"})
            return
        difference = supplied - running
        checks.append((name, running, supplied, difference if abs(difference) > d.TOTAL_TOLERANCE else 0.0))
        if abs(difference) > d.TOTAL_TOLERANCE:
            rows.append({"step": f"Difference to reported {name}", "field": f"{field}_difference",
                         "amount": difference, "start": running, "end": supplied, "kind": "difference"})
        running = supplied
        rows.append({"step": name, "field": field, "amount": supplied, "start": 0.0, "end": supplied,
                     "kind": "total"})

    if not add_steps(operating, "operating"):
        return None
    checkpoint("gop", "GOP")
    if not add_steps(below_gop, "below_gop"):
        return None
    checkpoint("ebitda", "EBITDA")
    ebitda = checks[-1]
    return {"steps": pd.DataFrame(rows), "checks": checks,
            "reconciles": all(diff in (0.0, None) for *_, diff in checks) and ebitda[2] is not None,
            "ebitda_supplied": ebitda[2], "ebitda_calculated": ebitda[1],
            "detailed_below_gop": below_gop == d.WATERFALL_BELOW_GOP_LINES}


# --- Compact charts: revenue by source, largest expense variances ----------------------------

def revenue_by_source(summary: dict) -> pd.DataFrame:
    """Actual and budget for each revenue source the template supports (rooms and other revenue)."""
    rows = []
    for field in BRIDGE_REVENUE_LINES:
        a = summary["actual"].get(field) if summary["actual"] else None
        if a is None:
            continue
        v = variance(summary["actual"], summary["budget"], field)
        rows.append({"source": d.label(field), "actual": a, "budget": v["budget"] if v else None,
                     "difference": v["difference"] if v else None, "percent": v["percent"] if v else None})
    return pd.DataFrame(rows, columns=["source", "actual", "budget", "difference", "percent"])


def expense_variances(summary: dict, limit: int = 5) -> pd.DataFrame:
    """The operating expense lines with the largest variances vs budget (in dollars, either direction)."""
    rows = []
    for field in d.EXPENSE_LINES:
        v = variance(summary["actual"], summary["budget"], field)
        if v:
            rows.append({"line": d.label(field), "actual": v["actual"], "budget": v["budget"],
                         "difference": v["difference"], "percent": v["percent"],
                         "over_budget": v["difference"] > 0})
    table = pd.DataFrame(rows, columns=["line", "actual", "budget", "difference", "percent", "over_budget"])
    if table.empty:
        return table
    order = table["difference"].abs().sort_values(ascending=False).index
    return table.loc[order].head(limit).reset_index(drop=True)


DETAIL_FIELDS = ["rooms_revenue", "other_revenue", "total_revenue", *d.EXPENSE_LINES, "total_operating_expenses",
                 "gop", "other_expenses", "ebitda", "capital_reserve", "noi"]


def financial_variances(summary: dict, fields=None) -> pd.DataFrame:
    """Detailed actual vs budget table (every P&L line by default)."""
    rows = []
    for field in fields or DETAIL_FIELDS:
        actual_value = summary["actual"].get(field) if summary["actual"] else None
        v = variance(summary["actual"], summary["budget"], field)
        rows.append({
            "Line": d.label(field),
            "Actual": actual_value,
            "Budget": v["budget"] if v else None,
            "Variance": v["difference"] if v else None,
            "Variance %": v["percent"] if v else None,
            "Result": ("Favorable" if v["favorable"] else "Unfavorable") if v else "",
        })
    return pd.DataFrame(rows)
