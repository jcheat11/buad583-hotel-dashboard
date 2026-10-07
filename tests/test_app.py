"""
Smoke tests for the screens, run without a browser using Streamlit's AppTest.
They use the fictional demo workspace only.
"""
import unittest

from streamlit.testing.v1 import AppTest


def imports_screen(hosted_demo):
    import app
    app.MODE["hosted_demo"] = hosted_demo
    app.imports_page()


def other_screens():
    import app
    app.definitions_page()
    app.signals_page()


class AppTests(unittest.TestCase):
    def test_definitions_and_signals_pages_load(self):
        at = AppTest.from_function(other_screens, default_timeout=60).run()
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])

    def test_overview_loads_with_the_demo_figures(self):
        at = AppTest.from_file("../app.py", default_timeout=60).run()
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        labels = [m.label for m in at.metric]
        self.assertEqual(labels, ["Occupancy", "ADR", "RevPAR", "Net Operating Income (NOI)"])
        self.assertEqual(at.title[0].value, "Portfolio Overview")

    def test_overview_shows_the_torchfolio_logo(self):
        import app
        self.assertTrue(app.LOGO_FILE.exists())               # shipped in the repository's assets folder
        at = AppTest.from_file("../app.py", default_timeout=60)
        at.session_state["workspace"] = "demo"
        at.run()
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        self.assertEqual(len(at.get("image")), 1)
        self.assertEqual(at.title[0].value, "Portfolio Overview")

    def test_local_app_offers_file_upload(self):
        at = AppTest.from_function(imports_screen, args=(False,), default_timeout=60).run()
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        opens = [b.key for b in at.button if b.key.startswith("open_")]
        self.assertEqual(len(opens), 7)                       # actuals, combined, budget + 4 supporting sources
        self.assertEqual(len(at.get("file_uploader")), 0)     # the picker is inside the dialog
        next(b for b in at.button if b.key == "open_actuals").click().run()
        uploaders = at.get("file_uploader")
        self.assertEqual(len(uploaders), 1)
        self.assertIn(".xlsm", list(uploaders[0].proto.type))   # the local app reads the real package format

    def test_hosted_copy_uploads_only_after_confirming_dummy_data(self):
        at = AppTest.from_function(imports_screen, args=(True,), default_timeout=60).run()
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        self.assertEqual(len(at.get("file_uploader")), 0)
        self.assertEqual(len([b for b in at.button if b.key.startswith("sample_")]), 6)   # samples need no confirm
        self.assertEqual(len(at.get("download_button")) >= 1, True)    # the fictional files to edit and upload
        routes = [b.key[len("open_"):] for b in at.button if b.key.startswith("open_")]
        self.assertEqual(len(routes), 7)
        next(b for b in at.button if b.key == "open_actuals").click().run()
        self.assertEqual(len(at.get("file_uploader")), 0)    # no picker until the visitor confirms dummy data only
        next(c for c in at.checkbox if c.key == "hosted_dummy_ok").check().run()
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        for route in routes:                                  # confirmed once, every route then offers a picker
            next(b for b in at.button if b.key == f"open_{route}").click().run()
            uploaders = at.get("file_uploader")
            self.assertEqual(len(uploaders), 1, route)
            self.assertNotIn(".xlsm", list(uploaders[0].proto.type))   # the real monthly package format is refused

    def test_hosted_copy_rejects_rows_for_real_properties(self):
        from hotel_dashboard.demo import build_demo_store
        from hotel_dashboard.importer import read_monthly_workbook
        from tests.helpers import hotel_month, write_workbook
        import tempfile
        from pathlib import Path
        store = build_demo_store()                            # the hosted copy only knows the fictional hotels
        with tempfile.TemporaryDirectory() as folder:
            path = write_workbook(Path(folder) / "company.xlsx",
                                  {"Actual": [hotel_month("WJ01", "2026-06", 300, 270, 27_000.0)]})
            result = read_monthly_workbook(path, "company.xlsx", store.properties)
        self.assertIn("Unknown property", {i.check for i in result.errors})
        self.assertFalse(result.can_import)


if __name__ == "__main__":
    unittest.main()
