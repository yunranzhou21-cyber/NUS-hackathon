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
