# Design decisions and the answerability check

Date: 2026-10-31

POC: Person B (answerability)

TL;DR: Three open questions were settled with the recommended option. A new answerability check says explicitly when a record was found but cannot answer the question. Development-set score went from 7/34 to 19/34 with 0 unsafe errors. That is a development score, not a measure of generalization.

## Decisions

1. **Manufacturer is optional when the model matches exactly.** The check list records `make: NOT_PROVIDED`. A manufacturer that is given and does not match still stops the comparison. The exact model token is still required. Revisit if two manufacturers ever share a model name; none does in this data.
2. **Superseded proposals are refused by default, with an explicit mode.** Setting `historical_comparison` (checkbox "Compare superseded proposals") compares the old proposal's text and labels the result `HISTORICAL_TEXT_SCOPE_ONLY`. Withdrawal notices are never compared, in either mode.
3. **A model that is not listed stays UNKNOWN, not NO_MATCH.** Whether "series" covers other models needs someone who knows the aircraft. Case T07 expects NO_MATCH and will keep failing until that is decided.

Labels worth rechecking: T12 to T26 expect MATCH or NO_MATCH against proposals that the CSV shows were later withdrawn. They stay unanswerable until the table, prefix and service-bulletin grammar exists, with or without the historical mode.

## Answerability

`answerability.py` runs after retrieval. It looks for five kinds of request the supplied data cannot satisfy: an exact page, a release or compliance decision, a specific technical value, a full procedure, and whether something is already in effect. If it finds one, the status becomes `INSUFFICIENT_EVIDENCE`, the matching record is still shown, and each unmet request gets an explanation. The "already superseded" case quotes the exact sentence that only proposes supersession.

It is pattern based and English only. A request kind that is not listed is not caught, so `NO_UNMET_REQUEST_DETECTED` does not mean the question was answered. To extend it, add a detector and two tests: a phrasing that must be flagged, and a near miss that must not.

## Honest limits

The detectors and decisions were written after seeing the 34 development cases. The tests use different phrasings, but only an unseen set can show real accuracy. Keep newly written cases away from whoever edits the code until the final run.

Run `python evaluate_cases.py` (add `--historical` for the explicit mode). Results are saved under `eval/`.
