"""Tests for the calculations: rates from totals, groups, no double counting, budget variances."""
import unittest

from hotel_dashboard import metrics
from tests.helpers import ACTUAL_JUNE, BUDGET_JUNE, PROPERTIES, facts_table, hotel_month


class RatesComeFromTotals(unittest.TestCase):
    def test_portfolio_rates_are_recalculated_not_averaged(self):
        facts = facts_table(ACTUAL_JUNE)
        totals = metrics.combine(facts, ["A", "B"], "2026-06", "Actual")
        self.assertEqual(totals["rooms_sold"], 720)
        self.assertEqual(totals["rooms_available"], 1200)
        self.assertAlmostEqual(totals["occupancy"], 0.60)       # not the 70% a simple average gives
        self.assertAlmostEqual(totals["adr"], 162.50)           # not $150
        self.assertAlmostEqual(totals["revpar"], 97.50)         # not $95

    def test_revpar_equals_occupancy_times_adr(self):
        totals = metrics.combine(facts_table(ACTUAL_JUNE), ["A", "B"], "2026-06", "Actual")
        self.assertAlmostEqual(totals["revpar"], totals["occupancy"] * totals["adr"])

    def test_zero_rooms_does_not_crash(self):
        rates = metrics.calculate_rates({"rooms_available": 0, "rooms_sold": 0, "rooms_revenue": 0})
        self.assertEqual(rates, {"occupancy": None, "adr": None, "revpar": None})

    def test_total_is_hidden_when_a_hotel_is_missing_that_line(self):
        rows = [hotel_month("A", "2026-06", 300, 270, 27_000.0, ebitda=5_000.0),
                hotel_month("B", "2026-06", 900, 450, 90_000.0)]          # B has no EBITDA
        totals = metrics.combine(facts_table(rows), ["A", "B"], "2026-06", "Actual")
        self.assertIsNone(totals["ebitda"])                               # rather than an understated $5,000

    def test_noi_is_calculated_from_its_parts(self):
        # GOP 18,900 - other expenses 2,000 - capital reserve 1,000 = 15,900 (the file's NOI of 99 is wrong)
        row = hotel_month("A", "2026-06", 300, 270, 27_000.0, other_expenses=2_000.0, capital_reserve=1_000.0,
                          noi=99.0)
        totals = metrics.combine(facts_table([row]), ["A"], "2026-06", "Actual")
        self.assertAlmostEqual(totals["noi"], 27_000 * 0.70 - 2_000 - 1_000)

    def test_noi_falls_back_to_the_file_when_a_part_is_missing(self):
        row = hotel_month("A", "2026-06", 300, 270, 27_000.0, noi=12_345.0)   # no other expenses / reserve
        totals = metrics.combine(facts_table([row]), ["A"], "2026-06", "Actual")
        self.assertAlmostEqual(totals["noi"], 12_345.0)


class GroupsAndDoubleCounting(unittest.TestCase):
    def test_group_selection(self):
        blue = metrics.Scope("group", classification="brand", group="Blue")
        stabilized = metrics.Scope("group", classification="stage", group="Stabilized")
        self.assertEqual(metrics.property_ids_in_scope(PROPERTIES, blue), ["A", "B"])
        self.assertEqual(metrics.property_ids_in_scope(PROPERTIES, stabilized), ["A", "C"])
        self.assertEqual(metrics.property_ids_in_scope(PROPERTIES, metrics.Scope()), ["A", "B", "C"])

    def test_hotel_listed_twice_is_counted_once(self):
        facts = facts_table(ACTUAL_JUNE)
        once = metrics.combine(facts, ["A", "B"], "2026-06", "Actual")
        twice = metrics.combine(facts, ["A", "A", "B"], "2026-06", "Actual")
        self.assertEqual(once["rooms_sold"], twice["rooms_sold"])

    def test_duplicate_stored_rows_are_refused(self):
        facts = facts_table(ACTUAL_JUNE + [ACTUAL_JUNE[0]])               # hotel A twice in June
        with self.assertRaises(ValueError):
            metrics.combine(facts, ["A", "B"], "2026-06", "Actual")


class BudgetVariances(unittest.TestCase):
    def setUp(self):
        self.summary = metrics.month_summary(facts_table(ACTUAL_JUNE, BUDGET_JUNE), ["A", "B"], "2026-06")

    def test_variances(self):
        a, b = self.summary["actual"], self.summary["budget"]
        self.assertAlmostEqual(b["occupancy"], 0.625)                     # 750 / 1,200
        self.assertAlmostEqual(metrics.variance(a, b, "occupancy")["difference"], -0.025)   # -2.5 points
        self.assertAlmostEqual(metrics.variance(a, b, "adr")["difference"], 162.50 - 159.40)
        self.assertAlmostEqual(metrics.variance(a, b, "revpar")["difference"], 97.50 - 99.625)

    def test_revpar_bridge_adds_up(self):
        bridge = metrics.revpar_bridge(self.summary["actual"], self.summary["budget"])
        self.assertAlmostEqual(bridge["occupancy_effect"], -0.025 * 159.40)   # -$3.985
        self.assertAlmostEqual(bridge["rate_effect"], 3.10 * 0.60)            # +$1.86
        self.assertAlmostEqual(bridge["total"], 97.50 - 99.625)

    def test_lower_cost_is_favorable(self):
        v = metrics.variance({"labor_expense": 100.0}, {"labor_expense": 120.0}, "labor_expense")
        self.assertTrue(v["favorable"])

    def test_no_budget_comparison_when_budget_is_incomplete(self):
        summary = metrics.month_summary(facts_table(ACTUAL_JUNE, BUDGET_JUNE[:1]), ["A", "B"], "2026-06")
        self.assertIsNone(summary["budget"])
        self.assertEqual(summary["budget_missing"], ["B"])

    def test_missing_actuals_are_reported(self):
        summary = metrics.month_summary(facts_table(ACTUAL_JUNE), ["A", "B", "C"], "2026-06")
        self.assertEqual(summary["missing_actual"], ["C"])


class ProfitBridge(unittest.TestCase):
    """Budget GOP 83,685 -> actual GOP 81,900 (helpers: labor is 30% of rooms revenue, no other costs)."""

    def summary(self, actual=ACTUAL_JUNE, budget=BUDGET_JUNE):
        return metrics.month_summary(facts_table(actual, budget), ["A", "B"], "2026-06")

    def test_bridge_steps_add_up_to_actual_gop(self):
        s = self.summary()
        bridge = metrics.gop_bridge(s["actual"], s["budget"])
        steps = bridge["steps"]
        self.assertAlmostEqual(steps["amount"].iloc[0], 83_685.0)                 # budget GOP
        self.assertAlmostEqual(steps["amount"].iloc[-1], 81_900.0)                # actual GOP
        middle = steps.iloc[1:-1]
        self.assertAlmostEqual(83_685.0 + middle["amount"].sum(), 81_900.0)
        self.assertAlmostEqual(bridge["end_of_steps"], s["actual"]["gop"])
        amounts = dict(zip(middle["field"], middle["amount"]))
        self.assertAlmostEqual(amounts["rooms_revenue"], 117_000 - 119_550)       # revenue shortfall
        self.assertAlmostEqual(amounts["labor_expense"], 35_865 - 35_100)         # labor saving adds profit
        self.assertNotIn("unexplained", amounts)

    def test_gap_in_the_imported_lines_is_shown_not_hidden(self):
        wrong_gop = [dict(ACTUAL_JUNE[0], gop=ACTUAL_JUNE[0]["gop"] - 500), ACTUAL_JUNE[1]]
        s = self.summary(wrong_gop)
        bridge = metrics.gop_bridge(s["actual"], s["budget"])
        steps = bridge["steps"]
        self.assertAlmostEqual(bridge["unexplained"], -500.0)
        self.assertIn("unexplained", list(steps["field"]))
        self.assertAlmostEqual(bridge["end_of_steps"], steps["amount"].iloc[-1])   # still reconciles

    def test_no_bridge_without_a_complete_budget(self):
        s = self.summary(budget=BUDGET_JUNE[:1])
        self.assertIsNone(metrics.gop_bridge(s["actual"], s["budget"]))


class ExceptionMapAndCharts(unittest.TestCase):
    def test_exception_map_statuses(self):
        import pandas as pd
        actual = [dict(ACTUAL_JUNE[0], labor_expense=12_000.0, total_operating_expenses=12_000.0, gop=15_000.0),
                  ACTUAL_JUNE[1]]
        warnings = pd.DataFrame([{"property_id": "B", "month": "2026-06", "message": "x"}])
        table = metrics.exception_map(facts_table(actual, BUDGET_JUNE), PROPERTIES, ["A", "B", "C"], "2026-06",
                                      warnings).set_index("property_id")
        self.assertEqual(table.loc["A", "labor_status"], "review")       # $4,350 and 57% over budget
        self.assertEqual(table.loc["A", "overall"], "review")
        self.assertEqual(table.loc["B", "data_status"], "watch")         # one data-check warning
        self.assertEqual(table.loc["C", "data_status"], "review")        # no actuals at all
        self.assertEqual(table.loc["C", "revpar_status"], "missing")
        self.assertEqual(len(table), 3)                                  # one row per hotel

    def test_largest_expense_variances_come_first(self):
        actual = [dict(ACTUAL_JUNE[0], rooms_expense=900.0), dict(ACTUAL_JUNE[1], ga_expense=-50.0)]
        s = metrics.month_summary(facts_table(actual, BUDGET_JUNE), ["A", "B"], "2026-06")
        table = metrics.expense_variances(s, limit=3)
        self.assertEqual(table["line"].iloc[0], "Rooms Expense")          # +900 is the biggest move
        self.assertEqual(list(table["difference"].abs()), sorted(table["difference"].abs(), reverse=True))

    def test_revenue_sources_are_only_those_in_the_data(self):
        s = metrics.month_summary(facts_table(ACTUAL_JUNE, BUDGET_JUNE), ["A", "B"], "2026-06")
        self.assertEqual(list(metrics.revenue_by_source(s)["source"]), ["Rooms Revenue", "Other Revenue"])


class Months(unittest.TestCase):
    def test_shift_month_across_year_end(self):
        self.assertEqual(metrics.shift_month("2026-01", -1), "2025-12")
        self.assertEqual(metrics.shift_month("2025-12", 1), "2026-01")


if __name__ == "__main__":
    unittest.main()
