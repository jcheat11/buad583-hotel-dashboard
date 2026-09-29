"""
End-to-end checks on the fictional sample files: they import cleanly, the broken file is
caught, and the dashboard totals match an independent calculation straight from Excel.
"""
import unittest
from datetime import date

import pandas as pd

from hotel_dashboard import metrics
from hotel_dashboard.demo import SAMPLE_DIR, build_demo_store
from hotel_dashboard.importer import read_monthly_workbook
from hotel_dashboard.store import load_properties

TODAY = date(2026, 9, 28)


class SampleDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.properties, problems = load_properties(SAMPLE_DIR / "properties.csv")
        assert problems == [], problems

    def test_clean_sample_files_pass_every_check(self):
        for name in ["budget_2026.xlsx", "actuals_2026-01_to_2026-07.xlsx", "monthly_2026-08.xlsx"]:
            result = read_monthly_workbook(SAMPLE_DIR / name, name, self.properties, TODAY)
            self.assertEqual(result.issues, [], name)

    def test_broken_sample_file_is_caught(self):
        result = read_monthly_workbook(SAMPLE_DIR / "monthly_2026-08_with_errors.xlsx", None, self.properties, TODAY)
        self.assertEqual({i.check for i in result.errors}, {"Missing field", "Duplicate month", "Subtotal row"})
        self.assertEqual({i.check for i in result.warnings}, {"Does not reconcile"})
        self.assertFalse(result.can_import)

    def test_dashboard_totals_match_an_independent_calculation(self):
        store = build_demo_store(TODAY)
        store.add_import(read_monthly_workbook(SAMPLE_DIR / "monthly_2026-08.xlsx", None, self.properties, TODAY))
        raw = pd.read_excel(SAMPLE_DIR / "monthly_2026-08.xlsx", sheet_name="Actual")
        expected_adr = raw["Rooms Revenue"].sum() / raw["Rooms Sold"].sum()
        expected_occupancy = raw["Rooms Sold"].sum() / raw["Rooms Available"].sum()

        ids = metrics.property_ids_in_scope(self.properties, metrics.Scope())
        totals = metrics.combine(store.facts, ids, "2026-08", "Actual")
        self.assertAlmostEqual(totals["adr"], expected_adr, places=6)
        self.assertAlmostEqual(totals["occupancy"], expected_occupancy, places=9)

    def test_groups_in_one_classification_add_up_to_the_portfolio(self):
        store = build_demo_store(TODAY)
        everyone = metrics.combine(store.facts, self.properties["property_id"], "2026-07", "Actual")
        for classification in ["brand", "asset_cohort", "stabilization_stage"]:
            parts = [metrics.combine(store.facts, ids, "2026-07", "Actual")
                     for _, ids in self.properties.groupby(classification)["property_id"]]
            self.assertAlmostEqual(sum(p["rooms_revenue"] for p in parts), everyone["rooms_revenue"], places=2)
            self.assertEqual(sum(p["rooms_sold"] for p in parts), everyone["rooms_sold"])


if __name__ == "__main__":
    unittest.main()
