"""
Runs the real Portfolio Overview (without a browser) on the fictional demo data and checks that
changing the view updates every widget, that groups never double count a hotel, and that missing
data is shown clearly. The demo's latest imported month is July 2026.
"""
import unittest

from streamlit.testing.v1 import AppTest

from hotel_dashboard import metrics
from hotel_dashboard.demo import build_demo_store
from hotel_dashboard.formatting import money, pct

MONTH = "2026-07"


def run_app(store=None, **state):
    at = AppTest.from_file("../app.py", default_timeout=90)
    at.session_state["workspace"] = "demo"
    if store is not None:
        at.session_state["demo_store"] = store
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def page_text(at) -> str:
    """Everything written on the page as text (dollar signs un-escaped)."""
    parts = [e.value for kind in ("markdown", "caption", "info", "warning") for e in getattr(at, kind)]
    return "\n".join(str(p) for p in parts).replace("\\$", "$")


def map_rows(at):
    """The Properties Needing Attention table: first columns Property, Status."""
    for frame in at.dataframe:
        value = frame.value
        data = value.data if hasattr(value, "data") else value     # a pandas Styler or a DataFrame
        if list(data.columns[:2]) == ["Property", "Status"]:
            return data
    raise AssertionError("exception map not found")


class FilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = build_demo_store()

    def expect(self, ids):
        return metrics.combine(self.store.facts, ids, MONTH, "Actual")

    def check_widgets_match(self, at, ids, label):
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        totals = self.expect(ids)
        cards = [m.value for m in at.metric]
        self.assertEqual(cards[0], pct(totals["occupancy"]))                 # KPI cards
        self.assertEqual(cards[2], money(totals["revpar"]))
        text = page_text(at)
        self.assertIn(f"**{label}**", text)                                    # reporting line
        self.assertIn(f"reported EBITDA {money(totals['ebitda'], 0)}", text)   # EBITDA waterfall check line
        self.assertIn(f"{label}, July 2026, {len(ids)} hotel", text)           # briefing sources
        return text

    def test_all_properties(self):
        at = run_app()
        self.check_widgets_match(at, [f"H0{i}" for i in range(1, 7)], "All properties")
        self.assertEqual(len(map_rows(at)), 6)

    def test_one_property_updates_every_widget(self):
        at = run_app(view="One property", property_pick="H06")
        self.check_widgets_match(at, ["H06"], "Canyon Gate Suites")
        rows = map_rows(at)
        self.assertEqual(len(rows), 6)                                         # map still shows the portfolio
        self.assertIn("▶ Canyon Gate Suites", list(rows["Property"]))          # with the selection marked

    def test_group_counts_each_hotel_once(self):
        # Mesa Suites (H02) is in Legacy Assets, LivAway Suites AND Stabilized Properties.
        at = run_app(view="Property group")
        grouping = next(s for s in at.selectbox if s.label == "Grouping")
        grouping.set_value("asset_cohort").run()
        group = next(s for s in at.selectbox if s.label == "Group")
        group.set_value("Legacy Assets").run()
        self.check_widgets_match(at, ["H01", "H02"], "Asset Cohort: Legacy Assets")
        self.assertEqual(len(map_rows(at)), 2)
        legacy = self.expect(["H01", "H02"])
        h01, h02 = self.expect(["H01"]), self.expect(["H02"])
        self.assertEqual(legacy["rooms_sold"], h01["rooms_sold"] + h02["rooms_sold"])

    def test_missing_actuals_are_shown_clearly(self):
        store = build_demo_store()
        store.facts = store.facts[~((store.facts["property_id"] == "H03") & (store.facts["month"] == MONTH)
                                    & (store.facts["scenario"] == "Actual"))]
        at = run_app(store)
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        text = page_text(at)
        self.assertIn("5 of 6 hotels reporting", text)
        self.assertIn("No actual figures for Parkside Inn", text)              # status bar detail
        self.assertIn("No July 2026 actuals for Parkside Inn", text)           # briefing decision card
        rows = map_rows(at)
        self.assertIn("✕ No actuals", list(rows["Data Quality"]))

    def test_missing_budget_is_flagged_and_the_waterfall_uses_actuals_only(self):
        store = build_demo_store()
        store.facts = store.facts[~((store.facts["property_id"] == "H01") & (store.facts["month"] == MONTH)
                                    & (store.facts["scenario"] == "Budget"))]
        at = run_app(store)
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        text = page_text(at)
        self.assertIn("Budget incomplete", text)
        self.assertIn("reported EBITDA", text)                                 # waterfall needs no budget
        self.assertNotIn("Budget EBITDA", text)                                # no partial budget comparison

    def test_approval_flow_unlocks_the_download(self):
        at = run_app()
        self.assertFalse([b for b in at.get("download_button")])               # locked while a draft
        next(t for t in at.text_input if t.label == "Reviewer name").input("Test Reviewer")
        next(c for c in at.checkbox if c.label.startswith("I have checked")).check()
        next(b for b in at.button if b.label == "Approve for sharing").click()
        at.run()
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        self.assertIn("Approved · Test Reviewer", page_text(at))
        self.assertEqual(len(at.get("download_button")), 1)

    def test_sources_not_imported_show_no_figures(self):
        text = page_text(run_app())
        self.assertEqual(text.count("data needed"), 4)                         # channel, pace, debt, STR


if __name__ == "__main__":
    unittest.main()
