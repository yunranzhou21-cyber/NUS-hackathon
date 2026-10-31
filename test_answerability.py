import tempfile
import unittest
from pathlib import Path

from answerability import assess
from applicability import evaluate, load_records, parse_record
from search import SearchEngine, build_index


def result(kind="proposal", text="The FAA proposes an inspection of the nut.", full=False, mentions=()):
    source = {"published_page_range_raw": "(Pages 1-4)", "citation": "Federal Register X",
              "published_page_range": [1, 4]}
    return {"record_id": "row-0001",
            "title": {"evidence": {"source": source}},
            "evidence": [{"quote": text, "field": "Summary", "start": 0, "end": len(text), "source": source}],
            "configuration_check": {"document_kind": kind},
            "full_text_available": full,
            "revision": {"supersession_mentions": list(mentions)}}


def codes(query, **kw):
    return [u["code"] for u in assess(query, [result(**kw)])["unmet_requests"]]


class MustBeFlagged(unittest.TestCase):
    """Phrasings differ from the labeled development cases on purpose."""

    def test_exact_page(self):
        for q in ("On which page does the inspection requirement appear?",
                  "What page number is the applicability statement on?",
                  "Give me the specific page for that sentence."):
            self.assertEqual(codes(q), ["EXACT_PAGE_UNAVAILABLE"], q)

    def test_release_decision(self):
        for q in ("Is my aircraft airworthy given this notice?",
                  "Can I fly this helicopter tomorrow?",
                  "Am I compliant if I skip the inspection?",
                  "Can I return it to service after this?"):
            self.assertEqual(codes(q), ["OPERATIONAL_DECISION_OUT_OF_SCOPE"], q)

    def test_value_not_in_record(self):
        for q in ("What is the torque value for the nut?",
                  "State the precise clearance in mm.",
                  "Give me the specific interval in flight hours."):
            self.assertIn("REQUESTED_VALUE_NOT_IN_RECORD", codes(q), q)

    def test_procedure_not_in_record(self):
        for q in ("Provide the detailed instructions for the repair.",
                  "How do I replace the servo-control?",
                  "Walk me through the step-by-step method."):
            self.assertIn("PROCEDURE_NOT_IN_RECORD", codes(q), q)

    def test_effective_status(self):
        for q in ("Has AD 2025-09-07 been superseded already?",
                  "Is it in effect right now?",
                  "Is this final?"):
            self.assertIn("EFFECTIVE_STATUS_NOT_ESTABLISHED", codes(q), q)

    def test_several_unmet_requests_reported_together(self):
        found = codes("Give me the exact torque and the complete repair procedure.")
        self.assertEqual(set(found), {"REQUESTED_VALUE_NOT_IN_RECORD", "PROCEDURE_NOT_IN_RECORD"})


class MustNotBeFlagged(unittest.TestCase):
    """Ordinary questions that share vocabulary with the flagged ones."""

    def test_normal_questions_pass_through(self):
        for q in ("Find the proposal about loss of tightening torque on a servo-control.",
                  "What actions does the proposal require?",
                  "Does an EMB-120 with serial 120006 match the stated applicability?",
                  "Which proposals mention corrosion on the wing?",
                  "Is this record evidence of an active proposal?",
                  "Does the notice mention a reporting requirement?",
                  "What is the docket for the A330 proposal?",
                  "Show proposals published in the Federal Register in 2004."):
            self.assertEqual(codes(q), [], q)

    def test_value_present_in_evidence_is_not_flagged(self):
        text = "Inspect within 2,500 flight cycles after the effective date."
        self.assertEqual(codes("How many flight cycles until the inspection?", text=text), [])

    def test_procedure_not_flagged_when_full_text_exists(self):
        self.assertEqual(codes("Give me the complete procedure.", full=True), [])

    def test_effective_not_flagged_when_record_may_be_final(self):
        self.assertEqual(codes("Is it in effect?", kind="needs_review"), [])

    def test_empty_results_and_bad_input(self):
        self.assertEqual(assess("anything", [])["verdict"], "NO_UNMET_REQUEST_DETECTED")
        self.assertEqual(assess(None, [result()])["verdict"], "NO_UNMET_REQUEST_DETECTED")

    def test_verdict_never_claims_answered(self):
        block = assess("What actions does it require?", [result()])
        self.assertEqual(block["verdict"], "NO_UNMET_REQUEST_DETECTED")
        self.assertIn("does not mean", block["limits"])


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.db = str(Path(cls.temp.name) / "i.sqlite3")
        build_index(database=cls.db)
        cls.engine = SearchEngine(database=cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()
        cls.temp.cleanup()

    def test_record_found_but_unanswerable_is_stated_not_hidden(self):
        r = self.engine.search("From FAA-2026-11814, what is the exact tightening torque value?")
        self.assertEqual(r["status"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("REQUESTED_VALUE_NOT_IN_RECORD", r["reason_codes"])
        self.assertEqual([x["record_id"] for x in r["results"]], ["row-0001"])  # still shown
        self.assertEqual(r["operational_answer"], "REFUSE")

    def test_release_question_refused(self):
        r = self.engine.search("Can I release this AS350B3 to service based on FAA-2026-11814?")
        self.assertEqual(r["status"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("OPERATIONAL_DECISION_OUT_OF_SCOPE", r["reason_codes"])

    def test_supersession_claim_cites_the_proposal_sentence(self):
        r = self.engine.search("Has AD 2025-09-07 already been superseded according to FAA-2026-11813?")
        self.assertEqual(r["status"], "INSUFFICIENT_EVIDENCE")
        unmet = r["answerability"]["unmet_requests"][0]
        self.assertEqual(unmet["code"], "EFFECTIVE_STATUS_NOT_ESTABLISHED")
        quotes = [e["quote"] for e in unmet["evidence"]]
        self.assertTrue(quotes and all("supersede" in q.lower() for q in quotes))
        raw = {x["record_id"]: x for x in load_records()}["row-0003"]["raw"]
        for e in unmet["evidence"]:
            self.assertEqual(raw[e["field"]][e["start"]:e["end"]], e["quote"])  # exact span

    def test_answerable_question_still_returns_evidence(self):
        r = self.engine.search("What actions does FAA-2026-11814 propose?")
        self.assertEqual(r["status"], "EVIDENCE_FOUND")
        self.assertEqual(r["answerability"]["unmet_requests"], [])

    def test_withdrawn_status_question_is_answerable(self):
        r = self.engine.search("FAA-2024-1884 has Status Current. Is this record evidence of an active proposal?")
        self.assertEqual(r["status"], "EVIDENCE_FOUND")
        self.assertEqual(r["results"][0]["configuration_check"]["document_kind"], "withdrawal_notice")


class DecisionsApplied(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = {r["record_id"]: parse_record(r) for r in load_records()}

    def test_manufacturer_optional_but_recorded(self):
        out = evaluate(self.parsed["row-0383"], {"model": "EMB-120", "serial_number": "120006"})
        self.assertEqual(out["scope_decision"], "MATCH")
        make = next(c for c in out["checks"] if c["field"] == "make")
        self.assertEqual(make["result"], "NOT_PROVIDED")

    def test_wrong_manufacturer_still_stops(self):
        out = evaluate(self.parsed["row-0383"], {"make": "Boeing", "model": "EMB-120", "serial_number": "120006"})
        self.assertEqual(out["scope_decision"], "UNKNOWN")
        self.assertIn("MAKE_MISMATCH_OR_UNREGISTERED_ALIAS", out["reason_codes"])

    def test_model_still_required(self):
        out = evaluate(self.parsed["row-0383"], {"serial_number": "120006"})
        self.assertEqual(out["missing_fields"], ["model"])
        self.assertEqual(out["scope_decision"], "UNKNOWN")

    def test_model_not_listed_stays_unknown(self):
        # Decision 3: no inference about whether "series" covers other models.
        out = evaluate(self.parsed["row-0383"], {"model": "EMB-145", "serial_number": "120006"})
        self.assertEqual(out["scope_decision"], "UNKNOWN")

    def test_historical_off_by_default_and_labeled_when_on(self):
        cfg = {"model": "EMB-120"}
        off = evaluate(self.parsed["row-0431"], cfg)
        self.assertEqual(off["scope_decision"], "UNKNOWN")
        self.assertIn("NOT_LATEST_OBSERVED_RECORD_IN_CSV", off["reason_codes"])
        on = evaluate(self.parsed["row-0431"], cfg, allow_historical=True)
        self.assertEqual(on["scope_decision"], "MATCH")
        self.assertEqual(on["status"], "HISTORICAL_TEXT_SCOPE_ONLY")
        self.assertTrue(on["historical_text_only"])
        self.assertEqual(on["legal_applicability"], "UNVERIFIED")

    def test_historical_mode_does_not_unlock_withdrawal_notices(self):
        out = evaluate(self.parsed["row-0367"], {"model": "A320-211"}, allow_historical=True)
        self.assertEqual(out["scope_decision"], "UNKNOWN")
        self.assertIn("DOCUMENT_KIND_NOT_CONFIRMED_PROPOSAL", out["reason_codes"])


if __name__ == "__main__":
    unittest.main()
