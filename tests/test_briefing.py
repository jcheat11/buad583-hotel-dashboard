"""Tests for the written briefing: three cards, traceable facts, possible causes kept separate, review gate."""
import unittest

from hotel_dashboard import briefing
from tests.helpers import ACTUAL_JUNE, BUDGET_JUNE, PROPERTIES, facts_table


def build(actual=ACTUAL_JUNE, budget=BUDGET_JUNE):
    return briefing.build_briefing(facts_table(actual, budget), PROPERTIES, ["A", "B"], "Brand: Blue", "2026-06")


class BriefingTests(unittest.TestCase):
    def test_every_statement_belongs_to_one_of_the_three_cards(self):
        self.assertEqual({s.section for s in build()}, {"changed", "matters", "decision"})

    def test_statements_quote_the_verified_figures_and_their_source(self):
        occupancy = next(s for s in build() if s.text.startswith("Occupancy"))
        self.assertEqual(occupancy.section, "changed")
        self.assertIn("60.0%, 2.5 points below budget (62.5%)", occupancy.text)
        self.assertIn("720 rooms sold / 1,200 rooms available", occupancy.source)

    def test_revpar_variance_is_explained_by_occupancy_and_rate_effects(self):
        # The exact effects (-$3.985 and +$1.86) are tested in test_metrics; here we check the wording.
        effect = next(s for s in build() if "of RevPAR" in s.text)
        self.assertEqual((effect.section, effect.kind), ("matters", "fact"))
        self.assertIn("Lower occupancy took away", effect.text)
        self.assertIn("higher ADR added $1.86", effect.text)

    def test_gop_bridge_statement_reconciles(self):
        # Budget GOP 83,685 + rooms revenue -2,550 + labor saving 765 = actual GOP 81,900
        bridge = next(s for s in build() if "GOP bridge" in s.source)
        self.assertIn("budget GOP $83,685", bridge.source)
        self.assertIn("actual GOP $81,900", bridge.source)

    def test_possible_causes_are_marked_and_unverified(self):
        causes = [s for s in build() if s.kind == "possible_cause"]
        self.assertTrue(causes)
        for s in causes:
            self.assertEqual(s.section, "matters")
            self.assertTrue(s.text.endswith("?"), s.text)        # written as questions, not findings
            self.assertTrue("Not verified" in s.source or "Standard question" in s.source)

    def test_labor_overspend_needs_a_decision(self):
        actual = [dict(ACTUAL_JUNE[0], labor_expense=12_000.0, total_operating_expenses=12_000.0,
                       gop=15_000.0), ACTUAL_JUNE[1]]            # budget labor is $7,650
        decisions = [s.text for s in build(actual) if s.section == "decision"]
        self.assertTrue(any("Alpha Hotel: Labor & Benefits" in t for t in decisions), decisions)

    def test_card_summaries_are_one_headline_and_one_sentence(self):
        cards = briefing.card_summaries(facts_table(ACTUAL_JUNE, BUDGET_JUNE), PROPERTIES, ["A", "B"], "2026-06")
        self.assertEqual(set(cards), {"changed", "matters", "decision"})
        self.assertEqual(cards["changed"][0], "GOP 2.1% below budget")          # 81,900 vs 83,685
        self.assertEqual(cards["matters"][0], "Revenue shortfall")              # revenue -2,550; costs -765
        self.assertEqual(cards["decision"][0], "No decisions needed")
        for headline, sentence in cards.values():
            self.assertLessEqual(len(headline), 45)
            self.assertLessEqual(sentence.count(". "), 1, sentence)

    def test_card_summary_names_the_property_needing_a_decision(self):
        actual = [dict(ACTUAL_JUNE[0], labor_expense=12_000.0, total_operating_expenses=12_000.0,
                       gop=15_000.0), ACTUAL_JUNE[1]]
        cards = briefing.card_summaries(facts_table(actual, BUDGET_JUNE), PROPERTIES, ["A", "B"], "2026-06")
        self.assertEqual(cards["decision"][0], "1 property to review")
        # GOP 15,000 vs 17,850 budget is also 16% below budget, so both reasons are named.
        self.assertIn("Alpha Hotel (GOP below budget, Labor & Benefits over budget)", cards["decision"][1])

    def test_changed_figures_change_the_fingerprint(self):
        changed = [dict(ACTUAL_JUNE[0], rooms_sold=265), ACTUAL_JUNE[1]]
        self.assertNotEqual(briefing.fingerprint(build()), briefing.fingerprint(build(changed)))

    def test_export_is_marked_draft_until_reviewed(self):
        statements = build()
        draft = briefing.to_markdown(statements, "Brand: Blue", "2026-06", "June 2026 figures")
        approved = briefing.to_markdown(statements, "Brand: Blue", "2026-06", "June 2026 figures",
                                        reviewer="Pat Lee", reviewed_at="01 Jul 2026, 10:00")
        self.assertIn("DRAFT - NOT REVIEWED", draft)
        self.assertIn("Possible causes (not verified)", draft)
        self.assertIn("approved by Pat Lee", approved)
        self.assertNotIn("DRAFT", approved)


if __name__ == "__main__":
    unittest.main()
