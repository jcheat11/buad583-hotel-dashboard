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
        self.assertEqual(len(at.get("file_uploader")), 7)    # actuals, budget, combined + 4 supporting sources

    def test_hosted_copy_uploads_only_after_confirming_dummy_data(self):
        at = AppTest.from_function(imports_screen, args=(True,), default_timeout=60).run()
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        self.assertEqual(len(at.get("file_uploader")), 0)    # no upload until the visitor confirms dummy data only
        self.assertTrue(any("August" in b.label for b in at.button))   # sample imports still work
        self.assertEqual(len(at.get("download_button")) >= 1, True)    # the fictional files to edit and upload
        next(c for c in at.checkbox if c.key == "hosted_dummy_ok").check().run()
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        uploaders = at.get("file_uploader")
        self.assertEqual(len(uploaders), 7)                   # actuals, budget, combined + 4 supporting sources
        for uploader in uploaders:                            # the real monthly package format (.xlsm) is refused
            self.assertNotIn(".xlsm", list(uploader.proto.type))

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
