"""
Runs the real screens (without a browser) on the fictional demo workspace and checks the new panels:
honest "data needed" states before any source is imported, populated previews after the demo files
are imported, property/group switching, the detail views, and the source import tabs.
"""
import unittest

from streamlit.testing.v1 import AppTest

from hotel_dashboard import metrics, sources
from hotel_dashboard.demo import SAMPLE_DIR, SOURCE_DEMOS, build_demo_store
from hotel_dashboard.formatting import money
from tests.test_dashboard_filters import page_text

NEEDED = ["Channel data needed", "Pace data needed", "Debt data needed", "STR data needed"]


def demo_with_sources():
    store = build_demo_store()
    for name, file_name in SOURCE_DEMOS.items():
        store.add_source_import(name, sources.read_source_file(
            name, (SAMPLE_DIR / file_name).read_bytes(), file_name, store.properties))
    return store


def run_app(store, **state):
    at = AppTest.from_file("../app.py", default_timeout=120)
    at.session_state["workspace"] = "demo"
    at.session_state["demo_store"] = store
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def pace_screen():
    import app
    app.pace_page()


def debt_screen():
    import app
    app.debt_page()


def market_screen():
    import app
    app.market_page()


def imports_screen():
    import app
    app.imports_page()


def run_screen(function, store):
    at = AppTest.from_function(function, default_timeout=120)
    at.session_state["workspace"] = "demo"
    at.session_state["demo_store"] = store
    return at.run()


class NewPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.empty = build_demo_store()
        cls.full = demo_with_sources()

    def test_missing_sources_show_data_needed_states(self):
        at = run_app(self.empty)
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        text = page_text(at)
        for state in NEEDED:
            self.assertIn(state, text)
        self.assertNotIn("Capital Plan", text)

    def test_imported_sources_fill_the_previews(self):
        at = run_app(self.full)
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        text = page_text(at)
        for state in NEEDED:
            self.assertNotIn(state, text)
        self.assertIn("As of 1 Sep 2026", text)                             # pace labelled by its as-of date
        self.assertIn("need review: DSCR below minimum", text)
        self.assertIn("below fair share (RGI under 100)", text)
        self.assertIn("Definition pending", text)                           # SellAway overlap not assumed

    def test_waterfall_and_previews_follow_property_and_group(self):
        for state, ids in (({"view": "One property", "property_pick": "H05"}, ["H05"]),
                           ({"view": "Property group", "group_by": "brand", "group_pick": "LivAway Suites"},
                            ["H02", "H04", "H06"])):
            at = run_app(self.full, **state)
            self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
            ebitda = metrics.combine(self.full.facts, ids, "2026-07", "Actual")["ebitda"]
            self.assertIn(f"reported EBITDA {money(ebitda, 0)}", page_text(at))
        single = page_text(run_app(self.full, view="One property", property_pick="H05"))
        self.assertIn("Below the 1.35x minimum", single)                    # ratio, threshold and period
        self.assertIn("T12 Aug 2025 – Jul 2026", single)
        self.assertIn("RGI", single)

    def test_detail_views_render_empty_and_populated(self):
        for screen, needed in ((pace_screen, "Pace data needed"), (debt_screen, "Debt data needed"),
                               (market_screen, "STR data needed")):
            empty = run_screen(screen, self.empty)
            self.assertEqual(len(empty.exception), 0, [e.value for e in empty.exception])
            self.assertIn(needed, page_text(empty))
            full = run_screen(screen, self.full)
            self.assertEqual(len(full.exception), 0, [e.value for e in full.exception])
            self.assertNotIn(needed, page_text(full))
            self.assertGreaterEqual(len(full.dataframe), 1)
        debt = run_screen(debt_screen, self.full)
        self.assertEqual(len(debt.dataframe[0].value), 6)                   # one row per property

    def test_source_tab_imports_a_demo_file_separately(self):
        store = build_demo_store()
        at = run_screen(imports_screen, store)
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        next(b for b in at.button if b.key == "demo_pace").click().run()
        next(b for b in at.button if b.key == "import_pace").click().run()
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        self.assertEqual(len(store.sources["pace"]), 50)
        self.assertEqual(len(store.source_imports), 1)
        self.assertEqual(len(store.imports), 2)                             # monthly import log unchanged


if __name__ == "__main__":
    unittest.main()
