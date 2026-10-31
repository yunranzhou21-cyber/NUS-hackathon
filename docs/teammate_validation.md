# Teammate applicability replacement validation

Date: 2026-10-31

Replaced `applicability.py` using the supplied Telegram download. The previous
working copy is backed up at `data/search/teammate_review/applicability.before.py`
(ignored by Git). Other pre-existing source edits were retained.

The supplied file cannot be used unchanged by the current application: search
imports `PARSER_VERSION` and passes a third argument to `evaluate`. Restored the
version constant (with a distinct cache version), used it in parsed records, and
retained the existing explicit historical-comparison option and historical labels.
The teammate's mandatory manufacturer check and parsing behavior are retained.
Rebuilt the search index and generated rule artifacts.

## Supplied cases, unchanged scoring

Input: `C:/Users/Luccass/Downloads/airbus_q4_test_cases.jsonl`

SHA-256: `87b05ad53854eded07775fb34710984b965f143dbbe3fbd9b66f45ce2f242fc1`

The input is byte-identical to `eval/airbus_q4_test_cases.jsonl`.

| End-to-end measurement | Before replacement | Integrated teammate file |
| --- | --- | --- |
| Cases passed | 19/34 | 11/34 |
| Safe failures (declined or missing answer) | 15/34 | 23/34 |
| Unsafe failures observed by this evaluator | 0/34 | 0/34 |
| Labeled record retrieved | 34/34 | 34/34 |
| Retrieval latency, mean | 306 ms | 364 ms |
| Retrieval latency, maximum | 346 ms | 452 ms |

The eight new failures are T01-T05 and T08-T10. The new mandatory `make` input
causes these serial/line-boundary cases to decline. Their supplied configurations
do not include a manufacturer. This is a behavior regression relative to the
current application's optional-manufacturer contract, not a wrong definitive
applicability decision.

The rule-only diagnostic remains 12/27 passing, with 15 safe failures and no
unsafe failures. This diagnostic supplies the labeled record and manufacturer;
it is not an end-to-end accuracy score.

Important evaluator limits: `evaluate_cases.py` drops
`manufacturer_serial_number` and bulletin-completion fields. It does not exercise
the complete new table/external-condition capability. Neither these cases nor
the observed zero unsafe errors establish zero hallucinations in general.
Latency runs were sequential on one machine; these results do not verify the
1000-page requirement or hosted-model behavior.

Detailed reports: `eval/results_before_teammate.json` and
`eval/results_teammate.json`.

## Existing regression suite

The integrated replacement ran all 96 tests without runtime errors, with seven
failed assertions/subtests:

- Three failures in `test_answerability.DecisionsApplied` arise from mandatory
  manufacturer input, including the historical example that omits it.
- Four subtests in `test_complex_real_conditions_refused` still expect records
  row-0350, row-0375, row-0377 and row-0412 to be unparseable. These also fail
  against the backed-up pre-replacement code: rule support was already expanded.

The focused baseline ran 19 tests with those same four pre-existing subtest
failures. A separate baseline full-suite attempt encountered stale-index HTTP
errors after the cache version changed and is not used for comparison.

Logs are under `data/search/teammate_review/`. Test expectations and case labels
were not changed to improve reported results.

## Follow-up needed

1. Decide whether manufacturer remains optional or update the form and input
   contract to require it; the current replacement requires it.
2. Carry explicitly identified MSN and external-condition answers through the
   API and evaluator without silently inferring missing aircraft facts.
3. Update obsolete grammar tests with boundary, exception and unknown-condition
   checks for the newly supported rules.

Changes are local on branch `merge-rules-v1.1`; no commit or push was made.
