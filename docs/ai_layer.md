# AI query understanding

Date: 2026-10-31

POC: Project team

TL;DR: An optional language model turns a question into structured fields. Deterministic code still decides applicability. With no key configured, a local interpreter does the same job for the fields it can recognize.

## What the model may and may not do

The model receives only the user's question text. It never sees the documents or the search results. It returns an intent and four optional fields: make, model, serial number, line number.

Every returned value must appear in the user's own text (case and spacing ignored). A value that does not is discarded and shown in the interface as discarded. Identifiers found by local rules always override model output. If the model output is not valid JSON, has an unknown intent, or the call fails, the system falls back to local rules and says so.

The model never judges applicability, never summarizes documents, and never supplies maintenance content. Those steps are unchanged: indexed search, the exact-match rule engine, and verbatim source citations.

## Enable it

```powershell
$env:AI_API_KEY = "your-key"
$env:AI_MODEL = "your-model-name"          # optional
$env:AI_BASE_URL = "https://api.openai.com/v1"   # optional, any OpenAI-compatible endpoint
python web_app.py
```

The key is read from the environment only and is never written to disk or logged. The interface toggle stays disabled, with an explanation, until a key is present. Confirm with the organizers that sending user questions to an external service is allowed before relying on it in the demo.

## Local interpreter (always available)

Recognizes serial and line numbers, manufacturers that exist in the index, and a history intent ("older", "previous", "superseded"). It does not infer anything else. The model name is matched by the search layer against models present in the data.

## Verified

`test_query_understanding.py` (14 tests) covers kept and discarded values, malformed output, unknown intents, provider failures, code-fenced JSON, conflicts between question and form, and end-to-end searches through the HTTP API using a fake model. The suite totals 56 tests.

Not verified: behavior with a real model. Extraction accuracy on real user phrasing has not been measured, and no independent evaluation set exists yet. Test models are scripted, so these tests show the safeguards work, not that a given model extracts well.

## Known limits

Search is still keyword AND matching, so a question word that appears in no document can hide a relevant result. The stopword list covers common question words but is not a general fix.

When the question and a form field disagree, the system asks for clarification instead of choosing one.
