# TraceAD

**Find technical evidence, inspect configuration conditions, and trace each decision back to the source.**

TraceAD is a local research prototype for Challenge #4: *How might AI help people find, understand, and verify regulated technical information?* It searches the supplied FAA dataset and returns structured source passages, configuration checks, version observations, and explicit uncertainty messages.

> **Proposal research only.** A match means the supplied configuration matches supported, quoted proposed text. It does not establish a currently effective Airworthiness Directive, legal applicability, compliance, or authorization to perform maintenance.

## Quick start

Requirements: Python 3 with SQLite FTS5 support and a browser. Development was tested on Python 3.13. The application uses the Python standard library; no `pip install` is required. AI is optional.

```powershell
git clone --branch merge-rules-v1.1 https://github.com/yunranzhou21-cyber/NUS-hackathon.git
cd NUS-hackathon
python search.py build
python web_app.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). If that port is occupied:

```powershell
python web_app.py --port 8001
```

The server binds to localhost. At startup, it rebuilds a missing or stale index. Rebuild and restart after changing source data or parser code. The index is generated locally and is excluded from Git.

## Features

### Interactive aircraft and autofill

A scalable aircraft schematic highlights the airframe, wings, engines or landing
gear when selected. The selected area offers a search for text mentions, not an
assertion that a document applies to that component. The illustration is generic,
not a model-specific drawing.

Use demo presets for EMB-120 serials, Boeing 757 line numbers or a historical A320
MSN case. Presets fill example values for review without immediately searching.
**Autofill from my question** extracts explicitly stated fields locally, keeps
conflicting existing inputs, and never invents a manufacturer or identifier.
Manufacturer and model inputs also suggest names already present in the index.

### On-demand official full text

Local candidates appear first. By default, the browser fetches XML for the top
three results in the background and reuses a 24-hour cache. Disable this option
for local-only searches. PDFs download only when explicitly requested. Fetched
source passages include chapters/sections, source hashes and printed page numbers
where XML page markers are present; unsupported conditions remain unknown.

The page reports downloaded document counts and cache size. The cache has a
50 MiB budget and is excluded from Git. See [on-demand fetching and API details](docs/on_demand_fulltext.md).

### Traceable evidence search

- **Docket lookup:** a known docket in the question retrieves its records, newest observation first. An unknown docket-shaped identifier is refused rather than silently replaced with a similar search.
- **Keyword retrieval:** SQLite FTS5, BM25 ranking, English stemming, and a minimum keyword-coverage threshold retrieve candidate passages.
- **Source inspection:** results include quoted text, docket, CSV record number, physical lines, field name, character offsets, publication metadata, and a source URL when available.
- **Structured output:** the web interface and JSON response expose evidence, configuration checks, revision observations, and reason codes separately. A search hit alone does not mean the question has been answered.

### Configuration-aware rule comparison

- Exact normalized model matching; no inferred model-family membership.
- Optional manufacturer input, with conflict checks when provided.
- Separate serial number, manufacturer serial number (**MSN**), and line number inputs.
- Supported inclusive ranges, unions, exclusions, explicitly prefixed serials, and model-specific MSN tables.
- Three-valued decisions: `MATCH`, `NO_MATCH`, or `UNKNOWN`. Missing or unsupported conditions prompt clarification instead of an invented answer.

The parser intentionally supports a limited grammar. It does not automatically turn arbitrary regulatory prose into verified rules.

### Bulletin conditions and explicit user answers

Some applicability clauses depend on a service bulletin, modification, or completed action. The page displays the quoted condition with **Unknown / Yes / No** choices and lets the user rerun the comparison.

Answers are bound to the cited condition and bulletin date. An explicit `false` is preserved; a missing answer remains unknown. User answers are marked `USER_ATTESTED_NOT_VERIFIED`, and conflicting answers require clarification. The application does not independently verify an aircraft's maintenance history.

### Historical records and uncertainty

The interface distinguishes two controls:

| Control | Purpose |
| --- | --- |
| Include historical records | Include older observations in keyword retrieval. Named docket lookup already returns its versions. |
| Compare superseded proposals | Explicitly allow comparison against older proposal text, with historical-only labels. Off by default. |

Version information describes observations within the supplied CSV. It is not a verified legal revision chain, and “latest in this CSV” does not mean “latest effective regulation.” Withdrawal or unconfirmed document types do not produce automatic proposal-scope comparisons.

The answerability checks also flag requests the records cannot support, including operational decisions, unavailable procedures or values, and exact-page requests unsupported by the data.

### Optional AI query understanding

AI helps interpret the question into limited structured fields. Deterministic code performs applicability comparisons; the model does not generate maintenance instructions or judge applicability.

Open **AI settings**, enter a key for an OpenAI-compatible endpoint, and select **Connect**. The server tests the connection before enabling AI. The key is held in server memory, cleared from the page after connection, and forgotten when the server stops or the user disconnects.

Only the question text is sent to the configured model endpoint, not the source documents. Returned field values must occur in the question text. Invalid output or provider failure falls back to local rules. Do not put credentials into questions. A real hosted model has not yet been validated; integration tests use stand-ins. See [AI setup and behavior](docs/ai_layer.md).

## Try the demo

| Goal | Query and inputs | Expected behavior |
| --- | --- | --- |
| Compare a serial boundary | `Find proposals for EMB-120 serial number 120006` | Inspect the EMB-120 proposed-scope match and its citations. |
| Check an excluded gap | Same query with serial `120005` | The supported EMB-120 serial rule returns `NO_MATCH`. |
| Inspect an MSN table | Query `2002-NM-224-AD`; model `A320-211`; MSN `1516`; enable historical comparison | Compare against the historical table and inspect range/exclusion evidence. |
| Resolve a bulletin condition | Query `2004-SW-16-AD`; model `600N`; serial `RN066`; enable historical comparison | Initially `UNKNOWN`; answering the cited modification question enables a conditional comparison. |
| See a refusal | Use the page's **Refuse when there is no evidence** example | Explicit inability to provide a reliable answer. |

## Tests and evaluation

```powershell
python -m unittest discover -q
python evaluate_cases.py --out eval/results_configuration_v12.json
python evaluate_cases.py --historical --out eval/results_configuration_v12_historical.json
```

The current suite passes **118 tests**, including autofill and on-demand fetch/cache/API checks. On the **34 supplied labeled cases**, with unchanged case labels and scoring criteria:

| End-to-end mode | Passed | Safe failures / declines | Unsafe errors observed |
| --- | --- | --- | --- |
| Default: historical comparison disabled | 19/34 (56%) | 15/34 | 0/34 |
| Historical comparison explicitly enabled | 33/34 (97%) | 1/34 | 0/34 |

**97% is the historical-text comparison result, not default-mode accuracy or accuracy on current regulations.** Many supplied cases reference older proposals that default mode deliberately declines. The remaining historical-mode failure, T07, expects a definite model mismatch while the engine conservatively returns `UNKNOWN`.

These 34 cases evaluate the offline CSV/rule path. They do not establish the accuracy of newly fetched full-text parsing.

All 34 labeled target records were retrieved in both modes. The evaluator also reports a separate rule-only diagnostic that supplies the target record and manufacturer; it is not the end-to-end score. Reports record the case-file hash, parser version, inputs, and rule-level reasons.

Zero unsafe errors on these cases does not prove zero hallucinations or general correctness. These cases have been used during development and are not an independent held-out evaluation. See [implementation and validation details](docs/configuration_v12.md), [default-mode results](eval/results_configuration_v12.json), and [historical-mode results](eval/results_configuration_v12_historical.json).

## Data and performance limits

- The lightweight corpus contains **431 CSV records and 1,552 indexed passages**. Official XML is fetched on demand; live cache counts are shown in the page. Arbitrary PDF ingestion and OCR are not implemented.
- CSV citations identify text spans with document-level page ranges. Fetched XML adds sections and explicit printed-page markers when available; absent markers are never replaced by guessed page numbers.
- Rule extraction has no expert approval or verified legal-effect status. Search results are limited and not exhaustive.
- Limited English/Chinese query patterns are supported; arbitrary natural-language understanding and semantic vector retrieval are not implemented.
- Sub-three-second retrieval across **1,000+ actual pages has not been verified**. Existing timing reports cover this local corpus and do not establish that requirement.
- The standard-library HTTP server is a local demo, without production authentication or deployment hardening.

To run the local retrieval benchmark:

```powershell
python benchmark_search.py
```

This rebuilds the index and updates `docs/search_benchmark.json`. Retrieval timings exclude browser rendering, network transport, and optional model calls.

## Project structure

| Path | Purpose |
| --- | --- |
| `prepare_data.py` | Preserve source rows and extract normalized fields, citations, and dataset history. |
| `applicability.py` | Parse supported conditions and compare configurations with quoted proposed text. |
| `configuration.py` | Validate typed identifiers, condition answers, and dated bulletin-completion fields. |
| `search.py` | Build and query the SQLite FTS5 index; attach evidence and version observations. |
| `answerability.py` | Identify requests that retrieved records cannot answer. |
| `query_understanding.py` | Local interpretation and optional validated AI field extraction. |
| `web_app.py`, `web/` | Local HTTP API and browser interface. |
| `fulltext.py` | On-demand official XML, source citations, bounded cache and separately requested PDFs. |
| `evaluate_cases.py`, `eval/` | Supplied labeled cases and reproducible evaluation reports. |
| `data/processed/` | Prepared records and data-quality reports. |
| `data/rules/` | Generated rule records, extraction report, and review queue. |
| `docs/` | Design notes, decisions, validation, and benchmark reports. |

Rebuild generated rule artifacts with `python applicability.py build`. Prepared records are included; to regenerate them from a CSV, use `python prepare_data.py --input "path/to/source.csv"`.

For more detail, read the [configuration follow-up](docs/configuration_v12.md), [web demo guide](docs/web_demo.md), and [design decisions](docs/decisions.md). Some earlier design notes describe previous iterations; this README and the configuration follow-up describe the current branch.
