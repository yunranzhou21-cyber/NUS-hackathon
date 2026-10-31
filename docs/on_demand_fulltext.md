# On-demand Federal Register full text

The application searches its local CSV index first. The browser then requests
official XML for at most the top three results automatically. Clear **Fetch full
text for the top 3 results** to keep retrieval local; individual Load buttons
remain available. No full-corpus download is performed.

## Data flow

1. `/api/search` returns CSV candidates immediately, including current cache state.
   It does not wait for network downloads. Its configuration checks are labeled
   `SUPPLIED_CSV_TEXT_ONLY`.
2. The page posts a `record_id` to `/api/fulltext` with `action: "text"`. The server
   resolves the document number from that record's supplied Federal Register URL.
   Only public document requests go to the official sources; search questions and
   aircraft configurations are not sent to Federal Register.
3. A worker requests the official metadata API, then the returned XML URL. The
   server returns `FETCHING` while working. Calls for the same document reuse
   cached data and deduplicate active work. At most two workers run, with eight
   outstanding jobs allowed.
4. The page polls until READY, FAILED, or its waiting window expires. It displays
   fetch duration separately from local retrieval latency. Full-text failure does
   not produce a full-text applicability conclusion.
5. READY responses contain source passages and a fresh configuration check based
   exclusively on the fetched Applicability section. The page replaces the
   preliminary check and updates its JSON view. A missing, ambiguous, unsupported,
   or unbounded section remains UNKNOWN; no CSV rule is substituted as a full-text
   rule. Extension, correction, withdrawal and unconfirmed document types do not
   yield automatic proposal-scope decisions.
6. **Load official PDF** starts a separate download. When ready, **Open cached
   official PDF** opens the locally served copy. Searching or loading XML alone
   does not download PDFs.

The lightweight index still determines which documents become candidates. Newly
fetched text is ranked into source excerpts for that document, not added to a
global full-text search index. A query expressed only in terms absent from the
CSV may still miss a relevant document. Documents with no resolvable supplied
Federal Register link require a later metadata-resolution workflow; no link is
guessed. Linked earlier NPRMs are not recursively fetched automatically.

## Cache and citations

Files live in ignored directory `data/fulltext/`: original XML, a derived text and
metadata JSON artifact, and optional PDF. Cache freshness is 24 hours; expired
text is refreshed before another full-text comparison. The default cache budget
is 50 MiB. Downloads are bounded at 1 MiB for metadata, 5 MiB for XML and 10 MiB
for PDF. On reaching the budget or a size limit, fetching stops with an explicit
error; automatic eviction is not implemented. Constructor settings can adjust
the TTL and budget.

Source URLs and redirect destinations are restricted to HTTPS official hosts.
Document numbers and available docket IDs are checked before a result is returned.
XML entity/DOCTYPE declarations are rejected. Cache writes are atomic, and the
derived text hash is validated on read.

Each fetched passage includes record ID, docket, FR document number, citation,
section, artifact offsets, XML/text hashes, source links and retrieval timestamp.
Printed page numbers are taken only from explicit XML `PRTPAGE`/`PGBRK` markers.
A paragraph crossing a page boundary lists multiple pages. Without markers,
only document-range precision is claimed. These are XML-based printed-page
locations, not PDF layout validation; PDFs are available for manual verification.

`GET /api/corpus` reports cached text documents, downloaded PDFs, total cache bytes,
TTL, budget and distinct dated printed pages observed in XML. The page shows the
document/PDF counts and storage use. It does not count unvisited links as ingested
pages or claim the 1,000-page requirement is met.

## HTTP interface

```json
{
  "record_id": "row-0157",
  "action": "text",
  "query": "FAA-2024-2142",
  "configuration": {},
  "historical_comparison": false,
  "retry": false
}
```

POST this body to `/api/fulltext`. Actions are `status` (no network), `text`, and
`pdf`. Explicit retry can restart a failed job. The endpoint retains same-origin
validation, body size limits and typed configuration validation. No arbitrary URL
or filesystem path is accepted. A ready PDF is served at the returned local
`pdf_url`. A completed download is not evidence of legal effect or current status.

## Verification

Fixture tests cover lazy and separate downloads, reuse, concurrency/deduplication,
failures/retry, stale or tampered cache, metadata identity, forbidden URLs/XML,
storage limits, citation round-trips, cross-page passages, missing page markers,
ambiguous applicability, HTTP validation, and cached PDF serving. No external
network is used by automated tests.

A real smoke test fetched `2024-25883` (record row-0157) as metadata plus XML in
about 1.8 seconds on this machine; initial cache use was 28,763 bytes and zero
PDFs. It is a comment-period extension, so the full-text comparison stayed UNKNOWN.
Browser verification retrieved both records for FAA-2024-2142 in the background;
the original NPRM was fetched separately as a candidate. These observations are
not a latency SLA or a 1,000-page benchmark.

The final suite passed 116 tests. Browser testing also confirmed that clicking
Load official PDF changed the corpus from 2 text documents / 0 PDFs to 2 text
documents / 1 PDF (about 0.35 MiB total), then exposed the cached PDF link.

The existing 34-case evaluation remains an offline CSV/rule regression baseline.
Its historical-mode 97% score does not measure this online ingestion/parser path.
Modern full-text grammar coverage and an independent evaluation remain future work.
