# Typed configuration and applicability follow-up

Date: 2026-10-31

## Behavior

- Manufacturer is optional. An explicitly supplied manufacturer is still checked;
  a conflict or unresolved alias stops the comparison. Models remain exact-match.
- MSN has its own `manufacturer_serial_number` field, including an input on the
  page. The source's explicitly labeled MSN table uses this identifier, not a
  line number. Query patterns `MSN 1516` and `manufacturer's serial number 1516`
  are recognized. Conflicting explicit MSN values require clarification.
- Boolean completion answers survive the evaluator, HTTP validation, search query
  planning and rule evaluation. `false` is an explicit answer, not a missing value.
- Bulletin answers are matched to the exact bulletin identifier, date and action
  meaning found in the source clause. Examples of supported input fields:
  `TB600N_007_2004_01_12_completed` and
  `F2000_A223_2001_10_17_actions_completed`. These are decoded from source metadata,
  not test IDs or expected outcomes. Wrong dates do not satisfy a condition.
- The page shows each source-backed external-condition question with Unknown,
  Yes and No choices and a Compare button. Unknown remains the default. The API
  also accepts `external_conditions` keyed by the returned condition ID. IDs now
  include the source bulletin date. User assertions are labeled
  `USER_ATTESTED_NOT_VERIFIED`; conflicting answers require clarification.
- Changing aircraft/query inputs clears page-held condition answers. No answer
  is inferred from a missing checkbox or obtained from a model's background knowledge.
- The historical comparison switch remains off by default. When explicitly on,
  old proposal text can be compared, with historical-only labels. Withdrawal and
  unconfirmed document types remain refused; no legal applicability is asserted.

## Validation

The same 34 supplied cases and the same scoring criteria are retained. The
evaluator now passes supported inputs instead of dropping MSN and completion
flags. Numeric identifiers in the case file are converted to text as before;
boolean flags retain their types.

| Mode | Passed | Safe failures | Unsafe failures observed |
| --- | --- | --- | --- |
| Default, historical comparisons disabled | 19/34 | 15/34 | 0/34 |
| Explicit historical-text comparison | 33/34 | 1/34 | 0/34 |

Default mode deliberately declines the older proposals used by many test cases.
The 97% figure requires historical mode and must not be presented as the default
or current-regulation accuracy. The supplied teammate version previously passed
11/34 in default mode; the pre-replacement working copy passed 19/34.

T07 remains a safe failure: the label expects NO_MATCH for a different model, but
the engine returns UNKNOWN when it cannot resolve exact model/series membership.
That conservative contract was retained instead of changing it for a test pass.

The full regression suite passes 105 tests. New coverage includes MSN range
boundaries and exceptions, prefixed serials, false versus missing answers,
wrong bulletin dates, conflicting condition answers, API type validation, and
end-to-end HTTP flow. Four obsolete grammar-refusal subtests were replaced with
these behavior checks rather than retaining assertions that supported rules
must be unparseable.

Browser verification used the updated demo on port 8013. For docket
2004-SW-16-AD, model 600N, serial RN066, with historical mode enabled: leaving the
bulletin answer unknown produced UNKNOWN; explicitly selecting No produced
MATCH with both historical-text-only and user-attestation notices.

Reports:

- `eval/results_configuration_v12.json`
- `eval/results_configuration_v12_historical.json`

Reports record the input SHA-256, parser version, mode, submitted configuration,
and rule-level reasons. Final report generation ran alongside regression tests;
its timings are observations under shared machine load, not a standalone latency
benchmark. No general zero-error or 1000-page performance claim is established.

Reproduce after rebuilding the index:

```powershell
python search.py build
python applicability.py build
python -m unittest discover -q
python evaluate_cases.py --out eval/results_configuration_v12.json
python evaluate_cases.py --historical --out eval/results_configuration_v12_historical.json
```

Implementation branch: `merge-rules-v1.1`.
