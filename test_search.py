import tempfile
import unittest
from pathlib import Path

from search import SearchEngine, build_index, load_records, query_plan


class QueryTests(unittest.TestCase):
    def test_chinese_config(self):
        plan, error = query_plan('EMB-120 序列号 120006 有哪些相关提案', {}, ['EMB-120'])
        self.assertIsNone(error)
        self.assertEqual(plan['configuration'], {'model': 'EMB-120', 'serial_number': '120006'})
        self.assertEqual(plan['fts_expression'], '"EMB-120"')

    def test_conflict(self):
        self.assertEqual(query_plan('EMB-120 serial number 100', {'serial_number': '200'}, ['EMB-120'])[1],
                         'QUERY_CONFIGURATION_CONFLICT')
        self.assertEqual(query_plan('EMB-120 and 777', {}, ['EMB-120', '777'])[1],
                         'MULTIPLE_MODELS_NEED_CLARIFICATION')

    def test_unsupported_chinese_not_silently_dropped(self):
        self.assertEqual(query_plan('EMB-120 起落架问题', {}, ['EMB-120'])[1],
                         'UNSUPPORTED_QUERY_LANGUAGE_OR_TERMS')


class SearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.database = str(Path(cls.temp.name) / 'index.sqlite3')
        cls.stats = build_index(database=cls.database)
        cls.engine = SearchEngine(database=cls.database)
        cls.records = {r['record_id']: r for r in load_records()}

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()
        cls.temp.cleanup()

    def test_real_serial_query(self):
        result = self.engine.search('EMB-120 序列号 120006 有哪些相关提案', {'make': 'Embraer'}, limit=20)
        self.assertEqual(result['status'], 'EVIDENCE_FOUND')
        by_id = {r['record_id']: r for r in result['results']}
        self.assertIn('row-0383', by_id)
        self.assertEqual(by_id['row-0383']['configuration_check']['scope_decision'], 'MATCH')
        self.assertTrue(all(self.records[rid]['normalized']['make'] == 'Embraer' for rid in by_id))
        self.assertTrue(all(r['configuration_check']['operational_answer'] == 'REFUSE'
                            for r in result['results']))

    def test_range_gap(self):
        result = self.engine.search('EMB-120 serial number 120005', {'make': 'Embraer'}, limit=20)
        item = next(r for r in result['results'] if r['record_id'] == 'row-0383')
        self.assertEqual(item['configuration_check']['scope_decision'], 'NO_MATCH')

    def test_exact_citations_and_dedup(self):
        result = self.engine.search('corrosion', limit=20, include_history=True)
        self.assertTrue(result['results'])
        ids = [r['record_id'] for r in result['results']]
        self.assertEqual(len(ids), len(set(ids)))
        for item in result['results']:
            raw = self.records[item['record_id']]['raw']
            for ev in item['evidence'] + [item['title']['evidence']]:
                self.assertEqual(raw[ev['field']][ev['start']:ev['end']], ev['quote'])

    def test_history_default(self):
        result = self.engine.search('2000-NM-120-AD', limit=20)
        self.assertTrue(result['results'])
        self.assertTrue(all(self.records[r['record_id']]['dataset_history']['is_latest_observed']
                            for r in result['results']))
        history = self.engine.search('2000-NM-120-AD', limit=20, include_history=True)
        self.assertGreater(len(history['results']), len(result['results']))

    def test_empty_no_evidence_invalid_limits(self):
        self.assertEqual(self.engine.search('zzzzUnlistedModel999')['results'], [])
        self.assertEqual(self.engine.search('')['status'], 'NEEDS_CLARIFICATION')
        self.assertEqual(self.engine.search('777', limit=99)['status'], 'REFUSE')
        self.assertEqual(self.engine.search('777', {'make': 'Not a real manufacturer'})['results'], [])

    def test_literal_fts_input(self):
        for query in ('" OR * --', 'NEAR(foo bar)', "'; DROP TABLE documents; --"):
            self.engine.search(query)
        self.assertEqual(self.engine.connection.execute('SELECT count(*) FROM documents').fetchone()[0], 431)

    def test_missing_index_actionable(self):
        with self.assertRaisesRegex(ValueError, 'index missing'):
            SearchEngine(database=str(Path(self.temp.name) / 'missing.sqlite3'))

    def test_stale_index_refused(self):
        with self.assertRaisesRegex(ValueError, 'index stale'):
            SearchEngine(input_path='search.py', database=self.database)


if __name__ == '__main__':
    unittest.main()
