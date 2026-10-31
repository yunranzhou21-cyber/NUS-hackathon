"""Generate a bounded inspection artifact from the local prepared dataset."""
import json
import re
from pathlib import Path

FIELD = "Link for Submitting Public Comment"
records = [json.loads(line) for line in
           Path("data/processed/records.jsonl").read_text(encoding="utf-8").splitlines()]
samples = []
for rec in records:
    text = rec["raw"][FIELD]
    matches = list(re.finditer(r"\bApplicability\s*:", text, re.I))
    for match in matches:
        tail = text[match.end():]
        stop = re.search(r"\n\s*(?:Note\s+\d+\s*:|Compliance\s*:|\([a-z]\)\s)", tail, re.I)
        end = match.end() + (stop.start() if stop else min(len(tail), 2200))
        samples.append((rec["record_id"], rec["source"]["docket"],
                        text[match.start():end], rec["normalized"]["document_kind"]))
lines = ["# Applicability inspection", ""]
for rid, docket, text, kind in samples:
    lines.extend([f"## {rid} / {docket} / {kind}", text, ""])
Path("data/processed/applicability_samples.md").write_text(
    "\n".join(lines), encoding="utf-8")
print(f"Applicability blocks: {len(samples)}")
print(f"Blocks mentioning serial: {sum(bool(re.search('serial|S/N', s[2], re.I)) for s in samples)}")
print("Inspection artifact: data/processed/applicability_samples.md")
