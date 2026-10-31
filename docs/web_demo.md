# English web demo

Date: 2026-10-31

POC: Project team

TL;DR: An English local web interface connects evidence search with conservative configuration checks. It is a research prototype, not a maintenance decision system.

## Start

```powershell
python web_app.py
```

Open http://127.0.0.1:8000. Use `--port 8001` if the default port is occupied. The server listens only on loopback. A missing index is built automatically; rebuild a stale index with `python search.py build`.

## Demonstrate

Select **Compare a serial-number range**. The query retrieves FAA-2003-16645 and compares EMB-120 serial number 120006 with the proposed scope. Expand **Configuration checks & supporting evidence** to inspect the applicability text, then **Inspect source text & citations** for retrieval evidence.

Select **Refuse when there is no evidence** to demonstrate a no-evidence response. **Boeing corrosion information** demonstrates manufacturer filtering. Enable **Include historical records** to inspect older observations. Latest observation means within this CSV, not the latest effective legal version.

The page includes English labels, status messages, source metadata, examples, error messages, and safety notices. Source quotations remain verbatim. No document or query is sent to an external model. The response can be inspected as JSON.

## Checks

```powershell
python -m unittest discover -q
```

HTTP tests cover query responses, input validation, origin restrictions, static asset routes, English interface text, and denial of project-file access. Browser checks confirmed an English serial-number result and its unverified-effect warning. Screenshot capture timed out in this environment, so visual layout verification remains incomplete.

The standard-library HTTP server is for local demos only. It has no production authentication, public deployment hardening, or expert approval workflow.
