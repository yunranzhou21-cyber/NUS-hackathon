import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock
from urllib.request import Request, urlopen

from query_understanding import interpret, rules_interpret, validate
import web_app

Q = "Is my Embraer EMB-120 with serial number 120006 covered?"


def model(**kw):
    base = {"intent": "check_proposed_scope", "make": None, "model": None,
            "serial_number": None, "line_number": None}
    base.update(kw)
    return lambda query: json.dumps(base)


class ValidationTests(unittest.TestCase):
    def test_values_found_in_text_are_kept(self):
        r = interpret(Q, model(make="Embraer", model="EMB-120", serial_number="120006"))
        self.assertEqual(r["source"], "llm_validated")
        self.assertEqual(r["fields"]["make"], "Embraer")
        self.assertEqual(r["fields"]["model"], "EMB-120")

    def test_hallucinated_values_dropped(self):
        r = interpret(Q, model(make="Boeing", model="EMB-145", serial_number="999"))
        self.assertEqual(r["fields"], {"serial_number": "120006"})  # rules found it
        self.assertEqual({x["field"] for x in r["rejected"]}, {"make", "model", "serial_number"})
        self.assertTrue(all(x["reason"] == "NOT_FOUND_IN_USER_TEXT" for x in r["rejected"]))

    def test_rules_identifier_overrides_model(self):
        r = interpret("EMB-120 serial number 120006", model(serial_number="120006"))
        self.assertEqual(r["fields"]["serial_number"], "120006")

    def test_case_and_spacing_tolerated(self):
        self.assertEqual(validate(json.dumps({"intent": "search_documents", "model": "emb 120"}),
                                  "my EMB-120".replace("-", " "))[1], {"model": "emb 120"})

    def test_bad_outputs_fall_back(self):
        for raw in ("not json", "[]", json.dumps({"intent": "approve_maintenance"}),
                    json.dumps({"make": "x"})):
            r = interpret(Q, lambda q, raw=raw: raw)
            self.assertEqual(r["source"], "local_rules")
            self.assertEqual(r["notice"], "AI_OUTPUT_INVALID_USING_LOCAL_RULES")

    def test_provider_errors_fall_back(self):
        for exc in (OSError("down"), TimeoutError(), KeyError("choices")):
            def boom(q, exc=exc): raise exc
            r = interpret(Q, boom)
            self.assertEqual(r["source"], "local_rules")
            self.assertEqual(r["notice"], "AI_CALL_FAILED_USING_LOCAL_RULES")

    def test_code_fences_accepted(self):
        fenced = "```json\n" + json.dumps({"intent": "search_documents"}) + "\n```"
        self.assertEqual(interpret(Q, lambda q: fenced)["source"], "llm_validated")

    def test_local_rules_detect_only_indexed_makes(self):
        makes = ["Boeing", "Airbus", "Embraer", "Airbus Helicopters"]
        self.assertEqual(rules_interpret("Airbus Helicopters corrosion", makes)["fields"]["make"],
                         "Airbus Helicopters")
        self.assertEqual(rules_interpret("embraer EMB-120", makes)["fields"]["make"], "Embraer")
        self.assertNotIn("make", rules_interpret("Cessna 172 inspection", makes)["fields"])
        self.assertNotIn("make", rules_interpret("Boeingly speaking", makes)["fields"])

    def test_local_only_never_marked_external(self):
        r = interpret(Q)
        self.assertFalse(r["sent_to_external_service"])
        self.assertEqual(r["intent"], "check_proposed_scope")
        self.assertEqual(rules_interpret("show older versions")["intent"], "find_history")


class WebIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), web_app.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def post(self, value):
        req = Request(self.base + "/api/search", json.dumps(value).encode(),
                      {"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as r:
            return json.load(r)

    def test_ai_fields_flow_into_real_search(self):
        provider = model(make="Embraer", model="EMB-120", serial_number="120006")
        with mock.patch("web_app.get_provider", return_value=provider):
            data = self.post({"query": Q, "use_ai": True})
        self.assertEqual(data["interpretation"]["source"], "llm_validated")
        item = next(r for r in data["results"] if r["record_id"] == "row-0383")
        self.assertEqual(item["configuration_check"]["scope_decision"], "MATCH")
        self.assertEqual(data["operational_answer"], "REFUSE")

    def test_hallucinated_make_cannot_filter_results(self):
        provider = model(make="Boeing", model="EMB-120")
        with mock.patch("web_app.get_provider", return_value=provider):
            data = self.post({"query": "EMB-120 serial number 120006", "use_ai": True})
        self.assertNotIn("make", data["query_plan"]["configuration"])
        self.assertTrue(data["interpretation"]["rejected"])

    def test_conflict_with_form_requires_clarification(self):
        provider = model(model="EMB-120")
        with mock.patch("web_app.get_provider", return_value=provider):
            data = self.post({"query": "EMB-120 corrosion", "use_ai": True,
                              "configuration": {"model": "777"}})
        self.assertEqual(data["status"], "NEEDS_CLARIFICATION")
        self.assertEqual(data["results"], [])
        self.assertEqual(data["interpretation"]["conflict"], "model")

    def test_ai_requested_but_unconfigured(self):
        with mock.patch("web_app.get_provider", return_value=None):
            data = self.post({"query": "corrosion", "use_ai": True})
        self.assertEqual(data["interpretation"]["notice"], "AI_NOT_CONFIGURED_USING_LOCAL_RULES")
        self.assertEqual(data["interpretation"]["source"], "local_rules")
        self.assertTrue(data["results"])

    def test_history_intent_is_visible_and_applied(self):
        data = self.post({"query": "2000-NM-120-AD older versions"})
        self.assertEqual(data["interpretation"]["intent"], "find_history")
        self.assertTrue(data["history_included"])

    def test_status_endpoint_leaks_no_secret(self):
        with mock.patch("web_app.get_provider", return_value=model()):
            with urlopen(self.base + "/api/status") as r:
                self.assertEqual(json.load(r), {"ai_configured": True})


if __name__ == "__main__":
    unittest.main()
