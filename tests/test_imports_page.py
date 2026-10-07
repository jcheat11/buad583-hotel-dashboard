"""
Data Imports & Checks: the three upload routes (Actuals, Budget, combined), the status displays, budget
coverage and replacement preview, the supporting-source cards, and that the dashboard and Review Log
show the newly imported figures and their provenance. Fictional data only.
"""
import io
import unittest
from datetime import date

import pandas as pd
from streamlit.testing.v1 import AppTest

from hotel_dashboard import coverage, metrics
from hotel_dashboard.demo import SAMPLE_DIR, build_demo_store
from hotel_dashboard.formatting import money
from hotel_dashboard.importer import read_monthly_workbook
from hotel_dashboard.store import DuplicateMonthError
from tests.test_dashboard_filters import page_text as _page_text, run_review


def page_text(at) -> str:
    """Page text including success and error messages (the import confirmation, failed checks)."""
    return _page_text(at) + "\n" + "\n".join(str(e.value) for e in list(at.success) + list(at.error))


TODAY = date(2026, 10, 7)
AUGUST = "2026-08"


def read(file_name, scenarios=None, store=None):
    store = store or build_demo_store()
    return read_monthly_workbook(SAMPLE_DIR / file_name, file_name, store.properties, TODAY, scenarios)


def august_without(*property_ids) -> bytes:
    df = pd.read_excel(SAMPLE_DIR / "monthly_2026-08.xlsx", sheet_name="Actual")
    df = df[~df["Property ID"].isin(property_ids)]
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Actual", index=False)
    return buffer.getvalue()


class RouteTests(unittest.TestCase):
    def test_actual_only_file(self):
        result = read("monthly_2026-08.xlsx", ["Actual"])
        self.assertFalse(result.errors)
        self.assertEqual(set(result.rows["scenario"]), {"Actual"})
        self.assertEqual(result.ignored_sheets, [])

    def test_budget_only_file_through_each_route(self):
        self.assertEqual(set(read("budget_2026.xlsx", ["Budget"]).rows["scenario"]), {"Budget"})
        wrong = read("budget_2026.xlsx", ["Actual"])                      # Budget file in the Actuals upload
        self.assertEqual(wrong.errors[0].check, "Wrong upload")
        self.assertTrue(wrong.rows.empty)

    def test_combined_workbook_never_leaks_through_a_single_route(self):
        name = "combined_2026-08_actual_and_budget.xlsx"
        both = read(name)
        self.assertEqual(both.rows.groupby("scenario").size().to_dict(), {"Actual": 6, "Budget": 6})
        budget_only = read(name, ["Budget"])
        self.assertEqual(set(budget_only.rows["scenario"]), {"Budget"})      # Actuals NOT imported here
        self.assertEqual(budget_only.ignored_sheets, ["Actual"])
        actual_only = read(name, ["Actual"])
        self.assertEqual(set(actual_only.rows["scenario"]), {"Actual"})
        self.assertEqual(actual_only.ignored_sheets, ["Budget"])


class StatusTests(unittest.TestCase):
    def test_actuals_status_updates_after_import(self):
        store = build_demo_store()
        before = coverage.actuals_status(store, 2026, TODAY).set_index("month")
        self.assertEqual(before.loc[AUGUST, "Status"], "Not imported")
        self.assertEqual(before.loc["2026-07", "Status"], "Complete")
        self.assertEqual(before.loc["2026-11", "Status"], "Not closed yet")
        store.add_import(read_monthly_workbook(august_without("H03"), "august_partial.xlsx", store.properties,
                                               TODAY, ["Actual"]))
        after = coverage.actuals_status(store, 2026, TODAY).set_index("month")
        self.assertEqual(after.loc[AUGUST, "Status"], "Partial")
        self.assertEqual(after.loc[AUGUST, "Properties reported"], "5 of 6")
        self.assertEqual(after.loc[AUGUST, "Missing"], "Parkside Inn")
        self.assertEqual(after.loc[AUGUST, "Latest file"], "august_partial.xlsx")
        details = coverage.month_details(store, AUGUST)["hotels"].set_index("Property")
        self.assertEqual(details.loc["Parkside Inn", "Actuals"], "Missing")
        self.assertEqual(details.loc["Mesa Suites", "File"], "august_partial.xlsx")

    def test_open_warnings_are_counted_per_month(self):
        store = build_demo_store()
        df = pd.read_excel(SAMPLE_DIR / "monthly_2026-08.xlsx", sheet_name="Actual")
        df.loc[0, "Total Revenue"] += 1_250                              # does not reconcile: a warning
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Actual", index=False)
        result = read_monthly_workbook(buffer.getvalue(), "aug.xlsx", store.properties, TODAY, ["Actual"])
        self.assertTrue(result.warnings)
        store.add_import(result)
        status = coverage.actuals_status(store, 2026, TODAY).set_index("month")
        self.assertEqual(status.loc[AUGUST, "Open warnings"], len(result.warnings))

    def test_separate_nonoverlapping_budget_workbooks(self):
        store = build_demo_store()
        legacy = read("budget_2027_legacy_assets.xlsx", ["Budget"], store)
        others = read("budget_2027_other_properties.xlsx", ["Budget"], store)
        self.assertTrue(coverage.replacement_preview(store, legacy.rows).empty)
        store.add_import(legacy)
        grid = coverage.budget_coverage(store, 2027).set_index("Property")
        self.assertEqual(grid.loc["Harbor House Hotel", "Months"], "12 of 12")
        self.assertEqual(grid.loc["Parkside Inn", "Months"], "0 of 12")
        self.assertTrue(coverage.replacement_preview(store, others.rows).empty)     # no overlap: nothing replaced
        store.add_import(others)
        grid = coverage.budget_coverage(store, 2027)
        self.assertTrue((grid["Months"] == "12 of 12").all())
        self.assertEqual(set(grid["Latest file"]), {"budget_2027_legacy_assets.xlsx",
                                                    "budget_2027_other_properties.xlsx"})
        budgets = store.facts[store.facts["scenario"] == "Budget"]
        self.assertFalse(budgets.duplicated(["scenario", "property_id", "month"]).any())

    def test_overlapping_budget_lists_exact_replacements_and_needs_confirmation(self):
        store = build_demo_store()
        again = read("budget_2026.xlsx", ["Budget"], store)
        preview = coverage.replacement_preview(store, again.rows)
        self.assertEqual(len(preview), 72)                                       # 6 hotels x 12 months
        self.assertEqual(set(preview["Currently from"]), {"budget_2026.xlsx (import #1)"})
        with self.assertRaises(DuplicateMonthError):
            store.add_import(again)                                               # never silently
        rows_before = len(store.facts)
        store.add_import(again, replace=True)
        self.assertEqual(len(store.facts), rows_before)                          # replaced, not added
        latest = store.facts[(store.facts["scenario"] == "Budget") & (store.facts["month"] == "2026-03")]
        self.assertEqual(set(latest["import_id"]), {3})


def imports_screen():
    import app
    app.imports_page()


def imports_with_upload():
    """The page with Streamlit's file picker replaced by a stand-in returning st.session_state["test_upload"]."""
    from unittest import mock

    import streamlit as st

    import app

    class Chosen:
        def __init__(self, name, raw):
            self.name, self.raw = name, raw

        def getvalue(self):
            return self.raw

    def picker(*args, **kwargs):
        upload = st.session_state.get("test_upload")
        return Chosen(*upload) if upload else None

    with mock.patch.object(app.st, "file_uploader", picker):
        app.imports_page()


def open_page(store, screen=imports_screen, **state):
    at = AppTest.from_function(screen, default_timeout=120)
    at.session_state["workspace"] = "demo"
    at.session_state["demo_store"] = store
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def click(at, key):
    next(b for b in at.button if b.key == key).click().run()
    assert not at.exception, [e.value for e in at.exception]


def button(at, key):
    return next((b for b in at.button if b.key == key), None)


def dialog_open(at) -> bool:
    return button(at, "cancel_import") is not None


def tables(at):
    return [getattr(f.value, "data", f.value) for f in at.dataframe]          # a Styler or a DataFrame


def actuals_row(at, month_name):
    status = next(t for t in tables(at) if "Status" in t.columns and "Month" in t.columns)
    return status[status["Month"] == month_name].iloc[0]


class SampleTileTests(unittest.TestCase):
    def test_page_top_is_a_caption_and_six_sample_tiles(self):
        at = open_page(build_demo_store())
        text = page_text(at)
        self.assertIn("Class Demo — Fictional Data Only.", text)
        self.assertNotIn("Never upload company files here", text)
        self.assertEqual([b.key for b in at.button if b.key.startswith("sample_")],
                         [f"sample_{k}" for k in ("actuals", "budget", "pace", "debt", "str", "channel_mix")])
        self.assertIn("2026 budget: 6 of 6 hotels, preloaded", text)                 # honest preloaded status
        self.assertFalse(dialog_open(at))

    def test_each_tile_opens_a_preview_then_imports_and_updates_status(self):
        store = build_demo_store()
        at = open_page(store)
        expected = {"actuals": "monthly_2026-08.xlsx", "budget": "budget_2027_legacy_assets.xlsx",
                    "pace": "DEMO_pace_2026-09-01.xlsx", "debt": "DEMO_debt_service.xlsx",
                    "str": "DEMO_str_2026.xlsx", "channel_mix": "DEMO_channel_mix_2026.xlsx"}
        for kind, file_name in expected.items():
            imports = len(store.imports) + len(store.source_imports)
            click(at, f"sample_{kind}")
            text = page_text(at)
            self.assertIn(f"Preview (not imported yet): `{file_name}`", text)
            self.assertIn("All checks passed. Not imported yet", text)
            self.assertEqual(len(store.imports) + len(store.source_imports), imports)   # opening never imports
            click(at, f"import_{kind}")
            self.assertFalse(dialog_open(at), kind)                                    # closed after import
            self.assertEqual(len(store.imports) + len(store.source_imports), imports + 1)
            self.assertIn(f"Imported {file_name}", page_text(at))
        text = page_text(at)
        self.assertIn("August imported", text)
        self.assertIn("2027 budget: 2 of 6 hotels", text)                             # one of two 2027 files
        self.assertEqual(text.count(":green-badge[:material/check_circle: Loaded]"), 8)  # 4 tiles + 4 cards
        self.assertEqual(at.session_state["import_year"], 2027)                        # shows the budget year
        grid = next(t for t in tables(at) if "Missing months" in t.columns).set_index("Property")
        self.assertEqual(grid.loc["Harbor House Hotel", "Months"], "12 of 12")
        self.assertIn("Coverage:** 5 of 6 properties (missing: Canyon Gate Suites)", text)   # STR card
        self.assertIn("as of 1 Sep 2026", text)                                         # pace card
        history = "".join(t.to_string() for t in tables(at) if "source" in t.columns)
        for file_name in list(expected.values())[2:]:
            self.assertIn(file_name, history)                                           # supporting history

    def test_cancel_closes_without_importing(self):
        store = build_demo_store()
        at = open_page(store)
        click(at, "sample_actuals")
        self.assertTrue(dialog_open(at))
        click(at, "cancel_import")
        self.assertFalse(dialog_open(at))
        self.assertEqual(len(store.imports), 2)
        self.assertEqual(actuals_row(at, "August 2026")["Status"], "Not imported")

    def test_file_with_errors_cannot_be_imported(self):
        store = build_demo_store()
        at = open_page(store)
        click(at, "sample_actuals")
        at.radio(key="dialog_sample_actuals").set_value("August 2026 with deliberate errors").run()
        self.assertIn("Import is disabled until they are fixed", page_text(at))
        self.assertTrue(button(at, "import_actuals").disabled)
        self.assertEqual(len(store.imports), 2)

    def test_overlapping_budget_needs_confirmation(self):
        store = build_demo_store()
        at = open_page(store)
        click(at, "sample_budget")
        at.radio(key="dialog_sample_budget").set_value("2026 portfolio budget (already loaded)").run()
        self.assertIn("72 property-month(s) in this file are already loaded", page_text(at))
        self.assertTrue(button(at, "import_budget").disabled)                         # needs explicit confirmation
        at.checkbox(key="replace_budget").check().run()
        self.assertFalse(button(at, "import_budget").disabled)
        click(at, "import_budget")
        self.assertEqual(int(store.imports.iloc[-1]["replaced_rows"]), 72)
        self.assertFalse(dialog_open(at))

    def test_only_one_dialog_at_a_time(self):
        at = open_page(build_demo_store())
        click(at, "sample_actuals")
        click(at, "sample_debt")                                                       # replaces, never stacks
        self.assertEqual([b.key for b in at.button if b.key.startswith("import_")], ["import_debt"])


class UploadDialogTests(unittest.TestCase):
    def test_manual_upload_of_actuals_previews_then_imports(self):
        store = build_demo_store()
        at = open_page(store, imports_with_upload)
        click(at, "open_actuals")
        self.assertIn("Choosing a file only **previews** it", page_text(at))           # dialog open, no file yet
        self.assertIsNone(button(at, "import_actuals"))
        at.session_state["test_upload"] = ("august_partial.xlsx", august_without("H03"))
        at.run()
        self.assertIn("Preview (not imported yet): `august_partial.xlsx`", page_text(at))
        self.assertEqual(len(store.imports), 2)                                        # selecting never imports
        click(at, "import_actuals")
        self.assertFalse(dialog_open(at))
        august = actuals_row(at, "August 2026")
        self.assertEqual((august["Status"], august["Properties reported"]), ("Partial", "5 of 6"))
        self.assertEqual(august["Latest file"], "august_partial.xlsx")

    def test_manual_upload_through_the_wrong_route_is_refused(self):
        store = build_demo_store()
        at = open_page(store, imports_with_upload, test_upload=(
            "budget_2026.xlsx", (SAMPLE_DIR / "budget_2026.xlsx").read_bytes()))
        click(at, "open_actuals")
        self.assertIn("Wrong upload", "".join(t.to_string() for t in tables(at)))
        self.assertTrue(button(at, "import_actuals").disabled)

    def test_combined_route_says_both_sheets_will_be_imported(self):
        store = build_demo_store()
        name = "combined_2026-08_actual_and_budget.xlsx"
        at = open_page(store, imports_with_upload, test_upload=(name, (SAMPLE_DIR / name).read_bytes()))
        click(at, "open_combined")
        text = page_text(at)
        self.assertIn("Both sheets found in this file will be imported", text)
        self.assertIn("6 property-month(s) in this file are already loaded", text)   # the August budget rows
        at.checkbox(key="replace_combined").check().run()
        click(at, "import_combined")
        self.assertEqual(store.imports.iloc[-1]["scenarios"], "Actual, Budget")


class ProvenanceTests(unittest.TestCase):
    def test_actuals_sample_shows_on_dashboard_and_review_log(self):
        store = build_demo_store()
        at = open_page(store)
        click(at, "sample_actuals")
        click(at, "import_actuals")
        self.assertIn("passed validation and now shows in the status", page_text(at))
        august = actuals_row(at, "August 2026")
        self.assertEqual((august["Status"], august["Latest file"]), ("Complete", "monthly_2026-08.xlsx"))
        review = run_review(store, month_pick=AUGUST)                            # provenance on Review Log
        self.assertIn("monthly_2026-08.xlsx", review.dataframe[3].value.to_string()
                      + review.dataframe[4].value.to_string() + page_text(review))
        trace = review.dataframe[0].value
        revpar = trace.loc[trace["Result"] == "RevPAR", "Actual (inputs → result)"].iloc[0]
        a = metrics.combine(store.facts, list(store.properties["property_id"]), AUGUST, "Actual")
        self.assertTrue(revpar.endswith(money(a["revpar"])))

    def test_supporting_section_has_no_guest_reviews(self):
        text = page_text(open_page(build_demo_store()))
        self.assertNotIn("Guest Reviews", text)
        self.assertNotIn("Guest & Market Context", text)
        self.assertNotIn("Not yet supported", text)


if __name__ == "__main__":
    unittest.main()
