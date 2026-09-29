"""Tests for reading Excel files and the data checks (missing fields, duplicates, reconciliation)."""
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from hotel_dashboard.importer import read_monthly_workbook
from hotel_dashboard.store import DataStore, DuplicateMonthError
from tests.helpers import ACTUAL_JUNE, PROPERTIES, hotel_month, write_workbook

TODAY = date(2026, 9, 28)


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def check(self, sheets, today=TODAY):
        path = write_workbook(self.folder / "test.xlsx", sheets)
        return read_monthly_workbook(path, "test.xlsx", PROPERTIES, today)

    def checks_found(self, result, severity):
        return {i.check for i in result.issues if i.severity == severity}

    def test_clean_file_imports(self):
        rows = [dict(ACTUAL_JUNE[0], reported_occupancy=90, reported_adr=100.0),   # 90 means 90%
                dict(ACTUAL_JUNE[1], month=datetime(2026, 6, 1), reported_occupancy=0.5)]
        result = self.check({"Actual": rows})
        self.assertEqual(result.errors, [])
        self.assertEqual({i.check for i in result.warnings}, {"Missing field"})    # optional columns absent
        self.assertTrue(result.can_import)
        self.assertEqual(sorted(result.rows["month"]), ["2026-06", "2026-06"])
        self.assertAlmostEqual(result.rows["reported_occupancy"].iloc[0], 0.90)

    def test_missing_required_column_is_an_error(self):
        rows = [{k: v for k, v in r.items() if k != "rooms_sold"} for r in ACTUAL_JUNE]
        result = self.check({"Actual": rows})
        self.assertIn("Missing field", self.checks_found(result, "error"))
        self.assertFalse(result.can_import)

    def test_blank_required_value_is_an_error(self):
        result = self.check({"Actual": [dict(ACTUAL_JUNE[0], rooms_sold=None), ACTUAL_JUNE[1]]})
        self.assertIn("Missing field", self.checks_found(result, "error"))

    def test_same_hotel_and_month_twice_is_a_duplicate_month(self):
        result = self.check({"Actual": ACTUAL_JUNE + [ACTUAL_JUNE[0]]})
        self.assertIn("Duplicate month", self.checks_found(result, "error"))

    def test_subtotal_rows_are_rejected(self):
        result = self.check({"Actual": ACTUAL_JUNE + [dict(ACTUAL_JUNE[0], property_id="Portfolio Total")]})
        self.assertIn("Subtotal row", self.checks_found(result, "error"))

    def test_unknown_property_is_rejected(self):
        result = self.check({"Actual": [dict(ACTUAL_JUNE[0], property_id="Z")]})
        self.assertIn("Unknown property", self.checks_found(result, "error"))

    def test_rooms_sold_above_available_is_an_error(self):
        result = self.check({"Actual": [dict(ACTUAL_JUNE[0], rooms_sold=301)]})
        self.assertIn("Does not reconcile", self.checks_found(result, "error"))

    def test_reported_adr_that_does_not_match_is_a_warning(self):
        result = self.check({"Actual": [dict(ACTUAL_JUNE[0], reported_adr=103.00)]})   # true ADR is $100
        self.assertIn("Does not reconcile", self.checks_found(result, "warning"))
        self.assertTrue(result.can_import)

    def test_totals_that_do_not_add_up_are_a_warning(self):
        result = self.check({"Actual": [dict(ACTUAL_JUNE[0], other_revenue=500.0)]})   # total stays 27,000
        messages = [i.message for i in result.warnings if i.check == "Does not reconcile"]
        self.assertTrue(any("Total Revenue" in m for m in messages))

    def test_more_rooms_than_keys_times_days_is_a_warning(self):
        result = self.check({"Actual": [hotel_month("A", "2026-06", 310, 270, 27_000.0)]})   # 10 keys x 30 = 300
        self.assertIn("Does not reconcile", self.checks_found(result, "warning"))

    def test_actuals_for_an_open_month_are_flagged(self):
        result = self.check({"Actual": ACTUAL_JUNE}, today=date(2026, 6, 15))
        self.assertIn("Month not closed", self.checks_found(result, "warning"))

    def test_daily_file_is_kept_out_of_monthly_results(self):
        result = self.check({"Signals": [{"as_of_date": "2026-09-27", "property_id": "A", "rooms_sold": 9}]})
        self.assertIn("Wrong file type", self.checks_found(result, "error"))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def result(self, rows, name="june.xlsx"):
        path = write_workbook(self.folder / name, {"Actual": rows})
        return read_monthly_workbook(path, name, PROPERTIES, TODAY)

    def test_importing_a_month_twice_needs_confirmation(self):
        store = DataStore(PROPERTIES)
        store.add_import(self.result(ACTUAL_JUNE))
        corrected = [dict(ACTUAL_JUNE[0], rooms_sold=260, rooms_revenue=26_000.0, total_revenue=26_000.0,
                          gop=26_000.0 - ACTUAL_JUNE[0]["labor_expense"])]
        with self.assertRaises(DuplicateMonthError):
            store.add_import(self.result(corrected, "june_fixed.xlsx"))
        store.add_import(self.result(corrected, "june_fixed.xlsx"), replace=True)
        june_a = store.facts[(store.facts["property_id"] == "A") & (store.facts["month"] == "2026-06")]
        self.assertEqual(len(june_a), 1)                       # replaced, not added
        self.assertEqual(june_a["rooms_sold"].iloc[0], 260)
        self.assertEqual(store.imports["replaced_rows"].iloc[-1], 1)

    def test_file_with_errors_is_never_stored(self):
        store = DataStore(PROPERTIES)
        with self.assertRaises(ValueError):
            store.add_import(self.result([dict(ACTUAL_JUNE[0], rooms_sold=None)]))
        self.assertTrue(store.facts.empty)

    def test_saved_data_and_import_date_survive_a_restart(self):
        data_folder = self.folder / "local_data"
        store = DataStore(PROPERTIES, data_folder)
        store.add_import(self.result(ACTUAL_JUNE), imported_at=datetime(2026, 7, 10, 9, 30))
        reopened = DataStore(PROPERTIES, data_folder)
        self.assertEqual(len(reopened.facts), 2)
        self.assertEqual(reopened.imports["imported_at"].iloc[0], "2026-07-10T09:30:00")

    def test_warnings_clear_when_the_flagged_figures_are_replaced(self):
        store = DataStore(PROPERTIES)
        store.add_import(self.result([dict(ACTUAL_JUNE[0], reported_adr=103.00)]))
        self.assertEqual(len(store.active_warnings().query("check == 'Does not reconcile'")), 1)
        store.add_import(self.result([ACTUAL_JUNE[0]], "fixed.xlsx"), replace=True)
        self.assertEqual(len(store.active_warnings().query("check == 'Does not reconcile'")), 0)


if __name__ == "__main__":
    unittest.main()
