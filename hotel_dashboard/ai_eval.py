"""
Evaluation of the AI draft against the rule-based briefing, on four FICTIONAL cases:

  normal                July 2026 as preloaded in the demo
  adverse               August 2026 (a ramp-up hotel far behind plan, a labor overspend)
  missing data          August 2026 with one hotel's actuals left out of the file
  reconciliation error  August 2026 with one hotel's EBITDA not matching its own lines

For each case the same evidence packet goes to the model, the reply is validated, and scored:
  numerical accuracy    numbers in the raw reply that appear in the evidence they cite
  unsupported claims    items removed by validation (bad evidence ID or unsupported number)
  decision coverage     share of the decision evidence (rule statements, flags, data problems) the AI's
                        "What Needs a Decision" section cites; the rule-based briefing covers all of it
  failures              errors (missing key, quota, model failure, unreadable reply)
Only used in the hosted fictional demo (it calls the model) and in the tests (with a mocked model).
"""
from __future__ import annotations

import io
from datetime import date

import pandas as pd

from . import briefing, metrics
from .ai_briefing import AIError, CAUSE_WORDS, FACT, packet_fingerprint, evidence_packet, validate
from .demo import SAMPLE_DIR, build_demo_store
from .importer import read_monthly_workbook

TODAY = date(2026, 9, 15)
DECISION_KINDS = {FACT, "exception_flag", "reconciliation", "data_check"}


def _august(change=None):
    """The fictional August close, optionally altered, imported through the normal checks."""
    store = build_demo_store(today=TODAY)
    df = pd.read_excel(SAMPLE_DIR / "monthly_2026-08.xlsx", sheet_name="Actual")
    if change:
        df = change(df)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Actual", index=False)
    result = read_monthly_workbook(buffer.getvalue(), "case.xlsx", store.properties, TODAY)
    store.add_import(result)
    return store


def _break_ebitda(df):
    df.loc[df["Property ID"] == "H01", ["EBITDA", "NOI"]] += 4_000     # EBITDA no longer = GOP - other expenses
    return df


def cases() -> list[dict]:
    return [
        {"name": "Normal month", "store": build_demo_store(today=TODAY), "month": "2026-07"},
        {"name": "Adverse month", "store": _august(), "month": "2026-08"},
        {"name": "Missing data", "store": _august(lambda df: df[df["Property ID"] != "H03"]), "month": "2026-08"},
        {"name": "Reconciliation error", "store": _august(_break_ebitda), "month": "2026-08"},
    ]


def packet_for(case: dict) -> dict:
    store = case["store"]
    ids = list(store.properties["property_id"])
    return evidence_packet(store.facts, store.properties, ids, "All properties", case["month"],
                           store.active_warnings(), ids)


def score(case: dict, packet: dict, draft=None, error: AIError | None = None) -> dict:
    required = {e["id"] for e in packet["evidence"]
                if e["section_hint"] == "decision" and e["kind"] in DECISION_KINDS}
    row = {"case": case["name"], "evidence items": len(packet["evidence"])}
    if error is not None:
        return {**row, "result": f"Failed: {error.code}", "numbers correct": "", "unsupported claims": "",
                "decision coverage": "", "possible causes labelled": "", "items kept": ""}
    cited = {e for item in draft.sections["decision"] for e in item["evidence"]}
    return {**row, "result": "Draft produced",
            "numbers correct": f"{draft.numbers_supported} of {draft.numbers_total}",
            "unsupported claims": len(draft.removed),
            "decision coverage": f"{len(cited & required)} of {len(required)}" if required else "nothing to decide",
            "possible causes labelled": sum(i["kind"] != FACT for s in draft.sections.values() for i in s),
            "hedged facts": sum(bool(CAUSE_WORDS.search(i["text"])) for s in draft.sections.values()
                                for i in s if i["kind"] == FACT),
            "items kept": sum(len(s) for s in draft.sections.values())}


def rule_based_baseline(case: dict) -> dict:
    store = case["store"]
    ids = list(store.properties["property_id"])
    statements = briefing.build_briefing(store.facts, store.properties, ids, "All properties", case["month"],
                                         store.active_warnings())
    return {"case": case["name"], "statements": len(statements),
            "decision statements": sum(s.section == "decision" for s in statements),
            "possible causes": sum(s.kind == briefing.POSSIBLE_CAUSE for s in statements)}


def run(call_model, model: str) -> list[dict]:
    """call_model(packet) -> raw JSON dict (or raises AIError). Returns one result per case."""
    results = []
    for case in cases():
        packet = packet_for(case)
        fp = packet_fingerprint(packet, case["name"])
        try:
            draft = validate(call_model(packet), packet, model, fp)
            results.append({"score": score(case, packet, draft), "draft": draft, "packet": packet,
                            "baseline": rule_based_baseline(case)})
        except AIError as error:
            results.append({"score": score(case, packet, error=error), "draft": None, "packet": packet,
                            "baseline": rule_based_baseline(case), "error": error.message})
    return results
