import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import query_understanding as qu
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

    def test_status_endpoint_reports_configured(self):
        with mock.patch("web_app.get_provider", return_value=model()):
            with urlopen(self.base + "/api/status") as r:
                self.assertTrue(json.load(r)["ai_configured"])


SECRET = "sk-test-SECRET-1234567890"


def fake_chat(content):
    import io
    body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
    return lambda request, timeout=None: io.BytesIO(body)


GOOD = json.dumps({"intent": "check_proposed_scope", "model": "EMB-120", "serial_number": "100"})


class SettingsTests(unittest.TestCase):
    def test_validation(self):
        ok, err = qu.validate_settings(SECRET, "m-1", "https://api.example.com/v1/")
        self.assertIsNone(err)
        self.assertEqual(ok["base_url"], "https://api.example.com/v1")
        for args in [("short", "m", ""), ("has space in key", "m", ""), (SECRET, "bad model!", ""),
                     (SECRET, "m", "http://api.example.com/v1"),     # http only for loopback
                     (SECRET, "m", "https://user:pw@api.example.com"),
                     (SECRET, "m", "https://api.example.com/v1?x=1"),
                     (SECRET, "m", "ftp://api.example.com"), (None, "m", ""), (SECRET, "m", "javascript:alert(1)")]:
            self.assertIsNotNone(qu.validate_settings(*args)[1], args)
        self.assertIsNone(qu.validate_settings(SECRET, "m", "http://localhost:11434/v1")[1])

    def test_connection_results(self):
        provider = qu.provider_for({"api_key": SECRET, "model": "m", "base_url": "https://x.test/v1"})
        with mock.patch("query_understanding.urllib.request.urlopen", fake_chat(GOOD)):
            self.assertEqual(qu.test_connection(provider), (True, None))
        with mock.patch("query_understanding.urllib.request.urlopen", fake_chat("hello")):
            self.assertEqual(qu.test_connection(provider), (False, "MODEL_OUTPUT_UNUSABLE"))
        err = qu.urllib.error.HTTPError("u", 401, "no", {}, None)
        with mock.patch("query_understanding.urllib.request.urlopen", side_effect=err):
            self.assertEqual(qu.test_connection(provider), (False, "AUTH_REJECTED"))
        with mock.patch("query_understanding.urllib.request.urlopen", side_effect=OSError()):
            self.assertEqual(qu.test_connection(provider), (False, "UNREACHABLE"))


class ConfigApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), web_app.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        qu.clear_runtime()
        self.env = mock.patch.dict("os.environ", {}, clear=False)
        self.env.start()
        os.environ.pop("AI_API_KEY", None)

    def tearDown(self):
        qu.clear_runtime()
        self.env.stop()

    def post(self, path, value, headers=None):
        req = Request(self.base + path, json.dumps(value).encode(),
                      {"Content-Type": "application/json", **(headers or {})})
        try:
            with urlopen(req, timeout=10) as r:
                return r.status, r.read().decode()
        except HTTPError as e:
            return e.code, e.read().decode()

    def status(self):
        with urlopen(self.base + "/api/status") as r:
            return r.read().decode()

    def test_connect_use_disconnect_never_leaks_key(self):
        transcript = [self.status()]
        with mock.patch("query_understanding.urllib.request.urlopen", fake_chat(GOOD)):
            code, body = self.post("/api/ai-config", {"action": "connect", "api_key": SECRET, "model": "m1"})
            transcript += [body, self.status()]
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(body)["test"], "ok")
            self.assertTrue(json.loads(body)["ai_configured"])  # page reads this name
            self.assertTrue(json.loads(self.status())["ai_configured"])
            code, body = self.post("/api/search", {"query": "Embraer EMB-120 serial number 100", "use_ai": True})
            transcript.append(body)
            self.assertEqual(json.loads(body)["interpretation"]["source"], "llm_validated")
            self.assertTrue(json.loads(body)["interpretation"]["sent_to_external_service"])
        code, body = self.post("/api/ai-config", {"action": "disconnect"})
        transcript.append(body)
        self.assertFalse(json.loads(self.status())["ai_configured"])
        self.assertTrue(all(SECRET not in t for t in transcript))

    def test_failed_test_is_not_saved(self):
        err = qu.urllib.error.HTTPError("u", 401, "no", {}, None)
        with mock.patch("query_understanding.urllib.request.urlopen", side_effect=err):
            code, body = self.post("/api/ai-config", {"action": "connect", "api_key": SECRET})
        self.assertEqual(json.loads(body)["test"], "failed")
        self.assertEqual(json.loads(body)["reason"], "AUTH_REJECTED")
        self.assertFalse(json.loads(self.status())["ai_configured"])
        self.assertNotIn(SECRET, body)

    def test_bad_input_rejected_without_echo(self):
        for bad in ({"action": "connect", "api_key": "x"},
                    {"action": "connect", "api_key": SECRET, "base_url": "http://evil.example.com"},
                    {"action": "launch"}, []):
            code, body = self.post("/api/ai-config", bad)
            self.assertEqual(code, 400)
            self.assertNotIn(SECRET, body)

    def test_cross_origin_cannot_set_key(self):
        code, _ = self.post("/api/ai-config", {"action": "connect", "api_key": SECRET},
                            {"Origin": "https://evil.example.com"})
        self.assertEqual(code, 403)
        self.assertFalse(json.loads(self.status())["ai_configured"])

    def test_key_not_written_to_disk(self):
        with mock.patch("query_understanding.urllib.request.urlopen", fake_chat(GOOD)):
            self.post("/api/ai-config", {"action": "connect", "api_key": SECRET})
        import pathlib
        root = pathlib.Path(__file__).parent
        for path in root.rglob("*"):
            if path.is_file() and ".git" not in path.parts and path.suffix in {".py", ".json", ".jsonl", ".md", ".csv", ".sqlite3", ".js", ".html"}:
                if path.name == "test_query_understanding.py":
                    continue
                self.assertNotIn(SECRET.encode(), path.read_bytes(), str(path))


if __name__ == "__main__":
    unittest.main()
