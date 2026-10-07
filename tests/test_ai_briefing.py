"""
Tests for the optional AI briefing draft (hosted fictional demo only). The model is always MOCKED:
no test sends anything to Google. Covers the evidence packet, the validation of the reply, error
handling, the local-mode block, and the on-screen flow (draft, approval, invalidation).
"""
import json
import unittest
from unittest import mock

import requests
from streamlit.testing.v1 import AppTest

from hotel_dashboard import ai_briefing, ai_eval
from hotel_dashboard.ai_briefing import FACT, POSSIBLE_CAUSE, AIError
from hotel_dashboard.demo import build_demo_store

POST = "hotel_dashboard.ai_briefing.requests.post"


def response(status=200, payload=None, text=None):
    r = mock.Mock()
    r.status_code = status
    r.text = text if text is not None else json.dumps(payload or {})
    r.json.side_effect = (lambda: payload) if payload is not None else ValueError("no json")
    return r


def gemini_reply(draft) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(draft)}]}, "finishReason": "STOP"}]}


def faithful_draft(packet) -> dict:
    """What a well-behaved model returns: evidence copied word for word, with its IDs."""
    ev = packet["evidence"]
    first = lambda cond: next(e for e in ev if cond(e))
    changed = first(lambda e: e["section_hint"] == "changed")
    cause = first(lambda e: e["kind"] == POSSIBLE_CAUSE)
    decision = first(lambda e: e["section_hint"] == "decision")
    return {"what_changed": [{"text": changed["text"], "evidence": [changed["id"]], "kind": FACT}],
            "why_it_matters": [{"text": cause["text"], "evidence": [cause["id"]], "kind": FACT}],
            "what_needs_a_decision": [{"text": decision["text"], "evidence": [decision["id"]], "kind": FACT}]}


def echo_model(url, **kwargs):
    packet = json.loads(kwargs["json"]["contents"][0]["parts"][0]["text"])
    return response(200, gemini_reply(faithful_draft(packet)))


def july_packet():
    store = build_demo_store()
    ids = list(store.properties["property_id"])
    return ai_briefing.evidence_packet(store.facts, store.properties, ids, "All properties", "2026-07",
                                       store.active_warnings(), ids)


class PacketTests(unittest.TestCase):
    def test_packet_is_small_verified_evidence_not_the_workbook(self):
        packet = july_packet()
        text = json.dumps(packet)
        self.assertLess(len(text), 10_000)
        self.assertEqual(packet["data"], "FICTIONAL demo data")
        self.assertNotIn("rooms_available", text)                     # no raw workbook columns or rows
        kinds = {e["kind"] for e in packet["evidence"]}
        self.assertTrue({FACT, POSSIBLE_CAUSE, "exception_flag", "reconciliation"} <= kinds)
        self.assertEqual([e["id"] for e in packet["evidence"]],
                         [f"E{n}" for n in range(1, len(packet["evidence"]) + 1)])


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.packet = july_packet()
        self.ev = self.packet["evidence"]
        self.fact = next(e for e in self.ev if e["kind"] == FACT and ai_briefing.numbers_in(e["text"]))
        self.cause = next(e for e in self.ev if e["kind"] == POSSIBLE_CAUSE)

    def check(self, items, section="what_changed"):
        raw = {"what_changed": [], "why_it_matters": [], "what_needs_a_decision": [], section: items}
        return ai_briefing.validate(raw, self.packet, "test-model", "fp")

    def test_copied_figures_are_kept(self):
        draft = self.check([{"text": self.fact["text"], "evidence": [self.fact["id"]], "kind": FACT}])
        self.assertEqual(len(draft.sections["changed"]), 1)
        self.assertEqual(draft.numbers_supported, draft.numbers_total)
        self.assertFalse(draft.removed)

    def test_invented_number_is_removed(self):
        draft = self.check([{"text": "RevPAR rose 99.9% to $512.34.", "evidence": [self.fact["id"]], "kind": FACT}])
        self.assertFalse(draft.sections["changed"])
        self.assertIn("not in the cited evidence", draft.removed[0][2])
        self.assertEqual(draft.numbers_supported, 0)

    def test_a_number_from_uncited_evidence_is_not_accepted(self):
        other = next(e for e in self.ev if e["id"] != self.fact["id"] and ai_briefing.numbers_in(e["text"])
                     - ai_briefing.numbers_in(self.fact["text"] + self.fact["source"]))
        draft = self.check([{"text": other["text"], "evidence": [self.fact["id"]], "kind": FACT}])
        self.assertFalse(draft.sections["changed"])

    def test_missing_or_unknown_evidence_is_removed(self):
        draft = self.check([{"text": "Results were mixed.", "evidence": [], "kind": FACT},
                            {"text": "Results were mixed.", "evidence": ["E999"], "kind": FACT}])
        self.assertEqual([r[2][:20] for r in draft.removed], ["no evidence cited", "cites evidence that "])

    def test_possible_causes_can_never_be_shown_as_facts(self):
        draft = self.check([{"text": self.cause["text"], "evidence": [self.cause["id"]], "kind": FACT},
                            {"text": "Labor costs might explain the gap.", "evidence": [self.fact["id"]],
                             "kind": FACT}], section="why_it_matters")
        self.assertEqual([i["kind"] for i in draft.sections["matters"]], [POSSIBLE_CAUSE, POSSIBLE_CAUSE])

    def test_at_most_three_items_per_section(self):
        item = {"text": "Results were mixed.", "evidence": [self.fact["id"]], "kind": FACT}
        draft = self.check([item] * 5)
        self.assertEqual(len(draft.sections["changed"]), 3)
        self.assertEqual(len(draft.removed), 2)


class CallTests(unittest.TestCase):
    def call(self, **overrides):
        args = {"api_key": "test-key", "hosted_demo": True, "workspace": "demo", **overrides}
        return ai_briefing.call_gemini(july_packet(), args.pop("api_key"), **args)

    def test_local_company_mode_never_calls_out(self):
        with mock.patch(POST) as post:
            for hosted, workspace in ((False, "demo"), (False, "local"), (True, "local")):
                with self.assertRaises(AIError) as raised:
                    self.call(hosted_demo=hosted, workspace=workspace)
                self.assertEqual(raised.exception.code, "disabled")
        post.assert_not_called()
        self.assertFalse(ai_briefing.ai_allowed(False, "local"))

    def test_missing_key_makes_no_request(self):
        with mock.patch(POST) as post, self.assertRaises(AIError) as raised:
            self.call(api_key=None)
        self.assertEqual(raised.exception.code, "missing_key")
        post.assert_not_called()

    def test_errors_become_clear_messages(self):
        cases = [(response(429, {"error": {}}), "quota"), (response(403, {"error": {}}), "bad_key"),
                 (response(400, text="API key not valid"), "bad_key"), (response(503, {"error": {}}), "failure"),
                 (response(200, text="not json"), "invalid_response"),
                 (response(200, {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}), "invalid_response")]
        for reply, code in cases:
            with mock.patch(POST, return_value=reply), self.assertRaises(AIError) as raised:
                self.call()
            self.assertEqual(raised.exception.code, code)
            self.assertTrue(raised.exception.message)
        with mock.patch(POST, side_effect=requests.Timeout()), self.assertRaises(AIError) as raised:
            self.call()
        self.assertEqual(raised.exception.code, "failure")

    def test_sends_only_the_packet_with_the_key_in_a_header(self):
        with mock.patch(POST, side_effect=echo_model) as post:
            draft = self.call()
        url, kwargs = post.call_args[0][0], post.call_args[1]
        self.assertNotIn("test-key", url)
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "test-key")
        self.assertEqual(json.loads(kwargs["json"]["contents"][0]["parts"][0]["text"]), july_packet())
        self.assertEqual(set(draft), set(ai_briefing.SECTION_FIELDS.values()))


def hosted_app():
    import app
    try:
        app.main(hosted_demo=True)
    finally:
        app.MODE["hosted_demo"] = False          # don't leak hosted mode into other tests in this process


def page_text(at) -> str:
    parts = [e.value for kind in ("markdown", "caption", "info", "warning", "success", "subheader")
             for e in getattr(at, kind)]
    return "\n".join(str(p) for p in parts).replace("\\$", "$")


class ScreenTests(unittest.TestCase):
    def open(self, key="test-key"):
        at = AppTest.from_function(hosted_app, default_timeout=120)
        if key:
            at.secrets["GEMINI_API_KEY"] = key
        return at

    def button(self, at, label):
        return next(b for b in at.button if b.label == label)

    def test_local_app_has_no_ai_button(self):
        at = AppTest.from_file("../app.py", default_timeout=120)
        at.session_state["workspace"] = "demo"
        with mock.patch(POST) as post:
            at.run()
        self.assertFalse([b for b in at.button if b.label == "Draft AI Briefing"])
        post.assert_not_called()

    def test_model_called_only_on_click_then_draft_needs_review(self):
        at = self.open()
        with mock.patch(POST, side_effect=echo_model) as post:
            at.run()
            post.assert_not_called()                                     # not on page load
            self.button(at, "Draft AI Briefing").click().run()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        text = page_text(at)
        self.assertIn("AI draft—asset-manager review required", text)
        self.assertIn("Possible cause (unverified)", text)              # relabelled although the model said fact
        self.assertIn("Management Briefing", text)                       # rule-based briefing still there
        self.assertFalse([b for b in at.get("download_button") if "AI draft" in b.label])   # locked until approved

    def test_selection_change_discards_the_draft(self):
        at = self.open()
        with mock.patch(POST, side_effect=echo_model):
            at.run()
            self.button(at, "Draft AI Briefing").click().run()
            self.assertIn("AI draft—asset-manager review required", page_text(at))
            next(s for s in at.selectbox if s.label == "Month").set_value("2026-06").run()
        self.assertIn("previous AI draft (and any approval of it) was discarded", page_text(at))
        # Approval of the AI draft is on Review Log: see tests/test_review_log.py.

    def test_missing_key_and_quota_leave_the_dashboard_usable(self):
        for key, reply, message in ((None, None, "no GEMINI_API_KEY"),
                                    ("test-key", response(429, {"error": {}}), "free Gemini quota is used up")):
            at = self.open(key)
            with mock.patch(POST, return_value=reply) as post:
                at.run()
                self.button(at, "Draft AI Briefing").click().run()
            self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
            self.assertIn(message, page_text(at))
            self.assertIn("Management Briefing", page_text(at))
            self.assertEqual(post.call_count, 0 if key is None else 1)


class EvaluationHarnessTests(unittest.TestCase):
    def test_scores_a_faithful_model(self):
        results = ai_eval.run(faithful_draft, "mock")
        self.assertEqual([r["score"]["case"] for r in results],
                         ["Normal month", "Adverse month", "Missing data", "Reconciliation error"])
        for r in results:
            self.assertEqual(r["score"]["unsupported claims"], 0)
            self.assertEqual(r["score"]["hedged facts"], 0)
        recon = results[3]["packet"]["evidence"]
        self.assertTrue(any(e["kind"] == "reconciliation" and "does not reconcile" in e["text"] for e in recon))
        missing = results[2]["packet"]
        self.assertEqual(missing["hotels_reporting"], "5 of 6 hotels reporting")

    def test_scores_invented_numbers_and_failures(self):
        def inventor(packet):
            e = packet["evidence"][0]
            return {"what_changed": [{"text": "Occupancy jumped 123.4%.", "evidence": [e["id"]], "kind": FACT}],
                    "why_it_matters": [], "what_needs_a_decision": []}
        score = ai_eval.run(inventor, "mock")[0]["score"]
        self.assertEqual(score["numbers correct"], "0 of 1")
        self.assertEqual(score["unsupported claims"], 1)

        def over_quota(packet):
            raise AIError("quota")
        self.assertEqual(ai_eval.run(over_quota, "mock")[0]["score"]["result"], "Failed: quota")


if __name__ == "__main__":
    unittest.main()
