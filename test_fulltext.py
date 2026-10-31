import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from applicability import load_records
from fulltext import FetchError, FullTextStore, checked_url, compare_document, evidence, parse_xml

XML = b'''<PRORULE><PRTPAGE P="100"/><HD>Applicability</HD>
<P>Model EMB-120 series airplanes, serial numbers 120006 through 120352 inclusive.</P>
<HD>Compliance</HD><P>Follow the stated requirements.<PRTPAGE P="101"/>Continued here.</P></PRORULE>'''


class FullTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = copy.deepcopy(next(r for r in load_records() if r['record_id'] == 'row-0383'))
        self.rec['source']['url'] = 'https://www.federalregister.gov/documents/2024/01/01/2024-00001/test'
        self.meta = {'document_number': '2024-00001', 'type': 'Proposed Rule',
                     'action': 'Notice of proposed rulemaking (NPRM).', 'citation': '89 FR 100',
                     'docket_ids': ['Docket No. FAA-2003-16645'], 'start_page': 100, 'end_page': 101,
                     'html_url': self.rec['source']['url'], 'publication_date': '2024-01-01',
                     'full_text_xml_url': 'https://www.federalregister.gov/documents/full_text/xml/2024/01/01/2024-00001.xml',
                     'pdf_url': 'https://www.govinfo.gov/content/pkg/FR-2024-01-01/pdf/2024-00001.pdf'}
        self.calls = []
        self.xml = XML
        self.store = FullTextStore([self.rec], self.tmp.name, fetch=self.fetch)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def fetch(self, url, limit):
        self.calls.append(url)
        if url.endswith('.json'):
            return json.dumps(self.meta).encode()
        if url.endswith('.pdf'):
            return b'%PDF-1.7\nfixture'
        return self.xml

    def ready(self, action='text', retry=False):
        state = self.store.request(self.rec['record_id'], action, retry)
        until = time.monotonic() + 5
        while state['status'] == 'FETCHING' and time.monotonic() < until:
            time.sleep(.01)
            state = self.store.request(self.rec['record_id'], action)
        self.assertNotEqual(state['status'], 'FETCHING')
        if state['status'] == 'READY':
            # READY cache may become visible just before the worker publishes its
            # final job state. Finish that tiny window before manipulating TTL.
            with self.store.document_locks['2024-00001']:
                pass
        return state

    def test_lazy_text_cache_reuse_and_explicit_pdf(self):
        self.assertEqual(self.store.request(self.rec['record_id'])['status'], 'NOT_CACHED')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.ready()['status'], 'READY')
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(list(Path(self.tmp.name).glob('*.pdf')))
        self.assertEqual(self.ready()['status'], 'READY')
        self.assertEqual(len(self.calls), 2)
        self.assertIn('pdf_url', self.ready('pdf'))
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.store.stats()['cached_documents'], 1)

    def test_fulltext_rule_and_precise_citation_round_trip(self):
        self.ready()
        result = self.store.result(self.rec['record_id'], {'model': 'EMB-120', 'serial_number': '120006'})
        self.assertEqual(result['configuration_check']['scope_decision'], 'MATCH')
        self.assertEqual(result['configuration_check']['basis'], 'FETCHED_FULL_TEXT')
        self.assertEqual(result['configuration_check']['operational_answer'], 'REFUSE')
        doc = self.store.cached('2024-00001')
        for ev in result['evidence'] + result['configuration_check']['evidence']:
            self.assertEqual(doc['text'][ev['start']:ev['end']], ev['quote'])
            self.assertIn('section', ev['source'])
            self.assertTrue(ev['source']['xml_sha256'])
        spanning = next(ev for ev in result['evidence'] if 'Continued' in ev['quote'])
        self.assertEqual(spanning['source']['pages'], [100, 101])

    def test_no_page_marker_means_no_invented_page(self):
        self.xml = XML.replace(b'<PRTPAGE P="100"/>', b'').replace(b'<PRTPAGE P="101"/>', b'')
        self.ready()
        result = self.store.result(self.rec['record_id'], {})
        self.assertTrue(all(not ev['source']['pages'] for ev in result['evidence']))

    def test_unsupported_or_unbounded_conditions_refused(self):
        for xml in [XML.replace(b' inclusive.', b' inclusive, except modified aircraft.'),
                    b'<PRORULE><HD>Applicability</HD><P>Model EMB-120 series airplanes.</P></PRORULE>',
                    XML.replace(b'Applicability', b'Summary')]:
            parsed = parse_xml(xml)
            parsed.update(metadata=self.meta, document_number='2024-00001', xml_sha256='fixture',
                          text_sha256='fixture', fetched_at='fixture')
            check = compare_document(parsed, self.rec, {'model': 'EMB-120', 'serial_number': '120006'})
            self.assertEqual(check['scope_decision'], 'UNKNOWN')

    def test_failed_download_and_retry(self):
        with patch.object(self.store, 'fetch', side_effect=OSError('offline')):
            self.assertEqual(self.ready()['status'], 'FAILED')
        self.assertEqual(self.ready(retry=True)['status'], 'READY')

    def test_document_and_docket_mismatch_rejected(self):
        self.meta['document_number'] = '2024-99999'
        self.assertEqual(self.ready()['error'], 'DOCUMENT_IDENTITY_MISMATCH')
        self.meta['document_number'] = '2024-00001'
        self.meta['docket_ids'] = ['FAA-OTHER']
        self.ready(retry=True)
        with self.assertRaisesRegex(FetchError, 'DOCKET_IDENTITY_MISMATCH'):
            self.store.result(self.rec['record_id'], {})

    def test_stale_cache_refetched_and_corrupt_cache_not_used(self):
        self.ready()
        self.store.ttl = -1
        self.assertEqual(self.store.request(self.rec['record_id'])['status'], 'STALE')
        # A stale result is never returned for comparison.
        self.assertIsNone(self.store.result(self.rec['record_id'], {}))
        self.store.ttl = 86400
        path = Path(self.tmp.name) / '2024-00001.json'
        doc = json.loads(path.read_text())
        doc['fetched_epoch'] -= 90000
        path.write_text(json.dumps(doc))
        self.assertEqual(self.store.request(self.rec['record_id'])['status'], 'STALE')
        self.assertEqual(self.ready()['status'], 'READY')
        self.assertEqual(len(self.calls), 4)
        doc = json.loads(path.read_text())
        doc['text'] += 'tampered'
        path.write_text(json.dumps(doc))
        self.assertIsNone(self.store.cached('2024-00001'))

    def test_unsafe_urls_xml_and_cache_limit(self):
        for url in ['http://www.federalregister.gov/x', 'https://example.com/x',
                    'https://www.federalregister.gov@localhost/x', 'https://www.federalregister.gov:8080/x']:
            with self.assertRaises(FetchError):
                checked_url(url)
        with self.assertRaises(FetchError):
            parse_xml(b'<!DOCTYPE x [<!ENTITY x "unsafe">]><x>&x;</x>')
        with self.assertRaises(FetchError):
            parse_xml(b'<html><body>Please sign in to view a document that is not XML.</body></html>')
        self.store.budget = 1
        self.assertEqual(self.ready()['error'], 'CACHE_LIMIT_REACHED')

    def test_nonblocking_deduplicated_jobs(self):
        gate = threading.Event()
        original = self.store.fetch
        def wait_fetch(url, limit):
            gate.wait(3)
            return original(url, limit)
        with patch.object(self.store, 'fetch', side_effect=wait_fetch):
            start = time.monotonic()
            self.assertEqual(self.store.request(self.rec['record_id'], 'text')['status'], 'FETCHING')
            self.assertLess(time.monotonic() - start, .5)
            self.assertEqual(self.store.request(self.rec['record_id'], 'text')['status'], 'FETCHING')
            gate.set()
            self.assertEqual(self.ready()['status'], 'READY')
        self.assertEqual(len(self.calls), 2)


if __name__ == '__main__':
    unittest.main()
