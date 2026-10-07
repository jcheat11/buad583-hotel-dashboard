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
    """Page text including success messages (the import confirmation)."""
    return _page_text(at) + "\n" + "\n".join(str(e.value) for e in at.success)

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


def open_page(store, **state):
    at = AppTest.from_function(imports_screen, default_timeout=120)
    at.session_state["workspace"] = "demo"
    at.session_state["demo_store"] = store
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def click(at, key):
    next(b for b in at.button if b.key == key).click().run()


class PageTests(unittest.TestCase):
    def test_actuals_route_imports_and_updates_status_dashboard_and_review_log(self):
        store = build_demo_store()
        at = open_page(store)
        self.assertIn("Choosing a file only **previews** it", page_text(at))
        click(at, "sample_actuals_monthly_2026-08.xlsx")
        self.assertIn("Preview (not imported yet)", page_text(at))
        self.assertEqual(len(store.imports), 2)                                  # still not imported
        click(at, "import_actuals")
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        self.assertIn("passed validation and now shows in the status", page_text(at))
        tables = [getattr(f.value, "data", f.value) for f in at.dataframe]          # a Styler or a DataFrame
        status = next(t for t in tables if "Status" in t.columns and "Month" in t.columns)
        august = status[status["Month"] == "August 2026"].iloc[0]
        self.assertEqual((august["Status"], august["Latest file"]), ("Complete", "monthly_2026-08.xlsx"))
        review = run_review(store, month_pick=AUGUST)                            # provenance on Review Log
        self.assertIn("monthly_2026-08.xlsx", review.dataframe[3].value.to_string()
                      + review.dataframe[4].value.to_string() + page_text(review))
        trace = review.dataframe[0].value
        revpar = trace.loc[trace["Result"] == "RevPAR", "Actual (inputs → result)"].iloc[0]
        a = metrics.combine(store.facts, list(store.properties["property_id"]), AUGUST, "Actual")
        self.assertTrue(revpar.endswith(money(a["revpar"])))

    def test_budget_routes_separate_files_then_overlap_confirmation(self):
        store = build_demo_store()
        at = open_page(store, import_year=2027)
        for name in ("budget_2027_legacy_assets.xlsx", "budget_2027_other_properties.xlsx"):
            click(at, f"sample_budget_{name}")
            self.assertNotIn("already loaded. Importing would replace", page_text(at))
            click(at, "import_budget")
        grid = next(f.value for f in at.dataframe if "Missing months" in f.value.columns)
        self.assertTrue((grid["Months"] == "12 of 12").all())
        click(at, "sample_budget_budget_2026.xlsx")                              # overlaps the loaded 2026 budget
        self.assertIn("72 property-month(s) in this file are already loaded", page_text(at))
        button = next(b for b in at.button if b.key == "import_budget")
        self.assertTrue(button.disabled)                                          # needs explicit confirmation
        next(c for c in at.checkbox if c.key == "replace_budget").check().run()
        self.assertFalse(next(b for b in at.button if b.key == "import_budget").disabled)
        click(at, "import_budget")
        self.assertEqual(int(store.imports.iloc[-1]["replaced_rows"]), 72)

    def test_combined_route_says_both_sheets_will_be_imported(self):
        store = build_demo_store()
        at = open_page(store)
        click(at, "sample_combined_combined_2026-08_actual_and_budget.xlsx")
        text = page_text(at)
        self.assertIn("Both sheets found in this file will be imported", text)
        self.assertIn("6 property-month(s) in this file are already loaded", text)   # the August budget rows
        next(c for c in at.checkbox if c.key == "replace_combined").check().run()
        click(at, "import_combined")
        self.assertEqual(store.imports.iloc[-1]["scenarios"], "Actual, Budget")

    def test_each_supporting_card_imports_its_demo_file(self):
        store = build_demo_store()
        at = open_page(store)
        self.assertIn("Not yet supported", page_text(at))                         # Guest Reviews: honest state
        self.assertFalse([u for u in at.get("file_uploader") if "Review" in u.label])
        for name in ("pace", "debt", "str", "channel_mix"):
            click(at, f"demo_{name}")
            click(at, f"import_{name}")
            self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
            state = coverage.source_status(store, name)
            self.assertTrue(state["loaded"], name)
        self.assertEqual(page_text(at).count("Loaded"), 4)
        self.assertIn("as of 1 Sep 2026", page_text(at))                          # pace: latest as-of date
        self.assertIn("Coverage:** 5 of 6 properties (missing: Canyon Gate Suites)", page_text(at))   # STR


if __name__ == "__main__":
    unittest.main()
