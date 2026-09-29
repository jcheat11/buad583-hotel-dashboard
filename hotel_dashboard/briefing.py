"""
The written management briefing, produced by fixed rules from the verified figures.
No AI service is used.

Statements are grouped into three cards:
  changed    What changed: the verified results against budget and the previous month
  matters    Why it matters: what drove the results (calculated), plus POSSIBLE causes.
             Possible causes are marked kind="possible_cause", written as questions, and
             nothing in this app verifies them; the asset manager has to investigate.
  decision   What needs a decision: hotels or lines that crossed a threshold in definitions.py,
             and missing or questionable data

Every statement carries a source: the exact figures and calculation behind it.
A briefing stays a draft until an asset manager approves it. Approval is tied to a
fingerprint of the exact statements, so if the figures change the briefing goes back to draft.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pandas as pd

from . import definitions as d
from . import metrics
from .formatting import count, money, money_short, month_label, pct

SECTIONS = {
    "changed": "What Changed",
    "matters": "Why It Matters",
    "decision": "What Needs a Decision",
}

FACT = "fact"
POSSIBLE_CAUSE = "possible_cause"


@dataclass
class Statement:
    section: str       # "changed", "matters" or "decision"
    text: str
    source: str        # the figures and calculation behind the statement
    kind: str = FACT   # "fact" (from verified figures) or "possible_cause" (unverified question)


def profit_source(totals: dict, field: str) -> str:
    """How a profit line is built, with the figures, e.g. 'NOI = GOP $X - other expenses $Y - ... = $Z.'"""
    if field == "noi" and None not in (totals.get("gop"), totals.get("other_expenses"),
                                       totals.get("capital_reserve")):
        return (f"NOI = GOP {money(totals['gop'], 0)} - other expenses {money(totals['other_expenses'], 0)} "
                f"- capital reserve {money(totals['capital_reserve'], 0)} = {money(totals['noi'], 0)}.")
    if field == "gop" and totals.get("total_operating_expenses") is not None:
        return (f"GOP = total revenue {money(totals['total_revenue'], 0)} - total operating expenses "
                f"{money(totals['total_operating_expenses'], 0)} = {money(totals['gop'], 0)}.")
    return f"{d.label(field)} as reported in the imported files: {money(totals.get(field), 0)}."


def _versus(difference: float, decimals: int, shown: str, budget_text: str) -> str:
    """'$5.00 above budget ($180.00)'; 'in line with budget ($180.00)' if the difference rounds to zero."""
    if round(difference, decimals) == 0:
        return f"in line with budget ({budget_text})"
    return f"{shown} {'above' if difference > 0 else 'below'} budget ({budget_text})"


def build_briefing(facts: pd.DataFrame, properties: pd.DataFrame, property_ids, scope_label: str,
                   month: str, warnings: pd.DataFrame | None = None) -> list[Statement]:
    summary = metrics.month_summary(facts, property_ids, month)
    actual, budget = summary["actual"], summary["budget"]
    period = month_label(month)
    if actual is None:
        return [Statement("decision", f"No actual figures have been imported for {scope_label} in {period}.",
                          "Import log")]

    hotels = len(actual["property_ids"])
    basis = f"{scope_label}, {period}, {hotels} hotel{'s' if hotels != 1 else ''}"
    names = properties.set_index("property_id")["property_name"]
    decisions, triggers = _decisions(facts, summary, month, names, warnings)
    return (_what_changed(facts, actual, budget, basis, month)
            + _why_it_matters(summary, basis, triggers)
            + decisions)


# --- What changed ----------------------------------------------------------------------

def _profit_statement(a: dict, b: dict | None, field: str, basis: str) -> Statement | None:
    name = d.label(field)
    if a.get(field) is None or not a.get("total_revenue"):
        return None
    margin = a[field] / a["total_revenue"]
    v = metrics.variance(a, b, field)
    source = f"{basis}. {profit_source(a, field)} Total revenue {money(a['total_revenue'], 0)}"
    if v and v["percent"] is not None:
        budget_margin = b[field] / b["total_revenue"] if b.get("total_revenue") else None
        text = (f"{name} was {money_short(a[field])}, "
                + _versus(v["percent"] * 100, 1, f"{abs(v['percent']) * 100:.1f}%", money_short(b[field]))
                + f"; margin {pct(margin)} vs {pct(budget_margin)} budgeted.")
        source += f". Budget {name} {money(b[field], 0)}; budget total revenue {money(b['total_revenue'], 0)}"
    else:
        text = f"{name} was {money_short(a[field])} (margin {pct(margin)}); no complete budget to compare."
    return Statement("changed", text, source + ".")


def _what_changed(facts, a: dict, b: dict | None, basis: str, month: str) -> list[Statement]:
    out = []
    revpar = metrics.variance(a, b, "revpar")
    source = (f"{basis}. RevPAR = rooms revenue / rooms available = {money(a['rooms_revenue'], 0)} / "
              f"{count(a['rooms_available'])}")
    if revpar:
        text = (f"RevPAR was {money(a['revpar'])}, "
                + _versus(revpar["difference"], 2, money(abs(revpar["difference"])), money(b["revpar"])) + ".")
        source += f". Budget: {money(b['rooms_revenue'], 0)} / {count(b['rooms_available'])}"
    else:
        text = f"RevPAR was {money(a['revpar'])} (no complete budget to compare)."
    out.append(Statement("changed", text, source + "."))

    for field in dict.fromkeys([d.BRIDGE_PROFIT_FIELD, d.HEADLINE_PROFIT_FIELD]):   # GOP, then NOI
        statement = _profit_statement(a, b, field, basis)
        if statement:
            out.append(statement)

    occ = metrics.variance(a, b, "occupancy")
    source = f"{basis}. Actual: {count(a['rooms_sold'])} rooms sold / {count(a['rooms_available'])} rooms available"
    if occ:
        source += f". Budget: {count(b['rooms_sold'])} / {count(b['rooms_available'])}"
        text = (f"Occupancy was {pct(a['occupancy'])}, "
                + _versus(occ["difference"] * 100, 1, f"{abs(occ['difference']) * 100:.1f} points",
                          pct(b["occupancy"])) + ".")
    else:
        text = f"Occupancy was {pct(a['occupancy'])}."
    out.append(Statement("changed", text, source + "."))

    if a["adr"] is not None:
        adr = metrics.variance(a, b, "adr")
        source = f"{basis}. Actual: {money(a['rooms_revenue'], 0)} rooms revenue / {count(a['rooms_sold'])} rooms sold"
        if adr:
            source += f". Budget: {money(b['rooms_revenue'], 0)} / {count(b['rooms_sold'])}"
            text = f"ADR was {money(a['adr'])}, " + _versus(adr["difference"], 2, money(abs(adr["difference"])),
                                                            money(b["adr"])) + "."
        else:
            text = f"ADR was {money(a['adr'])}."
        out.append(Statement("changed", text, source + "."))

    previous_month = metrics.shift_month(month, -1)
    previous = metrics.combine(facts, a["property_ids"], previous_month, "Actual")
    if previous and previous["property_ids"] == a["property_ids"] and previous["revpar"]:
        change = a["revpar"] / previous["revpar"] - 1
        hotels = len(a["property_ids"])
        out.append(Statement(
            "changed",
            f"RevPAR {'rose' if change > 0 else 'fell'} {abs(change) * 100:.1f}% from "
            f"{month_label(previous_month)} ({money(previous['revpar'])}).",
            f"Same {hotels} hotel{'s' if hotels != 1 else ''}, {month_label(previous_month)} actual: "
            f"{money(previous['rooms_revenue'], 0)} rooms revenue / {count(previous['rooms_available'])} "
            "rooms available."))
    return out


# --- Why it matters: calculated drivers, then possible causes -----------------------------

def _why_it_matters(summary: dict, basis: str, triggers: dict) -> list[Statement]:
    a, b = summary["actual"], summary["budget"]
    out = []
    effects = metrics.revpar_bridge(a, b)
    if effects:
        occ, rate = effects["occupancy_effect"], effects["rate_effect"]
        out.append(Statement(
            "matters",
            f"{'Higher' if occ >= 0 else 'Lower'} occupancy {'added' if occ >= 0 else 'took away'} "
            f"{money(abs(occ))} of RevPAR; {'higher' if rate >= 0 else 'lower'} ADR "
            f"{'added' if rate >= 0 else 'took away'} {money(abs(rate))}.",
            f"{basis}. Occupancy effect = ({pct(a['occupancy'])} - {pct(b['occupancy'])}) x budget ADR "
            f"{money(b['adr'])} = {money(occ)}. Rate effect = ({money(a['adr'])} - {money(b['adr'])}) x actual "
            f"occupancy {pct(a['occupancy'])} = {money(rate)}. The two effects add up to the RevPAR variance."))

    bridge = metrics.gop_bridge(a, b)
    if bridge:
        rev, cost, change = bridge["revenue_change"], bridge["expense_change"], bridge["change"]
        out.append(Statement(
            "matters",
            f"Revenue {money_short(abs(rev))} {'above' if rev >= 0 else 'below'} budget and operating costs "
            f"{money_short(abs(cost))} {'over' if cost > 0 else 'under'} budget "
            f"{'raised' if change >= 0 else 'lowered'} GOP by {money_short(abs(change))}.",
            f"{basis}. GOP bridge: budget GOP {money(bridge['budget_gop'], 0)} + revenue variance {money(rev, 0)} "
            f"- expense variance {money(cost, 0)}"
            + (f" + unexplained {money(bridge['unexplained'], 0)}" if bridge["unexplained"] else "")
            + f" = actual GOP {money(bridge['actual_gop'], 0)}."))
        largest = metrics.expense_variances(summary, limit=1)
        if not largest.empty and abs(largest["difference"].iloc[0]) >= 1:
            line = largest.iloc[0]
            out.append(Statement(
                "matters",
                f"The largest cost variance was {line['line']}: {money_short(abs(line['difference']))} "
                f"({abs(line['percent']) * 100:.1f}%) {'over' if line['over_budget'] else 'under'} budget.",
                f"{basis}. {line['line']}: actual {money(line['actual'], 0)}, budget {money(line['budget'], 0)}."))

    out += _possible_causes(a, b, triggers)
    return out


def _possible_causes(a: dict, b: dict | None, triggers: dict) -> list[Statement]:
    out = []
    occ = metrics.variance(a, b, "occupancy")
    adr = metrics.variance(a, b, "adr")
    if occ and adr:
        trigger = (f"Triggered by: occupancy {occ['difference'] * 100:+.1f} points and ADR "
                   f"{'+' if adr['difference'] >= 0 else '-'}{money(abs(adr['difference']))} vs budget. Not verified.")
        # (question, how to check it) for each combination of occupancy and ADR vs budget
        question, how_to_check = {
            (False, True): ("Did the higher rates cost occupancy?",
                            "Compare the month's pricing decisions with booking pace."),
            (False, False): ("Is demand softer than planned, across the market or only at these hotels?",
                             "Needs market data (e.g. STR), which is not in this app yet."),
            (True, False): ("Was occupancy gained through discounting, or a shift in channel or length-of-stay mix?",
                            "Review rate and channel reports for the month."),
            (True, True): ("Is the outperformance repeatable, or driven by a one-off event or group booking?",
                           "Review the month's group and event business."),
        }[(occ["difference"] >= 0, adr["difference"] >= 0)]
        out.append(Statement("matters", question, f"{trigger} To check: {how_to_check}", POSSIBLE_CAUSE))

    for field, hotel in triggers["expense_lines"]:
        if field == "labor_expense":
            out.append(Statement(
                "matters",
                f"At {hotel}: were labor schedules adjusted to occupancy, or were there one-off costs "
                "(overtime, contract labor, bonuses)?",
                f"Triggered by: Labor & Benefits over budget at {hotel}. Not verified.", POSSIBLE_CAUSE))

    out.append(Statement(
        "matters",
        "Were there one-off events (renovation, weather, local events, system changes) that monthly totals can't show?",
        "Standard question; not triggered by a specific figure.", POSSIBLE_CAUSE))
    return out


# --- What needs a decision --------------------------------------------------------------

def _decisions(facts, summary: dict, month: str, names, warnings) -> tuple[list[Statement], dict]:
    out, triggers = [], {"expense_lines": []}
    period = month_label(month)
    actual = summary["actual"]

    if summary["missing_actual"]:
        missing = ", ".join(names.get(p, p) for p in summary["missing_actual"])
        out.append(Statement(
            "decision",
            f"No {period} actuals for {missing}; totals exclude them. Decide whether to chase the figures "
            "or report without them.",
            "Import log: no Actual rows for these hotels and month."))
    if summary["budget_missing"]:
        missing = ", ".join(names.get(p, p) for p in summary["budget_missing"])
        out.append(Statement(
            "decision",
            f"No {period} budget for {missing}, so budget comparisons are hidden. Load the budget or "
            "report actuals only.",
            "Import log: no Budget rows for these hotels and month."))

    flagged = 0
    for pid in actual["property_ids"]:
        one = metrics.month_summary(facts, [pid], month)
        hotel = names.get(pid, pid)
        # The same rule that sets 'Review' in the exception map (metrics.review_triggers).
        for field, v in metrics.review_triggers(one["actual"], one["budget"]):
            flagged += 1
            if field in ("revpar", "gop"):
                name = "RevPAR" if field == "revpar" else "GOP"
                limit = d.REVIEW_REVPAR_VS_BUDGET if field == "revpar" else d.REVIEW_GOP_VS_BUDGET
                decimals = 2 if field == "revpar" else 0
                out.append(Statement(
                    "decision",
                    f"{hotel}: {name} {abs(v['percent']) * 100:.1f}% below budget "
                    f"({money(v['actual'], decimals)} vs {money(v['budget'], decimals)}). "
                    "Decide whether a recovery plan is needed.",
                    f"{hotel}, {period}. Threshold: {name} more than {abs(limit):.0%} below budget."))
            else:
                triggers["expense_lines"].append((field, hotel))
                out.append(Statement(
                    "decision",
                    f"{hotel}: {d.label(field)} {money_short(v['difference'])} ({v['percent'] * 100:.1f}%) "
                    "over budget. Decide whether to act or accept it as a one-off.",
                    f"{hotel}, {period}: actual {money(v['actual'], 0)}, budget {money(v['budget'], 0)}. "
                    f"Threshold: more than {d.ATTENTION_EXPENSE_OVER_BUDGET:.0%} and "
                    f"{money(d.ATTENTION_EXPENSE_MIN_DOLLARS, 0)} over budget."))

    if warnings is not None and not warnings.empty:
        relevant = warnings[(warnings["property_id"].isin(actual["property_ids"])) & (warnings["month"] == month)]
        if not relevant.empty:
            flagged += 1
            out.append(Statement(
                "decision",
                f"{len(relevant)} data-check warning(s) affect these figures. Resolve them before sharing "
                "(see Data Imports & Checks).",
                "Examples: " + "; ".join(relevant["message"].head(3))))

    if flagged == 0:
        out.append(Statement(
            "decision",
            "Nothing crossed the review thresholds this month.",
            f"Checked: RevPAR more than {abs(d.REVIEW_REVPAR_VS_BUDGET):.0%} below budget, GOP more than "
            f"{abs(d.REVIEW_GOP_VS_BUDGET):.0%} below budget, expense lines more than "
            f"{d.ATTENTION_EXPENSE_OVER_BUDGET:.0%} and {money(d.ATTENTION_EXPENSE_MIN_DOLLARS, 0)} over budget."))
    return out, triggers


# --- Card summaries: one headline and one sentence per card ----------------------------------

def _signed_short(value: float) -> str:
    return ("+" if value >= 0 else "-") + money_short(abs(value))


def card_summaries(facts: pd.DataFrame, properties: pd.DataFrame, property_ids, month: str,
                   warnings: pd.DataFrame | None = None) -> dict[str, tuple[str, str]]:
    """
    A short headline and one supporting sentence for each briefing card, from the same verified
    calculations as the full statements (which stay available as evidence).
    """
    summary = metrics.month_summary(facts, property_ids, month)
    a, b = summary["actual"], summary["budget"]
    if a is None:
        return {key: ("No actuals imported", f"Import {month_label(month)} figures to fill this card.")
                for key in SECTIONS}
    names = properties.set_index("property_id")["property_name"]
    cards = {}

    gop, revpar, noi = (metrics.variance(a, b, f) for f in ("gop", "revpar", "noi"))
    if gop and gop["percent"] is not None:
        share = abs(gop["percent"]) * 100
        headline = ("GOP in line with budget" if round(share, 1) == 0
                    else f"GOP {share:.1f}% {'above' if gop['percent'] > 0 else 'below'} budget")
        sentence = f"RevPAR {money(a['revpar'])} ({'+' if revpar['difference'] >= 0 else '-'}{money(abs(revpar['difference']))} vs budget)"
        if noi and noi["percent"] is not None:
            sentence += f"; NOI {money_short(a['noi'])} ({noi['percent'] * 100:+.1f}%)"
        cards["changed"] = (headline, sentence + ".")
    else:
        cards["changed"] = (f"RevPAR {money(a['revpar'])}", "No complete budget to compare against.")

    bridge = metrics.gop_bridge(a, b)
    if bridge:
        rev, cost, change = bridge["revenue_change"], bridge["expense_change"], bridge["change"]
        if rev < 0 and cost > 0:
            headline = "Lower revenue and higher costs"
        elif rev < 0:
            headline = "Revenue shortfall" if change < 0 else "Cost savings offset lower revenue"
        elif cost > 0:
            headline = "Cost overrun" if change < 0 else "Revenue growth covered higher costs"
        else:
            headline = "Revenue and costs both beat budget"
        sentence = f"Revenue {_signed_short(rev)} and costs {_signed_short(cost)} vs budget"
        largest = metrics.expense_variances(summary, limit=1)
        if not largest.empty:
            sentence += f"; largest cost variance: {largest['line'].iloc[0]} {_signed_short(largest['difference'].iloc[0])}"
        cards["matters"] = (headline, sentence + ".")
    else:
        cards["matters"] = ("Drivers need a complete budget", "Load the budget for every hotel in this view.")

    flagged = []
    for pid in a["property_ids"]:
        one = metrics.month_summary(facts, [pid], month)
        reasons = [metrics.trigger_reason(f) for f, _ in metrics.review_triggers(one["actual"], one["budget"])]
        if reasons:
            flagged.append(f"{names.get(pid, pid)} ({', '.join(reasons)})")
    gaps = [names.get(p, p) for p in summary["missing_actual"]]
    if flagged:
        headline = f"{len(flagged)} propert{'y' if len(flagged) == 1 else 'ies'} to review"
        sentence = "; ".join(flagged) + "."
        if gaps:
            sentence += f" Missing actuals: {', '.join(gaps)}."
    elif gaps or summary["budget_missing"]:
        headline = "Data gaps to resolve"
        sentence = (f"Missing actuals: {', '.join(gaps)}." if gaps else
                    f"Missing budget: {', '.join(names.get(p, p) for p in summary['budget_missing'])}.")
    else:
        headline = "No decisions needed"
        sentence = "No property crossed a review threshold this month."
    cards["decision"] = (headline, sentence)
    return cards


# --- Review and export ---------------------------------------------------------------------

def fingerprint(statements: list[Statement]) -> str:
    """A short code that changes whenever any statement or its source changes."""
    text = "\n".join(f"{s.section}|{s.kind}|{s.text}|{s.source}" for s in statements)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def to_markdown(statements: list[Statement], scope_label: str, month: str, data_note: str,
                reviewer: str | None = None, reviewed_at: str | None = None, notes: str = "",
                summaries: dict | None = None) -> str:
    status = (f"Reviewed and approved by {reviewer} on {reviewed_at}" if reviewer
              else "DRAFT - NOT REVIEWED - DO NOT SHARE")
    lines = [
        f"# Management Briefing: {scope_label}, {month_label(month)}",
        "",
        f"**Status:** {status}  ",
        f"**Figures:** {data_note}  ",
        "**Method:** generated by fixed rules from the imported figures; no AI service was used.",
    ]
    for key, title in SECTIONS.items():
        for kind, heading in ((FACT, title), (POSSIBLE_CAUSE, "Possible causes (not verified)")):
            items = [s for s in statements if s.section == key and s.kind == kind]
            if not items:
                continue
            lines += ["", f"## {heading}" if kind == FACT else f"### {heading}", ""]
            if kind == FACT and summaries and key in summaries:
                lines += [f"**{summaries[key][0]}.** {summaries[key][1]}", ""]
            for number, s in enumerate(items, start=1):
                lines += [f"{number}. {s.text}", f"   - *Source:* {s.source}"]
    if notes:
        lines += ["", "## Asset Manager Notes", "", notes]
    return "\n".join(lines) + "\n"
