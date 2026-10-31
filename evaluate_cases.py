"""Score the system against a JSONL set of labeled cases.

Mode A (headline): end to end through the real HTTP API with the case's query and
configuration, exactly as the web page would send them (local rules, no AI).
Mode B (diagnostic only): rule engine on the labeled record, with the manufacturer
filled from that record. It isolates rule-engine errors from retrieval errors and
is NOT a headline number, because it hands the system the right record.

Scoring is fixed before running; see `score_case`.
"""
import argparse
import json
import threading
from collections import Counter, defaultdict
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import web_app
from applicability import evaluate, load_records, parse_record

CASES = "eval/airbus_q4_test_cases.jsonl"
SUPPORTED = ("make", "model", "serial_number", "line_number")
DEFINITIVE = ("MATCH", "NO_MATCH")
# INSUFFICIENT_EVIDENCE was added with the answerability check: an explicit statement
# that records were found but cannot answer. It is an explicit decline like the others.
DECLINES = ("REFUSE", "NEEDS_CLARIFICATION", "INSUFFICIENT_EVIDENCE")


def load_cases(path):
    with open(path, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def split_config(configuration):
    """Keep only inputs the system can accept; report the rest instead of hiding it."""
    used = {k: str(v) for k, v in configuration.items() if k in SUPPORTED}
    dropped = sorted(k for k in configuration if k not in SUPPORTED)
    return used, dropped


def post(base, payload):
    req = Request(base + "/api/search", json.dumps(payload).encode(),
                  {"Content-Type": "application/json"})
    with urlopen(req, timeout=20) as response:
        return json.load(response)


def overlaps(item, expected):
    return (item["field"] == expected["field"]
            and item["start"] < expected["end"] and expected["start"] < item["end"])


def score_case(case, response):
    """Return (outcome, detail). Outcomes: PASS, SAFE_FAIL, UNSAFE_FAIL."""
    expected = case["expected"]
    decision = expected["decision"]
    target = expected["relevant_record_ids"][0]
    results = {r["record_id"]: r for r in response["results"]}
    hit = results.get(target)
    status = response["status"]
    reason = (response.get("reason_codes") or [None])[0]
    scope = hit["configuration_check"]["scope_decision"] if hit else None
    rank = (list(results).index(target) + 1) if hit else None
    detail = {"status": status, "reason": reason, "retrieved": bool(hit),
              "rank": rank, "scope_decision": scope}

    if decision in DEFINITIVE:
        if not hit:
            return "SAFE_FAIL", {**detail, "why": "relevant record not retrieved"}
        if scope == decision:
            return "PASS", detail
        if scope in DEFINITIVE:
            return "UNSAFE_FAIL", {**detail, "why": f"said {scope}, expected {decision}"}
        return "SAFE_FAIL", {**detail, "why": "retrieved but declined to decide"}

    if decision == "UNKNOWN":
        if any(r["configuration_check"]["scope_decision"] in DEFINITIVE
               for r in results.values() if r["record_id"] == target):
            return "UNSAFE_FAIL", {**detail, "why": f"gave a definitive {scope}"}
        if hit and scope == "UNKNOWN":
            return "PASS", detail
        if not hit and status in DECLINES:
            return "PASS", {**detail, "why": "explicit decline"}
        return "SAFE_FAIL", {**detail, "why": "no explicit decline for the labeled record"}

    if decision == "INSUFFICIENT_EVIDENCE":
        # The system must say it cannot answer; returning passages alone does not say so.
        if status in DECLINES:
            return "PASS", {**detail, "why": "explicit decline"}
        return "SAFE_FAIL", {**detail, "why": "returned passages without stating it cannot answer"}

    if decision == "ANSWER":
        if not hit:
            return "SAFE_FAIL", {**detail, "why": "relevant record not retrieved"}
        shown = any(overlaps(ev, exp) and ev["source"]["csv_record_number"] == exp["source"]["csv_record_number"]
                    for ev in hit["evidence"] for exp in case["evidence"])
        if not shown:
            return "SAFE_FAIL", {**detail, "why": "record retrieved but expected passage not shown"}
        if case["category"] == "withdrawal_vs_current":
            kind = hit["configuration_check"]["document_kind"]
            if kind != "withdrawal_notice":
                return "UNSAFE_FAIL", {**detail, "why": f"document typed as {kind}"}
        return "PASS", detail
    return "SAFE_FAIL", {**detail, "why": "unknown label " + decision}


def structural_checks(response):
    """Weak by construction: the product always returns these. Reported, not scored."""
    ok = response.get("operational_answer") == "REFUSE" and response.get("legal_applicability") == "UNVERIFIED"
    pages = all(ev["source"]["page_precision"] == "document_range_only_not_exact_claim_page"
                for r in response["results"] for ev in r["evidence"])
    return ok and pages


def run_mode_b(cases, records):
    parsed = {r["record_id"]: parse_record(r) for r in records}
    rows = []
    for case in cases:
        if case["expected"]["decision"] not in DEFINITIVE + ("UNKNOWN",) or not case["configuration"]:
            continue
        rid = case["expected"]["relevant_record_ids"][0]
        used, dropped = split_config(case["configuration"])
        used["make"] = parsed[rid]["make"]
        check = evaluate(parsed[rid], used)
        fake = {"status": check["status"], "results": [{
            "record_id": rid, "evidence": [], "configuration_check": check}],
            "reason_codes": check["reason_codes"]}
        outcome, detail = score_case(case, fake)
        rows.append({"test_id": case["test_id"], "outcome": outcome, "dropped_inputs": dropped,
                     "scope_decision": check["scope_decision"], "reasons": check["reason_codes"]})
    return rows


def pct(n, d):
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "n/a"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=CASES)
    parser.add_argument("--out", default="eval/results.json")
    parser.add_argument("--historical", action="store_true",
                        help="send historical_comparison=true (explicit mode for superseded proposals)")
    args = parser.parse_args()
    cases = load_cases(args.cases)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_app.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    rows, latencies = [], []
    try:
        for case in cases:
            used, dropped = split_config(case["configuration"])
            response = post(base, {"query": case["query"], "configuration": used, "limit": 5,
                                   "historical_comparison": args.historical})
            outcome, detail = score_case(case, response)
            latencies.append(response["retrieval_ms"])
            rows.append({"test_id": case["test_id"], "category": case["category"],
                         "expected": case["expected"]["decision"], "outcome": outcome,
                         "dropped_inputs": dropped, "structural_ok": structural_checks(response),
                         "interpretation": response.get("interpretation", {}).get("fields"),
                         **detail})
    finally:
        server.shutdown()
        server.server_close()
    mode_b = run_mode_b(cases, load_records())
    total = Counter(r["outcome"] for r in rows)
    by_category = defaultdict(Counter)
    for r in rows:
        by_category[r["category"]][r["outcome"]] += 1
    summary = {
        "cases": len(rows), "file_sha256_note": "see eval/ directory", "mode_A": dict(total),
        "mode_A_pass_rate": total["PASS"] / len(rows),
        "mode_A_unsafe_errors": total["UNSAFE_FAIL"],
        "mode_B_diagnostic": dict(Counter(r["outcome"] for r in mode_b)),
        "retrieval_hit_rate_labeled_record": sum(r["retrieved"] for r in rows) / len(rows),
        "latency_ms": {"max": max(latencies), "mean": sum(latencies) / len(latencies)},
        "structural_checks_all_ok": all(r["structural_ok"] for r in rows),
    }
    Path(args.out).write_text(json.dumps({"summary": summary, "mode_A": rows, "mode_B": mode_b},
                                         indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\nMODE A  end to end, {len(rows)} cases")
    print(f"  pass                 {pct(total['PASS'], len(rows))}")
    print(f"  safe fail (declined or not found)   {pct(total['SAFE_FAIL'], len(rows))}")
    print(f"  UNSAFE fail (wrong definitive/typed) {pct(total['UNSAFE_FAIL'], len(rows))}")
    print(f"  labeled record retrieved            {pct(sum(r['retrieved'] for r in rows), len(rows))}")
    print("\nBy category (pass/total)")
    for cat, counts in sorted(by_category.items()):
        print(f"  {cat:34s} {counts['PASS']}/{sum(counts.values())}"
              + (f"  unsafe={counts['UNSAFE_FAIL']}" if counts["UNSAFE_FAIL"] else ""))
    print("\nFailures")
    for r in rows:
        if r["outcome"] != "PASS":
            print(f"  {r['test_id']} [{r['outcome']}] expected {r['expected']}: {r.get('why')}"
                  f" | status={r['status']} reason={r['reason']}"
                  + (f" | unsupported inputs dropped: {r['dropped_inputs']}" if r["dropped_inputs"] else ""))
    print(f"\nMODE B  rule engine on the labeled record (diagnostic), {len(mode_b)} cases")
    cb = Counter(r["outcome"] for r in mode_b)
    print(f"  pass {pct(cb['PASS'], len(mode_b))}  safe fail {cb['SAFE_FAIL']}  unsafe {cb['UNSAFE_FAIL']}")
    print(f"\nLatency per query: mean {summary['latency_ms']['mean']:.0f} ms, max {summary['latency_ms']['max']:.0f} ms")
    print(f"Results written to {args.out}")


if __name__ == "__main__":
    main()
