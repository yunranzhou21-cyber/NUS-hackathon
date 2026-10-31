import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from web_app import Handler


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, value=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        body = json.dumps(value).encode() if value is not None else None
        request = Request(self.base + path, data=body, headers=headers)
        try:
            return urlopen(request, timeout=10)
        except HTTPError as error:
            return error

    def test_page_and_assets(self):
        for path in ('/', '/app.js', '/style.css', '/favicon.svg'):
            with self.request(path) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("default-src 'self'", response.headers['Content-Security-Policy'])
                self.assertTrue(response.read())

    def test_english_interface(self):
        import re
        for path in ('/', '/app.js'):
            with self.request(path) as response:
                text = response.read().decode('utf-8')
                self.assertIsNone(re.search(r'[\u3400-\u9fff]', text))
                if path == '/':
                    self.assertIn('lang="en"', text)

    def test_files_not_exposed(self):
        for path in ('/search.py', '/.git/config', '/../prepare_data.py'):
            with self.request(path) as response:
                self.assertEqual(response.status, 404)

    def test_real_query(self):
        with self.request('/api/search', {'query': 'EMB-120 serial number 120006',
                                         'configuration': {'make': 'Embraer'}}) as response:
            self.assertEqual(response.status, 200)
            value = json.load(response)
            item = next(r for r in value['results'] if r['record_id'] == 'row-0383')
            self.assertEqual(item['configuration_check']['scope_decision'], 'MATCH')
            self.assertEqual(value['operational_answer'], 'REFUSE')

    def test_invalid_requests(self):
        for value in ([], {'query': 123}, {'query': '777', 'limit': True},
                      {'query': '777', 'configuration': {'make': []}},
                      {'query': '777', 'include_history': 'false'}):
            with self.request('/api/search', value) as response:
                self.assertEqual(response.status, 400)

    def test_msn_and_bulletin_conditions_through_api(self):
        examples = [
            ('2002-NM-224-AD', {'model': 'A320-211', 'manufacturer_serial_number': '1516'}, 'row-0375', 'MATCH'),
            ('2002-NM-224-AD', {'model': 'A320-211', 'manufacturer_serial_number': '1624'}, 'row-0375', 'NO_MATCH'),
            ('2004-SW-16-AD', {'model': '600N', 'serial_number': 'RN066',
                               'TB600N_007_2004_01_12_completed': False}, 'row-0350', 'MATCH'),
            ('2004-SW-16-AD', {'model': '600N', 'serial_number': 'RN066',
                               'TB600N_007_2004_01_12_completed': True}, 'row-0350', 'NO_MATCH'),
            ('2004-SW-16-AD', {'model': '600N', 'serial_number': 'RN066',
                               'TB600N_007_2004_01_13_completed': False}, 'row-0350', 'UNKNOWN'),
        ]
        for query, configuration, rid, expected in examples:
            with self.subTest(configuration=configuration):
                with self.request('/api/search', {'query': query, 'configuration': configuration,
                                                  'historical_comparison': True}) as response:
                    self.assertEqual(response.status, 200)
                    data = json.load(response)
                    check = next(r['configuration_check'] for r in data['results'] if r['record_id'] == rid)
                    self.assertEqual(check['scope_decision'], expected)
                    self.assertEqual(check['operational_answer'], 'REFUSE')
                    self.assertEqual(check['legal_applicability'], 'UNVERIFIED')
                    if expected != 'UNKNOWN':
                        self.assertTrue(check['historical_text_only'])

    def test_condition_inputs_validate_types_and_keep_unknown_unknown(self):
        for config in ({'TB600N_007_2004_01_12_completed': 'false'},
                       {'TB600N_007_2004_02_31_completed': False},
                       {'external_conditions': []},
                       {'external_conditions': {'condition': 'maybe'}},
                       {'manufacturer_serial_number': True}):
            with self.subTest(configuration=config):
                with self.request('/api/search', {'query': '600N', 'configuration': config}) as response:
                    self.assertEqual(response.status, 400)
        with self.request('/api/search', {'query': '2004-SW-16-AD',
                          'configuration': {'model': '600N', 'serial_number': 'RN066'},
                          'historical_comparison': True}) as response:
            data = json.load(response)
            check = next(r['configuration_check'] for r in data['results'] if r['record_id'] == 'row-0350')
            condition = check['required_supplements'][0]
            self.assertEqual(check['scope_decision'], 'UNKNOWN')
        with self.request('/api/search', {'query': '2004-SW-16-AD',
                          'configuration': {'model': '600N', 'serial_number': 'RN066',
                                            'external_conditions': {condition['id']: False}},
                          'historical_comparison': True}) as response:
            data = json.load(response)
            check = next(r['configuration_check'] for r in data['results'] if r['record_id'] == 'row-0350')
            self.assertEqual(check['scope_decision'], 'MATCH')
            self.assertIn('RELIES_ON_USER_ATTESTED_EXTERNAL_CONDITION', check['reason_codes'])

    def test_cross_origin_denied(self):
        with self.request('/api/search', {'query': '777'}, 'https://example.com') as response:
            self.assertEqual(response.status, 403)

    def test_no_evidence(self):
        with self.request('/api/search', {'query': 'zzzzUnlistedModel999'}) as response:
            value = json.load(response)
            self.assertEqual(value['status'], 'REFUSE')
            self.assertEqual(value['results'], [])


if __name__ == '__main__':
    unittest.main()
