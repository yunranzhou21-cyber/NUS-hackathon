"""Parse a deliberately narrow applicability grammar; never infer legal effect."""
import argparse
import csv
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

from prepare_data import clean, make_value

FIELD = "Link for Submitting Public Comment"
DEFAULT_INPUT = "data/processed/records.jsonl"
DEFAULT_OUTPUT = "data/rules"


def load_records(path=DEFAULT_INPUT):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def citation(rec, start, end):
    text = rec["raw"][FIELD]
    return {
        "record_id": rec["record_id"],
        "source": rec["source"],
        "field": FIELD, "start": start, "end": end,
        "quote": text[start:end],
    }


def extract_blocks(rec):
    text = rec["raw"][FIELD]
    result = []
    for match in re.finditer(r"\bApplicability\s*:", text, re.I):
        # Never truncate at an arbitrary character count and call it complete.
        tail = text[match.end():]
        stop = re.search(
            r"\n\s*(?:Note\s+\d+\s*:|Compliance\s*:|"
            r"Unsafe Condition\b|Compliance\b|\([a-z]\)\s)",
            tail, re.I,
        )
        end = match.end() + (stop.start() if stop else len(tail))
        result.append({
            "evidence": citation(rec, match.start(), end),
            "body": text[match.end():end],
            "boundary_found": stop is not None,
        })
    return result


def normalize_model(value):
    return clean(value).upper()


def parse_models(text):
    """Expand only explicit shared hyphen-prefix shorthand, e.g. 737-100, -200."""
    tokens = re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", text.strip(), flags=re.I)
    values, prefix = [], None
    for token in tokens:
        token = normalize_model(token)
        if not token:
            return None
        if re.fullmatch(r"-[0-9][A-Z0-9]*", token):
            if prefix is None:
                return None
            token = prefix + token
        elif not re.fullmatch(r"[A-Z0-9]+(?:[- ][A-Z0-9]+)*", token):
            return None
        prefix = token.rsplit("-", 1)[0] if "-" in token else None
        values.append(token)
    return list(dict.fromkeys(values))


def number_set(text):
    """Parse complete unions of numeric singleton/range terms, never partial matches."""
    text = re.sub(r"\binclusive\b", "", text, flags=re.I).strip(" ,")
    tokens = re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", text, flags=re.I)
    ranges = []
    for token in tokens:
        match = re.fullmatch(r"(\d+)(?:\s+(?:through|to)\s+(\d+))?", token.strip(), re.I)
        if not match:
            return None
        lo, hi = int(match[1]), int(match[2] or match[1])
        if lo > hi:
            return None
        ranges.append([lo, hi])
    return ranges or None


def parse_block(block):
    if not block["boundary_found"]:
        return None, "UNCONFIRMED_BLOCK_BOUNDARY"
    text = clean(block["body"])
    # Full-string grammar: every unconsumed condition causes refusal.
    head = re.fullmatch(
        r"(?:All\s+)?Model\s+(.+?)\s+(series\s+)?"
        r"(airplanes|helicopters)\b(.*)", text, re.I,
    )
    if not head:
        return None, "UNSUPPORTED_MODEL_OR_TABLE_SYNTAX"
    models = parse_models(head[1])
    if not models:
        return None, "UNSUPPORTED_MODEL_LIST"
    tail = head[4].strip()
    tail = re.sub(r"(?:certificated|certified)\s+in\s+any\s+category",
                  "", tail, flags=re.I)
    tail = tail.strip(" ,;.")
    predicate = None
    if tail:
        constraint = re.fullmatch(
            r"(?:having\s+)?(serial numbers|line numbers)\s*"
            r"(?:\(\s*(?:S/\s*N|L/\s*N)\s*\))?\s*"
            r"(.+?)(?:[,;]\s*except\s+(?:serial numbers|line numbers)\s+(.+))?",
            tail, re.I,
        )
        if not constraint:
            return None, "UNRESOLVED_ADDITIONAL_CONDITIONS"
        include = number_set(constraint[2])
        exclude = number_set(constraint[3]) if constraint[3] else []
        if include is None or exclude is None:
            return None, "UNSUPPORTED_IDENTIFIER_CONDITION"
        predicate = {
            "field": ("serial_number" if constraint[1].lower().startswith("serial")
                      else "line_number"),
            "include_ranges_inclusive": include,
            "exclude_ranges_inclusive": exclude,
            "numeric_only": True,
        }
    return {
        "models": models,
        "model_comparison": "EXACT_NORMALIZED_ONLY",
        "series_word_present": bool(head[2]),
        "identifier_predicate": predicate,
        "evidence": block["evidence"],
    }, None


def parse_record(rec):
    blocks = extract_blocks(rec)
    result = {
        "record_id": rec["record_id"],
        "docket": rec["normalized"]["docket"],
        "make": rec["normalized"]["make"],
        "make_evidence": rec["normalized"]["make_evidence"],
        "source": rec["source"],
        "document_kind": rec["normalized"]["document_kind"],
        "document_kind_evidence": rec["normalized"]["document_kind_evidence"],
        "dataset_history": rec["dataset_history"],
        "parser_version": "1.0",
        "review_status": "AUTOMATED_NOT_EXPERT_APPROVED",
        "parse_status": "UNSUPPORTED",
        "reasons": [], "rule": None,
        "applicability_blocks": blocks,
        "effective_rule_verified": False,
    }
    if not blocks:
        result["reasons"] = ["NO_EXPLICIT_APPLICABILITY_BLOCK"]
    elif len(blocks) != 1:
        result["reasons"] = ["MULTIPLE_APPLICABILITY_BLOCKS"]
    else:
        rule, reason = parse_block(blocks[0])
        if reason:
            result["reasons"] = [reason]
        else:
            result["parse_status"] = "SUPPORTED_TEXT_GRAMMAR"
            result["rule"] = rule
    return result


def in_ranges(value, ranges):
    return any(lo <= value <= hi for lo, hi in ranges)


def evaluate(parsed, configuration):
    """Decision refers only to the quoted proposed scope, NOT legal applicability."""
    answer = {
        "record_id": parsed["record_id"], "docket": parsed["docket"],
        "status": "NEEDS_CLARIFICATION",
        "scope_decision": "UNKNOWN",
        "legal_applicability": "UNVERIFIED",
        "operational_answer": "REFUSE",
        "reason_codes": [],
        "configuration": configuration,
        "checks": [],
        "evidence": [b["evidence"] for b in parsed["applicability_blocks"]],
        "document_kind": parsed["document_kind"],
        "document_kind_evidence": parsed["document_kind_evidence"],
        "dataset_history": parsed["dataset_history"],
        "warning": "Only compares configuration with a quoted proposed scope. "
                   "Does not establish an effective AD or authorize maintenance.",
    }

    def stop(code, status="REFUSE"):
        answer["status"] = status
        answer["reason_codes"].append(code)
        return answer

    if parsed["document_kind"] not in ("proposal", "supplemental_or_revised_proposal"):
        return stop("DOCUMENT_KIND_NOT_CONFIRMED_PROPOSAL")
    if not parsed["dataset_history"]["is_latest_observed"]:
        return stop("NOT_LATEST_OBSERVED_RECORD_IN_CSV")
    if parsed["parse_status"] != "SUPPORTED_TEXT_GRAMMAR":
        answer["reason_codes"].extend(parsed["reasons"])
        return stop("APPLICABILITY_NOT_FULLY_PARSED")
    if not parsed["make"]:
        return stop("SOURCE_MAKE_MISSING")
    missing = [field for field in ("make", "model") if not configuration.get(field)]
    if missing:
        answer["missing_fields"] = missing
        return stop("CONFIGURATION_INCOMPLETE", "NEEDS_CLARIFICATION")
    make_match = make_value(configuration["make"]) == parsed["make"]
    answer["checks"].append({
        "field": "make", "result": "MATCH" if make_match else "NO_MATCH",
        "evidence": parsed["make_evidence"],
    })
    if not make_match:
        # Different spellings may be unregistered aliases, not another manufacturer.
        return stop("MAKE_MISMATCH_OR_UNREGISTERED_ALIAS", "NEEDS_CLARIFICATION")
    rule = parsed["rule"]
    model_match = normalize_model(configuration["model"]) in rule["models"]
    answer["checks"].append({
        "field": "model", "result": "MATCH" if model_match else "UNKNOWN",
        "allowed_exact_tokens": rule["models"], "evidence": rule["evidence"],
    })
    if not model_match:
        # Series membership or a model alias needs an explicit mapping we do not have.
        return stop("MODEL_NOT_EXACTLY_RESOLVED", "NEEDS_CLARIFICATION")
    predicate = rule["identifier_predicate"]
    if predicate:
        field = predicate["field"]
        supplied = configuration.get(field)
        if supplied is None or str(supplied).strip() == "":
            answer["missing_fields"] = [field]
            return stop("IDENTIFIER_REQUIRED", "NEEDS_CLARIFICATION")
        if not re.fullmatch(r"[0-9]+", str(supplied).strip()):
            return stop("IDENTIFIER_MUST_BE_NUMERIC", "NEEDS_CLARIFICATION")
        value = int(str(supplied).strip())
        included = in_ranges(value, predicate["include_ranges_inclusive"])
        excluded = in_ranges(value, predicate["exclude_ranges_inclusive"])
        answer["checks"].append({
            "field": field, "value": value, "included": included,
            "excluded": excluded, "evidence": rule["evidence"],
        })
        decision = "MATCH" if included and not excluded else "NO_MATCH"
    else:
        decision = "MATCH"
    answer["scope_decision"] = decision
    answer["status"] = "TEXT_SCOPE_COMPARISON_ONLY"
    answer["reason_codes"] = ["PROPOSED_SCOPE_ONLY_EFFECTIVENESS_UNVERIFIED"]
    return answer


def build(input_path=DEFAULT_INPUT, output_dir=DEFAULT_OUTPUT):
    records = load_records(input_path)
    parsed = [parse_record(rec) for rec in records]
    evidence_count = 0
    for rec, item in zip(records, parsed):
        for block in item["applicability_blocks"]:
            ev = block["evidence"]
            if rec["raw"][ev["field"]][ev["start"]:ev["end"]] != ev["quote"]:
                raise ValueError("Citation round-trip failed: " + rec["record_id"])
            evidence_count += 1
    counts = {
        "records": len(parsed),
        "explicit_block_records": sum(bool(r["applicability_blocks"]) for r in parsed),
        "supported_text_grammar_records": sum(
            r["parse_status"] == "SUPPORTED_TEXT_GRAMMAR" for r in parsed),
        "supported_with_identifier_predicate": sum(
            bool(r["rule"] and r["rule"]["identifier_predicate"]) for r in parsed),
        "unsupported_reasons": dict(Counter(
            reason for r in parsed for reason in r["reasons"])),
        "validated_block_citations": evidence_count,
        "expert_reviewed_rules": 0,
        "legally_verified_rules": 0,
    }
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "applicability_rules.jsonl").open("w", encoding="utf-8") as stream:
        for item in parsed:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    with (out / "review_queue.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["record_id", "docket", "parse_status", "reasons",
                         "models", "identifier_predicate", "raw_applicability",
                         "expert_review_status"])
        for item in parsed:
            rule = item["rule"] or {}
            writer.writerow([
                item["record_id"], item["docket"], item["parse_status"],
                " | ".join(item["reasons"]), " | ".join(rule.get("models", [])),
                json.dumps(rule.get("identifier_predicate"), ensure_ascii=False),
                "\n\n".join(b["evidence"]["quote"]
                            for b in item["applicability_blocks"]),
                "NOT_REVIEWED",
            ])
    (out / "extraction_report.json").write_text(
        json.dumps(counts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(counts, indent=2))
    return parsed, counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--input", default=DEFAULT_INPUT)
    build_parser.add_argument("--output", default=DEFAULT_OUTPUT)
    check = sub.add_parser("check")
    check.add_argument("--input", default=DEFAULT_INPUT)
    check.add_argument("--record-id", required=True)
    check.add_argument("--make")
    check.add_argument("--model")
    check.add_argument("--serial-number")
    check.add_argument("--line-number")
    args = parser.parse_args()
    if args.command == "build":
        build(args.input, args.output)
    else:
        candidates = [r for r in load_records(args.input)
                      if r["record_id"] == args.record_id]
        if not candidates:
            parser.error("Unknown record ID; do not guess an alternative.")
        config = {key: getattr(args, key) for key in
                  ("make", "model", "serial_number", "line_number")
                  if getattr(args, key) is not None}
        result = evaluate(parse_record(candidates[0]), config)
        print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
