import unittest
from prepare_data import (
    annotate_groups, classify, make_value, model_candidates, record,
    relations, validate,
)


def row(**kwargs):
    fields = (
        "Docket Number", "Summary", "Subject Heading", "Comments",
        "Link for Submitting Public Comment", "Model", "Make", "Product Type",
        "Product Subtype", "Issue Date", "Citation Publish Date",
        "Comments Due Date", "AD Number", "Page Number", "Citation", "Status",
    )
    value = dict.fromkeys(fields, "")
    value.update(kwargs)
    return value


def build(value, ordinal=1):
    return record(value, ordinal, 2, 2, "fixture.csv", "fixture-hash")


class PreparationTests(unittest.TestCase):
    def test_aliases_do_not_merge_subsidiaries(self):
        self.assertEqual(make_value("The Boeing Company"), "Boeing")
        self.assertEqual(make_value("Airbus SAS"), "Airbus")
        self.assertEqual(make_value("Airbus Helicopters"), "Airbus Helicopters")

    def test_old_scope_is_not_current_applicability(self):
        value = row(**{
            "Model": "A330-941",
            "Summary": "The FAA proposes to supersede AD 2025-09-07, which "
                       "applies to certain Airbus Model A330-243, -243F, -841, "
                       "and -941 airplanes. This proposed AD reduces the applicability."
        })
        rec = build(value)
        self.assertEqual([x["value"] for x in
                          rec["normalized"]["models_from_column"]], ["A330-941"])
        candidates = rec["normalized"]["model_candidates"]
        self.assertEqual([x["value"] for x in candidates], ["A330-243"])
        self.assertIn("APPLICABILITY_CHANGED", rec["safety"]["flags"])
        self.assertEqual(rec["safety"]["applicability_decision"], "UNKNOWN")
        self.assertEqual(relations(value)[0]["kind"], "proposed_supersession")
        validate([rec])

    def test_body_in_link_column_is_not_url(self):
        value = row(**{
            "Link for Submitting Public Comment":
                "Applicability: All Model 777 series airplanes.\n"
                "Compliance: Required as indicated.",
        })
        rec = build(value)
        self.assertIsNone(rec["source"]["url"])
        self.assertTrue(rec["extracted"]["proposed_text"])
        self.assertIn("777", [x["value"] for x in
                              rec["normalized"]["model_candidates"]])
        validate([rec])

    def test_withdrawal_overrides_current_metadata(self):
        value = row(**{
            "Status": "Current",
            "Summary": "This action withdraws a notice of proposed rulemaking "
                       "(NPRM). Accordingly, the proposed rule is withdrawn.",
        })
        self.assertEqual(classify(value)[0], "withdrawal_notice")
        self.assertIn("CURRENT_METADATA_DOES_NOT_MEAN_ACTIVE_PROPOSAL",
                      build(value)["safety"]["flags"])

    def test_serial_mention_is_not_executable_rule(self):
        rec = build(row(Summary="Certain serial numbers 1000 through 1500."))
        self.assertTrue(rec["extracted"]["serial_mentions"])
        self.assertEqual(rec["safety"]["executable_applicability_rules"], [])

    def test_history_preserves_ties_and_is_dataset_only(self):
        records = [
            build(row(**{"Docket Number": "FAA-2026-1234",
                         "Citation Publish Date": date}), i)
            for i, date in enumerate(
                ["01/01/2026", "02/01/2026", "02/01/2026"], 1)
        ]
        annotate_groups(records)
        history = records[0]["dataset_history"]
        self.assertEqual(history["latest_observed_record_ids"],
                         ["row-0002", "row-0003"])
        self.assertFalse(history["latest_legal_version_verified"])

    def test_unsupported_models_not_invented(self):
        self.assertEqual(model_candidates(row(Summary="Model UNKNOWN-999.")), [])

    def test_unicode_and_newlines_keep_exact_offsets(self):
        rec = build(row(Summary="é\nThe FAA proposes Model A320-214 airplanes."))
        self.assertGreater(validate([rec]), 0)

    def test_unknown_dates_remain_null(self):
        rec = build(row(**{"Issue Date": "invalid"}))
        self.assertIsNone(rec["normalized"]["issue_date"])
        self.assertIn("INVALID_DATE:Issue Date", rec["safety"]["flags"])

    def test_single_date_is_not_page_range(self):
        rec = build(row(**{"Page Number": "Page 1234"}))
        self.assertIsNone(rec["source"]["published_page_range"])


if __name__ == "__main__":
    unittest.main()
