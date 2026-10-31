import unittest

from configuration import normalize_configuration
from evaluate_cases import split_config
from search import query_plan


class ConfigurationTests(unittest.TestCase):
    def test_false_preserved_and_unrecognized_fields_reported(self):
        supplied = {'line_number': 1, 'TB600N_007_2004_01_12_completed': False,
                    'unrecognized': 'value'}
        used, dropped = split_config(supplied)
        self.assertIs(used['TB600N_007_2004_01_12_completed'], False)
        self.assertEqual(used['line_number'], '1')
        self.assertEqual(dropped, ['unrecognized'])

    def test_query_plan_keeps_nested_answers_and_msn_distinct(self):
        answer = {'MODIFIED_PER:TB600N-007@2004-01-12': False}
        plan, error = query_plan('A320-211 MSN 1516', {'external_conditions': answer}, ['A320-211'])
        self.assertIsNone(error)
        self.assertEqual(plan['configuration']['external_conditions'], answer)
        self.assertEqual(plan['configuration']['manufacturer_serial_number'], '1516')
        self.assertNotIn('serial_number', plan['configuration'])
        _, error = query_plan('A320-211 MSN 1516', {'manufacturer_serial_number': '1624'}, ['A320-211'])
        self.assertEqual(error, 'QUERY_CONFIGURATION_CONFLICT')

    def test_malformed_and_ambiguous_answers_rejected(self):
        for value in ['false', 0, None, [], {}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_configuration({'TB600N_007_2004_01_12_completed': value})


if __name__ == '__main__':
    unittest.main()
