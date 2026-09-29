"""
Checks for the EBITDA waterfall and the separate sources (channel mix, pace, STR, debt).
All data is fictional: the tiny hotels from helpers.py or the demo files in sample_data/.
"""
import io
import unittest
from unittest import mock

import pandas as pd

from hotel_dashboard import definitions as d
from hotel_dashboard import metrics, sources
from hotel_dashboard.demo import SAMPLE_DIR, SOURCE_DEMOS, build_demo_store
from hotel_dashboard.importer import read_monthly_workbook
from tests.helpers import PROPERTIES, facts_table, hotel_month, write_workbook

BELOW_GOP = {"insurance_expense": 900.0, "real_estate_tax": 700.0, "personal_property_tax": 150.0,
             "other_taxes": 100.0, "misc_other_expense": 50.0}          # = 1,900 other expenses


def with_ebitda(pid, month, available, sold, revenue, detail=True, **overrides):
    """A hotel-month whose GOP, other expenses and EBITDA all reconcile (unless overridden)."""
    row = hotel_month(pid, month, available, sold, revenue)
    row.update(other_expenses=1_900.0, ebitda=row["gop"] - 1_900.0, capital_reserve=0.0)
    if detail:
        row.update(BELOW_GOP)
    row.update(overrides)
    return row


def xlsx(rows: list[dict], headings: dict) -> bytes:
    buffer = io.BytesIO()
    pd.DataFrame([{headings.get(k, k): v for k, v in r.items()} for r in rows]).to_excel(buffer, index=False)
    return buffer.getvalue()


def source_file(source: str, rows: list[dict]) -> bytes:
    return xlsx(rows, {c.field: c.heading for c in sources.SOURCES[source].columns})


class WaterfallTests(unittest.TestCase):
    def waterfall(self, *rows, ids=("A",)):
        facts = facts_table(list(rows))
        return metrics.ebitda_waterfall(metrics.combine(facts, ids, "2026-06", "Actual"))

    def test_steps_run_from_total_revenue_to_reported_ebitda(self):
        w = self.waterfall(with_ebitda("A", "2026-06", 300, 270, 27_000.0))
        steps = w["steps"]
        self.assertEqual(steps["step"].iloc[0], "Total Revenue")
        self.assertEqual(steps["step"].iloc[-1], "EBITDA")
        self.assertEqual(list(steps.loc[steps["kind"] == "below_gop", "step"]),
                         [d.label(f) for f in d.OTHER_EXPENSE_DETAIL_FIELDS])     # source P&L groups
        self.assertTrue(w["reconciles"])
        self.assertAlmostEqual(steps["end"].iloc[-1], 27_000 - 8_100 - 1_900)     # revenue - labor - other
        # Every downward step is exactly minus its line: nothing is adjusted.
        self.assertAlmostEqual(steps.loc[steps["field"] == "insurance_expense", "amount"].iloc[0], -900.0)

    def test_a_gap_is_shown_as_its_own_step_not_hidden_in_a_bar(self):
        w = self.waterfall(with_ebitda("A", "2026-06", 300, 270, 27_000.0, ebitda=17_500.0))   # 500 too high
        steps = w["steps"]
        self.assertFalse(w["reconciles"])
        gap = steps[steps["kind"] == "difference"]
        self.assertEqual(list(gap["step"]), ["Difference to reported EBITDA"])
        self.assertAlmostEqual(gap["amount"].iloc[0], 500.0)
        self.assertAlmostEqual(steps["end"].iloc[-1], 17_500.0)                    # ends at the REPORTED figure
        self.assertAlmostEqual(steps.loc[steps["field"] == "misc_other_expense", "amount"].iloc[0], -50.0)

    def test_missing_ebitda_is_labelled_calculated_and_not_reconciled(self):
        w = self.waterfall(with_ebitda("A", "2026-06", 300, 270, 27_000.0, ebitda=None))
        self.assertIsNone(w["ebitda_supplied"])
        self.assertFalse(w["reconciles"])
        self.assertEqual(w["steps"]["step"].iloc[-1], "EBITDA (calculated)")

    def test_without_below_gop_detail_uses_one_combined_step(self):
        w = self.waterfall(with_ebitda("A", "2026-06", 300, 270, 27_000.0, detail=False))
        below = w["steps"][w["steps"]["kind"] == "below_gop"]
        self.assertEqual(list(below["step"]), ["Other Expenses (below GOP)"])
        self.assertFalse(w["detailed_below_gop"])
        self.assertTrue(w["reconciles"])

    def test_group_waterfall_adds_hotels_once(self):
        a = with_ebitda("A", "2026-06", 300, 270, 27_000.0)
        b = with_ebitda("B", "2026-06", 900, 450, 90_000.0)
        w = self.waterfall(a, b, ids=("A", "B", "A"))           # A listed twice: still counted once
        self.assertAlmostEqual(w["steps"]["amount"].iloc[0], 117_000.0)
        self.assertAlmostEqual(w["ebitda_supplied"], a["ebitda"] + b["ebitda"])
        self.assertTrue(w["reconciles"])


class MonthlyImportMappingTests(unittest.TestCase):
    def read(self, rows, tmp_name):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path = write_workbook(Path(folder) / tmp_name, {"Actual": rows})
            return read_monthly_workbook(path, tmp_name, PROPERTIES, today=pd.Timestamp("2026-09-15").date())

    def test_below_gop_detail_must_add_up_to_other_expenses(self):
        good = self.read([with_ebitda("A", "2026-06", 300, 270, 27_000.0)], "good.xlsx")
        self.assertFalse([i for i in good.issues if i.check == "Does not reconcile"])
        bad = self.read([with_ebitda("A", "2026-06", 300, 270, 27_000.0, insurance_expense=1_400.0)], "bad.xlsx")
        self.assertTrue(any("Other Expenses (below GOP)" in i.message for i in bad.warnings))

    def test_source_report_subtotal_labels_are_recognised(self):
        row = with_ebitda("A", "2026-06", 300, 270, 27_000.0, detail=False)
        renamed = {("Gross Operating Profit" if k == "gop" else "Total Other Expense" if k == "other_expenses"
                    else d.ALL_FIELDS.get(k, k)): v for k, v in row.items()}
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer) as writer:
            pd.DataFrame([renamed]).to_excel(writer, sheet_name="Actual", index=False)
        result = read_monthly_workbook(buffer.getvalue(), "labels.xlsx", PROPERTIES,
                                       today=pd.Timestamp("2026-09-15").date())
        self.assertFalse(result.errors)
        self.assertAlmostEqual(result.rows["other_expenses"].iloc[0], 1_900.0)
        self.assertFalse([i for i in result.issues if i.check == "Column ignored"])


class SourceImportTests(unittest.TestCase):
    def read(self, source, rows):
        return sources.read_source_file(source, source_file(source, rows), f"{source}.xlsx", PROPERTIES)

    def test_every_demo_file_imports_cleanly_and_stays_apart_from_monthly_figures(self):
        store = build_demo_store()
        facts_before = store.facts.copy()
        for name, file_name in SOURCE_DEMOS.items():
            result = sources.read_source_file(name, (SAMPLE_DIR / file_name).read_bytes(), file_name, store.properties)
            self.assertFalse(result.errors, name)
            store.add_source_import(name, result)
            self.assertEqual(len(store.sources[name]), len(result.rows))
        pd.testing.assert_frame_equal(store.facts, facts_before)                 # monthly results untouched

    def test_common_checks(self):
        result = self.read("channel_mix", [
            {"property_id": "A", "month": "2026-06", "ota_revenue": 100},
            {"property_id": "A", "month": "2026-06", "ota_revenue": 120},            # duplicate record
            {"property_id": "TOTAL", "month": "2026-06", "ota_revenue": 220},       # subtotal row
            {"property_id": "Z", "month": "2026-06", "ota_revenue": 5},             # unknown property
            {"property_id": "B", "month": "2026-06"},                               # no channel revenue
        ])
        checks = {i.check for i in result.errors}
        self.assertEqual(checks, {"Duplicate record", "Subtotal row", "Unknown property", "Missing field"})

    def test_reimport_needs_confirmation_then_replaces(self):
        store = build_demo_store()
        rows = [{"property_id": "H01", "month": "2026-07", "ota_revenue": 100}]
        store.add_source_import("channel_mix", sources.read_source_file(
            "channel_mix", source_file("channel_mix", rows), "one.xlsx", store.properties))
        again = sources.read_source_file("channel_mix", source_file("channel_mix", [{**rows[0], "ota_revenue": 150}]),
                                         "two.xlsx", store.properties)
        with self.assertRaises(Exception):
            store.add_source_import("channel_mix", again)
        store.add_source_import("channel_mix", again, replace=True)
        self.assertEqual(list(store.sources["channel_mix"]["ota_revenue"]), [150])


class ChannelMixTests(unittest.TestCase):
    ROWS = [{"property_id": "A", "month": "2026-06", "sellaway_revenue": 3_000, "direct_revenue": 8_000,
             "ota_revenue": 12_000, "other_channel_revenue": 4_000},
            {"property_id": "B", "month": "2026-06", "direct_revenue": 30_000, "ota_revenue": 50_000,
             "other_channel_revenue": 10_000}]                                   # B: SellAway not reported

    def mix(self, ids):
        rows = sources.read_source_file("channel_mix", source_file("channel_mix", self.ROWS), "c.xlsx", PROPERTIES).rows
        facts = facts_table([hotel_month("A", "2026-06", 300, 270, 27_000.0), hotel_month("B", "2026-06", 900, 450, 90_000.0)])
        return sources.channel_mix(rows, facts, ids, "2026-06")

    def test_blank_is_not_read_as_zero(self):
        table = self.mix(["A", "B"])["table"].set_index("channel")["revenue"]
        self.assertTrue(pd.isna(table["SellAway Revenue"]))                        # not totalled, not zero
        self.assertEqual(table["OTA"], 62_000)

    def test_no_shares_until_channels_are_confirmed_exclusive(self):
        self.assertFalse(self.mix(["A"])["shares"])
        with mock.patch.object(d, "CHANNELS_MUTUALLY_EXCLUSIVE", True):
            mix = self.mix(["A"])
        self.assertTrue(mix["shares"])
        self.assertAlmostEqual(mix["table"]["share"].sum(), 1.0)
        self.assertAlmostEqual(mix["difference"], 27_000 - 27_000)                 # channels = rooms revenue

    def test_hotels_without_channel_data_are_listed(self):
        self.assertEqual(self.mix(["A", "C"])["missing"], ["C"])


class PaceTests(unittest.TestCase):
    def test_stay_months_must_be_in_the_future_and_as_of_is_required(self):
        result = sources.read_source_file("pace", source_file("pace", [
            {"property_id": "A", "as_of_date": "2026-09-01", "stay_month": "2026-08", "room_nights_otb": 10,
             "room_revenue_otb": 1000},
            {"property_id": "A", "stay_month": "2026-10", "room_nights_otb": 10, "room_revenue_otb": 1000},
        ]), "p.xlsx", PROPERTIES)
        self.assertEqual({i.check for i in result.errors}, {"Not a future period", "Missing field"})

    def test_latest_as_of_date_only_and_percent_of_budget(self):
        rows = pd.DataFrame([
            {"property_id": "A", "as_of_date": "2026-05-01", "stay_month": "2026-06", "room_nights_otb": 100.0, "room_revenue_otb": 10_000.0},
            {"property_id": "A", "as_of_date": "2026-05-15", "stay_month": "2026-06", "room_nights_otb": 135.0, "room_revenue_otb": 13_500.0},
            {"property_id": "B", "as_of_date": "2026-05-01", "stay_month": "2026-06", "room_nights_otb": 300.0, "room_revenue_otb": 60_000.0},
        ])
        facts = facts_table([], [hotel_month("A", "2026-06", 300, 270, 27_000.0)])
        view = sources.pace_view(rows, facts, ["A", "B"])
        self.assertEqual(view["as_of"], "2026-05-15")
        self.assertEqual(view["missing"], ["B"])                                   # older report not mixed in
        row = view["table"].iloc[0]
        self.assertEqual(row["room_nights"], 135)
        self.assertAlmostEqual(row["pct_of_budget_nights"], 135 / 270)
        self.assertEqual(row["pickup_nights"], 35)


class StrTests(unittest.TestCase):
    HIST = {"report_type": "Historical", "period": "2026-06", "report_date": "2026-07-17", "occupancy": 0.80,
            "adr": 150.0, "revpar": 120.0, "comp_occupancy": 0.75, "comp_adr": 160.0, "comp_revpar": 120.0}

    def test_indexes_are_per_hotel_and_historical_forward_are_kept_apart(self):
        rows = sources.read_source_file("str", source_file("str", [
            {"property_id": "A", **self.HIST},
            {"property_id": "B", **self.HIST, "occupancy": 0.60, "revpar": 90.0},
            {"property_id": "A", "report_type": "Forward", "period": "2026-08", "report_date": "2026-07-20",
             "occupancy": 0.40, "comp_occupancy": 0.50}]), "s.xlsx", PROPERTIES).rows
        view = sources.str_view(rows, ["A", "B"], "2026-06")
        hist = view["historical"].set_index("property_id")
        self.assertAlmostEqual(hist.loc["A", "rgi"], 100.0)
        self.assertAlmostEqual(hist.loc["B", "rgi"], 75.0)
        self.assertEqual(len(hist), 2)                                              # no portfolio average row
        self.assertEqual(list(view["forward"]["property_id"]), ["A"])
        self.assertAlmostEqual(view["forward"]["occupancy_index"].iloc[0], 80.0)

    def test_historical_rows_need_rates_and_forward_rows_need_future_months(self):
        result = sources.read_source_file("str", source_file("str", [
            {"property_id": "A", **{**self.HIST, "adr": None}},
            {"property_id": "A", "report_type": "Forward", "period": "2026-05", "report_date": "2026-07-20",
             "occupancy": 0.4, "comp_occupancy": 0.5}]), "s.xlsx", PROPERTIES)
        self.assertEqual({i.check for i in result.errors}, {"Missing field", "Not a future period"})


class DscrTests(unittest.TestCase):
    def debt(self, months, cash=1_300.0, minimum=1.25):
        return pd.DataFrame([{"property_id": "A", "month": m, "debt_service": 1_000.0, "cash_flow": cash,
                              "dscr_minimum": minimum, "lender": ""} for m in months])

    def test_trailing_twelve_months_when_supplied(self):
        months = [metrics.shift_month("2026-06", -n) for n in range(12)]
        row = sources.dscr_view(self.debt(months), ["A"], "2026-06").iloc[0]
        self.assertTrue(row["period"].startswith("T12 Jul 2025"))
        self.assertAlmostEqual(row["dscr"], 1.3)
        self.assertEqual(row["status"], "good")

    def test_month_only_is_labelled_and_below_minimum_needs_review(self):
        row = sources.dscr_view(self.debt(["2026-05", "2026-06"], cash=1_100.0, minimum=1.20), ["A"], "2026-06").iloc[0]
        self.assertIn("only (T12 needs 12 months; 2 supplied)", row["period"])
        self.assertEqual(row["status"], "review")

    def test_no_numerator_or_threshold_is_assumed(self):
        no_cash = sources.dscr_view(self.debt(["2026-06"], cash=None), ["A"], "2026-06").iloc[0]
        self.assertTrue(pd.isna(no_cash["dscr"]))
        self.assertIn("cash-flow", no_cash["note"])
        no_min = sources.dscr_view(self.debt(["2026-06"], minimum=float("nan")), ["A"], "2026-06").iloc[0]
        self.assertEqual(no_min["status"], "no_threshold")
        missing = sources.dscr_view(pd.DataFrame(), ["A"], "2026-06").iloc[0]
        self.assertEqual(missing["status"], "missing")

    def test_zero_debt_service_is_rejected(self):
        result = sources.read_source_file("debt", source_file("debt", [
            {"property_id": "A", "month": "2026-06", "debt_service": 0}]), "d.xlsx", PROPERTIES)
        self.assertEqual(result.errors[0].check, "Invalid value")


if __name__ == "__main__":
    unittest.main()
