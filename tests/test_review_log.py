"""
Review Log (Calculation & Evidence Log): the current-view trace, provenance and the two approvals.
All data is fictional; the AI model is always MOCKED (nothing is sent to Google).
"""
import unittest
from unittest import mock

import pandas as pd
from streamlit.testing.v1 import AppTest

from hotel_dashboard import metrics, trace
from hotel_dashboard.demo import SAMPLE_DIR, build_demo_store
from hotel_dashboard.formatting import money, pct
from hotel_dashboard.importer import read_monthly_workbook
from tests.helpers import PROPERTIES, facts_table, hotel_month
from tests.test_ai_briefing import POST, echo_model, page_text

JULY = "2026-07"


def two_pages():
    """Portfolio Overview or Review Log in ONE session, as when a visitor clicks between the tabs."""
    import streamlit as st
    import app
    app.MODE["hosted_demo"] = bool(st.session_state.get("_hosted"))
    try:
        (app.review_page if st.session_state.get("_page") == "review" else app.overview_page)()
    finally:
        app.MODE["hosted_demo"] = False


def session(store=None, page="review", hosted=False, key=None, **state):
    at = AppTest.from_function(two_pages, default_timeout=120)
    at.session_state["workspace"] = "demo"
    at.session_state["demo_store"] = store or build_demo_store()
    at.session_state["_page"] = page
    at.session_state["_hosted"] = hosted
    if key:
        at.secrets["GEMINI_API_KEY"] = key
    for name, value in state.items():
        at.session_state[name] = value
    return at


def go(at, page):
    at.session_state["_page"] = page
    return at.run()


def approve(at, prefix, button, name="Test Reviewer", confirm=True):
    next(t for t in at.text_input if t.key == f"{prefix}_reviewer").input(name)
    if confirm:
        next(c for c in at.checkbox if c.key == f"{prefix}_confirmed").check()
    next(b for b in at.button if b.label == button).click().run()


class TraceTests(unittest.TestCase):
    def test_results_come_from_metrics_and_missing_is_not_zero(self):
        a = hotel_month("A", "2026-06", 300, 270, 27_000.0)
        b = hotel_month("B", "2026-06", 900, 450, 90_000.0, gop=None)        # B didn't supply GOP
        t = trace.calculation_trace(facts_table([a, b]), PROPERTIES, ["A", "B"], "2026-06")
        rows = {r["key"]: r for r in t["rows"]}
        self.assertEqual(rows["occupancy"]["Actual (inputs → result)"], "720 ÷ 1,200 = 60.0%")   # not 70%
        self.assertEqual(rows["adr"]["Actual (inputs → result)"], "$117,000 ÷ 720 = $162.50")
        self.assertIn("missing", rows["gop"]["Actual (inputs → result)"])
        self.assertIn("GOP", rows["gop"]["Missing inputs"])
        self.assertIn("not zero", rows["gop"]["Missing inputs"])
        self.assertIn("no budget comparison", rows["occupancy"]["Actual − budget"])
        self.assertEqual(t["budget_note"], "No budget imported for this month.")

    def test_partial_budget_is_named_not_summed(self):
        facts = facts_table([hotel_month("A", "2026-06", 300, 270, 27_000.0),
                             hotel_month("B", "2026-06", 900, 450, 90_000.0)],
                            [hotel_month("A", "2026-06", 300, 255, 25_500.0)])
        t = trace.calculation_trace(facts, PROPERTIES, ["A", "B"], "2026-06")
        self.assertIn("No budget for Beta Hotel", t["budget_note"])
        self.assertTrue(all(r["Budget (inputs → result)"] == "missing" for r in t["rows"]))

    def test_provenance_names_the_import_for_each_hotel_month(self):
        store = build_demo_store()
        store.add_import(read_monthly_workbook(SAMPLE_DIR / "monthly_2026-08.xlsx", None, store.properties))
        p = trace.provenance(store, ["H01", "H03"], "2026-08")
        self.assertEqual(set(p["files"]["File"]), {"monthly_2026-08.xlsx", "budget_2026.xlsx"})
        self.assertEqual(list(p["coverage"]["Actual"]), ["monthly_2026-08.xlsx (import #3)"] * 2)
        july = trace.provenance(store, ["H01"], JULY)
        self.assertEqual(list(july["files"]["File"]), ["actuals_2026-01_to_2026-07.xlsx", "budget_2026.xlsx"])

    def test_missing_hotel_month_is_reported_not_invented(self):
        store = build_demo_store()
        store.facts = store.facts[~((store.facts["property_id"] == "H03") & (store.facts["month"] == JULY)
                                    & (store.facts["scenario"] == "Actual"))]
        p = trace.provenance(store, ["H03"], JULY)
        self.assertEqual(p["coverage"]["Actual"].iloc[0], "No actual")
        hotel = trace.inputs_by_hotel(store.facts, store.properties, ["H03"], JULY)
        self.assertEqual(list(hotel["scenario"]), ["Budget"])

    def test_untraceable_limits_are_stated(self):
        text = " ".join(trace.UNTRACEABLE)
        for phrase in ("Excel row numbers", "original files", "not a permanent audit trail"):
            self.assertIn(phrase, text)


class ReviewPageTests(unittest.TestCase):
    def test_sections_follow_the_selected_scope_and_month(self):
        store = build_demo_store()
        for state, ids, month in (({}, list(store.properties["property_id"]), JULY),
                                  ({"view": "One property", "property_pick": "H05"}, ["H05"], JULY),
                                  ({"view": "Property group", "group_by": "brand", "group_pick": "LivAway Suites"},
                                   ["H02", "H04", "H06"], JULY),
                                  ({"month_pick": "2026-05"}, list(store.properties["property_id"]), "2026-05")):
            at = session(store, **state).run()
            self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
            self.assertEqual(at.title[0].value, "Calculation & Evidence Log")
            labels = [e.label for e in at.expander]
            self.assertTrue(labels[0].startswith("**1. How the numbers were calculated**"))
            self.assertTrue(labels[1].startswith("**2. Sources and data checks**"))
            self.assertTrue(labels[2].startswith("**3. Briefing evidence and review**"))
            a = metrics.combine(store.facts, ids, month, "Actual")
            table = at.dataframe[0].value
            revpar = table.loc[table["Result"] == "RevPAR", "Actual (inputs → result)"].iloc[0]
            self.assertTrue(revpar.endswith(money(a["revpar"])), revpar)
            occupancy = table.loc[table["Result"] == "Occupancy", "Actual (inputs → result)"].iloc[0]
            self.assertTrue(occupancy.endswith(pct(a["occupancy"])))

    def test_local_mode_shows_no_ai_and_calls_nothing(self):
        with mock.patch(POST) as post:
            at = session().run()
        self.assertIn("never calls an outside AI model", page_text(at))
        self.assertFalse([b for b in at.button if b.label == "Draft with AI"])
        post.assert_not_called()

    def test_rule_based_approval_needs_name_and_confirmation_then_goes_stale(self):
        store = build_demo_store()
        at = session(store).run()
        approve(at, "rule", "Approve for sharing", confirm=False)
        self.assertIn("Enter your name and tick the box", " ".join(str(e.value) for e in at.error))
        self.assertFalse(at.get("download_button"))
        approve(at, "rule", "Approve for sharing")
        self.assertIn("Approved · Test Reviewer", page_text(at))
        self.assertEqual([b.label for b in at.get("download_button")], ["Download approved briefing (.md)"])
        go(at, "overview")                                              # the status shows on the overview
        self.assertIn("Approved · Test Reviewer", page_text(at))
        july_h01 = (store.facts["property_id"] == "H01") & (store.facts["month"] == JULY) \
            & (store.facts["scenario"] == "Actual")
        store.facts.loc[july_h01, "rooms_revenue"] += 5_000               # the figures change after approval
        go(at, "overview")
        self.assertNotIn("Approved · Test Reviewer", page_text(at))
        self.assertIn("Draft · asset manager review required", page_text(at))
        go(at, "review")
        self.assertFalse(at.get("download_button"))

    def test_ai_empty_state_does_not_call_the_model(self):
        with mock.patch(POST) as post:
            at = session(hosted=True, key="test-key").run()
        self.assertIn("No AI draft for this view", page_text(at))
        post.assert_not_called()

    def test_ai_draft_evidence_approval_and_invalidation_across_pages(self):
        store = build_demo_store()
        at = session(store, page="overview", hosted=True, key="test-key")
        with mock.patch(POST, side_effect=echo_model) as post:
            at.run()
            next(b for b in at.button if b.label == "Draft with AI").click().run()
            self.assertFalse([b for b in at.button if b.label == "Approve AI draft"])   # not on the overview
            go(at, "review")
            self.assertEqual(post.call_count, 1)                        # opening Review Log calls nothing
            text = page_text(at)
            self.assertIn("not to independent research by the model", text)
            self.assertIn("E1: RevPAR was $138.01", text)               # finding beside its cited evidence
            self.assertIn("Source: All properties, July 2026", text)    # ... and the evidence's own source
            self.assertIn("Possible cause (unverified)", text)
            self.assertIn("by gemini-3.5-flash-lite", text)
            approve(at, "ai", "Approve AI draft")
            self.assertIn("Approved AI draft · Test Reviewer", page_text(at))
            self.assertIn("Download approved AI draft (.md)", [b.label for b in at.get("download_button")])
            go(at, "overview")
            self.assertIn("Approved AI draft · Test Reviewer", page_text(at))
            store.add_import(read_monthly_workbook(SAMPLE_DIR / "monthly_2026-08.xlsx", None, store.properties))
            go(at, "review")                                            # new import: draft and approval gone
        text = page_text(at)
        self.assertIn("previous AI draft (and any approval of it) was discarded", text)
        self.assertNotIn("Approved AI draft", text)
        self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
