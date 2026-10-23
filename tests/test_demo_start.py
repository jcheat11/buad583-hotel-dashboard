"""
Starting the fictional demo with the sample data or empty (the welcome dialog), and Start over on
Data Imports & Checks. Fictional data only; the local company-data workspace is never offered this.
"""
import unittest

from streamlit.testing.v1 import AppTest

from hotel_dashboard.demo import PRELOADED, build_demo_store
from tests.test_dashboard_filters import page_text


def hosted_app():
    import app
    try:
        app.main(hosted_demo=True)
    finally:
        app.MODE["hosted_demo"] = False


def imports_screen():
    import app
    app.imports_page()


def buttons(at):
    return [b.key for b in at.button]


def click(at, key):
    next(b for b in at.button if b.key == key).click().run()
    assert not at.exception, [e.value for e in at.exception]


class WelcomeDialogTests(unittest.TestCase):
    def test_first_visit_asks_and_start_empty_clears_everything(self):
        for at in (AppTest.from_file("../app.py", default_timeout=120), AppTest.from_function(hosted_app,
                                                                                              default_timeout=120)):
            at.run()
            self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
            self.assertIn("start_empty", buttons(at))                    # asked on entry
            self.assertIn("start_sample", buttons(at))
            click(at, "start_empty")
            store = at.session_state["demo_store"]
            self.assertTrue(store.facts.empty and store.imports.empty and store.preloaded == [])
            self.assertNotIn("start_empty", buttons(at))                 # asked once per session
            self.assertIn("No monthly actuals have been imported yet", page_text(at))
            at.run()
            self.assertNotIn("start_empty", buttons(at))

    def test_keep_sample_keeps_the_preloaded_data(self):
        at = AppTest.from_file("../app.py", default_timeout=120).run()
        click(at, "start_sample")
        store = at.session_state["demo_store"]
        self.assertEqual(list(store.imports["file_name"]), PRELOADED)
        self.assertNotIn("start_sample", buttons(at))
        self.assertIn("Occupancy", [m.label for m in at.metric])

    def test_not_asked_in_the_local_company_workspace(self):
        at = AppTest.from_file("../app.py", default_timeout=120)
        at.session_state["workspace"] = "local"
        at.run()
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        self.assertNotIn("start_empty", buttons(at))


class StartOverTests(unittest.TestCase):
    def open(self, store):
        at = AppTest.from_function(imports_screen, default_timeout=120)
        at.session_state["workspace"] = "demo"
        at.session_state["demo_store"] = store
        return at.run()

    def test_clear_all_data_then_restore_sample(self):
        at = self.open(build_demo_store())
        self.assertIn("2026 budget: 6 of 6 hotels, preloaded", page_text(at))
        click(at, "clear_demo")
        store = at.session_state["demo_store"]
        self.assertTrue(store.facts.empty)
        text = page_text(at) + " ".join(s.value for s in at.success)
        self.assertIn("All data cleared", text)
        self.assertIn("No actuals loaded", text)                          # tiles say so honestly
        self.assertIn("2026 budget: 0 of 6 hotels.", text)
        self.assertNotIn("preloaded", text)
        click(at, "sample_budget")                                         # import the 2026 budget yourself
        at.radio(key="dialog_sample_budget").set_value("2026 portfolio budget (already loaded)").run()
        self.assertNotIn("already loaded. Importing would replace", page_text(at))   # nothing to replace now
        click(at, "import_budget")
        self.assertIn("2026 budget loaded", page_text(at))                # loaded, not "preloaded"
        click(at, "restore_demo")
        self.assertEqual(list(at.session_state["demo_store"].imports["file_name"]), PRELOADED)
        self.assertIn("Restored the fictional sample data", " ".join(s.value for s in at.success))

    def test_start_over_discards_an_ai_draft(self):
        at = AppTest.from_function(imports_screen, default_timeout=120)
        at.session_state["workspace"] = "demo"
        at.session_state["demo_store"] = build_demo_store()
        at.session_state["ai_draft"] = "a draft"
        at.run()
        click(at, "clear_demo")
        self.assertNotIn("ai_draft", at.session_state)


if __name__ == "__main__":
    unittest.main()
