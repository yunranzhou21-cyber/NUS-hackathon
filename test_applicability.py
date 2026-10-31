import unittest

from applicability import (
    evaluate, load_records, number_set, parse_block, parse_models, parse_record,
)


def fixture(body):
    return parse_block({"body": body, "boundary_found": True,
                        "evidence": {"quote": body}})


class GrammarTests(unittest.TestCase):
    def test_shorthand(self):
        self.assertEqual(parse_models("737-100, -200, and -200C"),
                         ["737-100", "737-200", "737-200C"])
        self.assertIsNone(parse_models("-200"))

    def test_complete_number_union(self):
        self.assertEqual(number_set("120004, and 120006 through 120352 inclusive"),
                         [[120004, 120004], [120006, 120352]])
        self.assertIsNone(number_set("100 through 200 except modified aircraft"))
        self.assertIsNone(number_set("200 through 100"))

    def test_numeric_exceptions(self):
        rule, error = fixture(
            "Model Falcon 2000 series airplanes, serial numbers 2 through 132, "
            "except serial numbers 123, 130, and 131; certificated in any category.")
        self.assertIsNone(error)
        self.assertEqual(rule["identifier_predicate"]["exclude_ranges_inclusive"],
                         [[123, 123], [130, 130], [131, 131]])

    def test_unconsumed_conditions_refused(self):
        for body in (
            "Model 777-200 airplanes equipped with General Electric engines.",
            "Model EMB-145 series airplanes as listed in Service Bulletin 123.",
            "Model R22 helicopters, serial numbers RN003 through RN066.",
            "Model Falcon 2000 airplanes, serial numbers 2 through 132; "
            "excluding those airplanes modified per Service Bulletin 123.",
        ):
            with self.subTest(body=body):
                self.assertIsNone(fixture(body)[0])

    def test_unconfirmed_boundary_refused(self):
        rule, error = parse_block({"body": "All Model 777 airplanes.",
                                  "boundary_found": False})
        self.assertIsNone(rule)
        self.assertEqual(error, "UNCONFIRMED_BLOCK_BOUNDARY")


class RealDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = {r["record_id"]: parse_record(r) for r in load_records()}

    def check(self, rid, allow_historical=False, **configuration):
        item = self.parsed[rid]
        return evaluate(item, {"make": item["make"], **configuration}, allow_historical)

    def test_real_emb_serial_union(self):
        item = self.parsed["row-0383"]
        self.assertEqual(item["rule"]["models"], ["EMB-120"])
        self.assertEqual(item["rule"]["identifier_predicate"]["include_ranges_inclusive"],
                         [[120004, 120004], [120006, 120352]])
        for serial, expected in [
            ("120003", "NO_MATCH"), ("120004", "MATCH"),
            ("120005", "NO_MATCH"), ("120006", "MATCH"),
            ("120352", "MATCH"), ("120353", "NO_MATCH"),
        ]:
            with self.subTest(serial=serial):
                result = self.check("row-0383", model="EMB-120", serial_number=serial)
                self.assertEqual(result["scope_decision"], expected)
                self.assertEqual(result["operational_answer"], "REFUSE")
                self.assertEqual(result["legal_applicability"], "UNVERIFIED")

    def test_real_line_number_boundaries(self):
        for line, expected in [("0", "NO_MATCH"), ("1", "MATCH"),
                               ("95", "MATCH"), ("96", "NO_MATCH")]:
            result = self.check("row-0384", model="757-200", line_number=line)
            self.assertEqual(result["scope_decision"], expected)
        result = self.check("row-0384", model="757-200", serial_number="10")
        self.assertEqual(result["scope_decision"], "UNKNOWN")
        self.assertEqual(result["missing_fields"], ["line_number"])

    def test_missing_or_prefixed_serial(self):
        for config in ({}, {"serial_number": "S-120004"}):
            result = self.check("row-0383", model="EMB-120", **config)
            self.assertEqual(result["status"], "NEEDS_CLARIFICATION")
            self.assertEqual(result["scope_decision"], "UNKNOWN")

    def test_complex_real_conditions_refused(self):
        for rid in ("row-0269", "row-0292", "row-0370", "row-0420"):
            with self.subTest(record=rid):
                self.assertIsNone(self.parsed[rid]["rule"])
                self.assertEqual(self.check(rid, model="777-200")["scope_decision"],
                                 "UNKNOWN")

    def test_msn_table_boundaries_and_exceptions(self):
        for model, msn, expected in [('A320-211', '1515', 'NO_MATCH'),
                                     ('A320-211', '1516', 'MATCH'),
                                     ('A320-211', '1624', 'NO_MATCH'),
                                     ('A320-211', '1754', 'MATCH'),
                                     ('A320-211', '1755', 'NO_MATCH'),
                                     ('A321-211', '1572', 'MATCH'),
                                     ('A321-211', '1675', 'NO_MATCH')]:
            with self.subTest(model=model, msn=msn):
                result = self.check('row-0375', allow_historical=True, model=model, manufacturer_serial_number=msn)
                self.assertEqual(result['scope_decision'], expected)
        missing = self.check('row-0375', allow_historical=True, model='A320-211', line_number='1516')
        self.assertEqual(missing['scope_decision'], 'UNKNOWN')
        self.assertIn('manufacturer_serial_number', missing['missing_fields'])

    def test_prefixed_serial_and_explicit_false_attestation(self):
        field = 'TB600N_007_2004_01_12_completed'
        for serial, done, expected in [('RN003', False, 'MATCH'),
                                       ('RN066', False, 'MATCH'),
                                       ('RN067', False, 'NO_MATCH'),
                                       ('RN068', False, 'MATCH'),
                                       ('RN066', True, 'NO_MATCH'),
                                       ('066', False, 'UNKNOWN')]:
            with self.subTest(serial=serial, done=done):
                result = self.check('row-0350', allow_historical=True, model='600N', serial_number=serial, **{field: done})
                self.assertEqual(result['scope_decision'], expected)
        unknown = self.check('row-0350', allow_historical=True, model='600N', serial_number='RN066')
        self.assertEqual(unknown['scope_decision'], 'UNKNOWN')
        self.assertTrue(unknown['required_supplements'])

    def test_external_condition_dates_and_conflicting_answers(self):
        cfg = {'model': '600N', 'serial_number': 'RN066', 'allow_historical': True}
        wrong_date = self.check('row-0350', **cfg, TB600N_007_2004_01_13_completed=False)
        self.assertEqual(wrong_date['scope_decision'], 'UNKNOWN')
        condition = self.parsed['row-0350']['rule']['external_conditions'][0]
        self.assertIn('@2004-01-12', condition['id'])
        conflict = self.check('row-0350', **cfg, TB600N_007_2004_01_12_completed=False,
                              external_conditions={condition['id']: True})
        self.assertEqual(conflict['scope_decision'], 'UNKNOWN')
        self.assertIn('EXTERNAL_CONDITION_ANSWERS_CONFLICT', conflict['reason_codes'])

    def test_listing_and_actions_remain_unknown_without_answers(self):
        for rid, model in [('row-0377', 'EMB-145'), ('row-0412', 'FALCON 2000')]:
            out = self.check(rid, allow_historical=True, model=model, serial_number='132')
            self.assertEqual(out['scope_decision'], 'UNKNOWN')
            self.assertIn('EXTERNAL_CONDITION_UNRESOLVED', out['reason_codes'])

    def test_no_automatic_series_membership(self):
        result = self.check("row-0414", model="777-200")
        self.assertEqual(result["scope_decision"], "UNKNOWN")

    def test_old_record_and_withdrawal_refused(self):
        result = self.check("row-0431", model="EMB-120")
        self.assertIn("NOT_LATEST_OBSERVED_RECORD_IN_CSV", result["reason_codes"])
        for item in self.parsed.values():
            if item["document_kind"] == "withdrawal_notice":
                self.assertEqual(evaluate(item, {})["status"], "REFUSE")

    def test_all_outputs_refuse_operational_advice(self):
        for item in self.parsed.values():
            result = evaluate(item, {"make": item["make"], "model": "777"})
            self.assertEqual(result["operational_answer"], "REFUSE")
            self.assertEqual(result["legal_applicability"], "UNVERIFIED")

    def test_source_offsets(self):
        for item in self.parsed.values():
            for block in item["applicability_blocks"]:
                self.assertTrue(block["evidence"]["quote"].startswith("Applicability:"))


if __name__ == "__main__":
    unittest.main()
