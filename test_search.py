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

    def test_keyword_route_hides_older_records_by_default(self):
        default = self.engine.search('withdrawal', limit=20)
        self.assertEqual(default['route'], 'KEYWORD_COVERAGE')
        self.assertTrue(default['results'])
        self.assertTrue(all(r['revision']['this_is_latest'] for r in default['results']))
        history = self.engine.search('withdrawal', limit=20, include_history=True)
        self.assertGreaterEqual(len(history['results']), len(default['results']))

    def test_named_docket_shows_every_version_newest_first(self):
        result = self.engine.search('2000-NM-120-AD', limit=20)
        self.assertEqual(result['route'], 'DOCKET_LOOKUP')
        ids = [r['record_id'] for r in result['results']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreater(len(ids), 1)
        latest_flags = [r['revision']['this_is_latest'] for r in result['results']]
        self.assertTrue(latest_flags[0])  # newest first
        for r in result['results']:
            self.assertEqual(r['revision']['versions_in_csv'], len(ids))
            self.assertEqual(r['revision']['versions'][0]['is_latest'], True)

    def test_older_version_is_shown_but_not_compared(self):
        result = self.engine.search('2002-NM-224-AD', limit=20)
        by_id = {r['record_id']: r for r in result['results']}
        self.assertEqual(set(by_id), {'row-0367', 'row-0375'})
        self.assertEqual(by_id['row-0367']['configuration_check']['document_kind'], 'withdrawal_notice')
        self.assertFalse(by_id['row-0375']['revision']['this_is_latest'])
        self.assertIn('NOT_LATEST_OBSERVED_RECORD_IN_CSV',
                      by_id['row-0375']['configuration_check']['reason_codes'])

    def test_unknown_docket_refused_never_substituted(self):
        result = self.engine.search('FAA-2003-99999')
        self.assertEqual(result['status'], 'REFUSE')
        self.assertEqual(result['reason_codes'], ['IDENTIFIER_NOT_FOUND'])
        self.assertEqual(result['results'], [])
        # A near miss is offered as a suggestion, never silently used.
        near = self.engine.search('FAA-2025-0199')
        self.assertEqual(near['results'], [])
        self.assertIn('FAA-2025-0199_S', near['unknown_identifiers'][0]['did_you_mean'])

    def test_named_docket_not_hidden_by_wrong_model(self):
        result = self.engine.search('Does an EMB-145 with serial 120006 match FAA-2003-16645?',
                                    {'model': 'EMB-145'})
        item = next(r for r in result['results'] if r['record_id'] == 'row-0383')
        self.assertNotEqual(item['configuration_check']['scope_decision'], 'MATCH')

    def test_ad_reference_is_required(self):
        found = self.engine.search('AD 2025-09-07')
        self.assertIn('row-0003', [r['record_id'] for r in found['results']])
        self.assertEqual(self.engine.search('AD 1999-01-01')['results'], [])

    def test_coverage_threshold_refuses_unrelated_questions(self):
        for q in ('banana smoothie recipe', 'how do I bake sourdough bread at home'):
            self.assertEqual(self.engine.search(q)['results'], [], q)

    def test_natural_question_found_by_coverage_not_exact_words(self):
        q = ('Find the proposal about loss of tightening torque between an upper ball '
             'bearing end and a main rotor servo-control. What actions does it propose?')
        result = self.engine.search(q)
        self.assertEqual(result['route'], 'KEYWORD_COVERAGE')
        self.assertEqual(result['results'][0]['record_id'], 'row-0001')
        match = result['results'][0]['match']
        self.assertGreaterEqual(match['term_coverage'], 0.6)
        self.assertTrue(match['matched_terms'])

    def test_stemming(self):
        # "proposed" and "proposes" share a stem, so an inflected word still matches.
        a = self.engine.search('proposes', limit=20)
        b = self.engine.search('proposed', limit=20)
        self.assertTrue(a['results'])
        self.assertEqual([r['record_id'] for r in a['results']],
                         [r['record_id'] for r in b['results']])

    def test_identifier_extraction_forms(self):
        known = ['EMB-120']
        for text, serial in [('serial number is 95', '95'), ('serial 120004', '120004'),
                             ('S/N: 120006', '120006'), ('serial numbers RN068', 'RN068')]:
            plan, err = query_plan(f'EMB-120 {text}', {}, known)
            self.assertIsNone(err, text)
            self.assertEqual(plan['configuration'].get('serial_number'), serial, text)
        # Words that merely contain "line" or "serial" must not become identifiers.
        plan, _ = query_plan('EMB-120 airline 767 serialized parts', {}, known)
        self.assertNotIn('line_number', plan['configuration'])
        self.assertNotIn('serial_number', plan['configuration'])
        plan, _ = query_plan('EMB-120 line number 5', {}, known)
        self.assertEqual(plan['configuration']['line_number'], '5')

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
