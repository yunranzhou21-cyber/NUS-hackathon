"""Parse a deliberately narrow applicability grammar; never infer legal effect."""
import argparse
import csv
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

from prepare_data import clean, make_value
from configuration import dated_condition

FIELD = "Link for Submitting Public Comment"
PARSER_VERSION = "1.2-typed-configuration"
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


def sub_evidence(block, start, end):
    """Citation for body[start:end], mapped back to raw CSV offsets when known."""
    ev, body = block["evidence"], block["body"]
    if "start" not in ev:  # synthetic fixtures carry no offsets
        return {"quote": body[start:end]}
    body_start = ev["end"] - len(body)
    return {**ev, "start": body_start + start, "end": body_start + end,
            "quote": body[start:end]}


def locate(block, normalized):
    """Find whitespace-normalized text inside the raw body; None if not exactly once."""
    pattern = r"\s+".join(map(re.escape, normalized.split()))
    hits = list(re.finditer(pattern, block["body"]))
    return sub_evidence(block, hits[0].start(), hits[0].end()) if len(hits) == 1 else None


# --- Grammar 3: external documents (service bulletins, modifications) -------------
# A reference counts only when it pins one dated document. Undated or disjunctive
# references ("SB 123", "145-31-0022; or 145-31-0030") are never peeled, so they stay
# in the tail and make the whole rule unsupported.
MONTH = (r"(?:January|February|March|April|May|June|July|August|September|"
         r"October|November|December)")
DOC_REF = (
    r"(?P<doc>(?:[A-Z][A-Za-z]*\s+){0,3}?"
    r"(?:Alert\s+|Special\s+Attention\s+)?"
    r"(?:Service|Technical)\s+Bulletin\s+"
    r"(?P<docnum>[A-Z0-9]+(?:-[A-Z0-9]+)*)"
    r"(?:,\s*(?:Revision|Change)\s+\d+)?"
    r",\s*dated\s+" + MONTH + r"\s+\d{1,2},\s+\d{4})"
)
EXTERNAL_CLAUSES = [
    # (kind, regex, condition value that keeps the aircraft IN scope, question)
    ("LISTED_IN",
     r"as\s+(?:listed|specified|identified)\s+in\s+" + DOC_REF,
     True, "Is the aircraft listed in {doc}?"),
    ("ACTIONS_DONE",
     r"excluding\s+those\s+(?:airplanes|helicopters)\s+on\s+which\s+the\s+actions\s+"
     r"specified\s+in\s+" + DOC_REF + r",?\s+ha(?:s|ve)\s+been\s+(?:done|accomplished)",
     False, "Have the actions specified in {doc} been done on the aircraft?"),
    ("MODIFIED_PER",
     r"that\s+have\s+not\s+been\s+modified\s+by\s+following\s+the\s+Accomplishment\s+"
     r"Instructions\s+of\s+" + DOC_REF,
     False, "Has the aircraft been modified by following the Accomplishment "
            "Instructions of {doc}?"),
]


def peel_external_conditions(tail, block):
    """Remove whole, separator-bounded external clauses. Returns (tail, conditions)."""
    conditions = []
    for kind, pattern, in_scope_when, question in EXTERNAL_CLAUSES:
        bounded = r"(?:^|[,;])\s*(?P<clause>" + pattern + r")\s*(?=[,;.]|$)"
        while True:
            match = re.search(bounded, tail, re.I)
            if not match:
                break
            document_date, completion_field = dated_condition(
                clean(match['doc']), match['docnum'].upper(), kind)
            cid = f"{kind}:{match['docnum'].upper()}@{document_date or 'date-unresolved'}"
            if any(c["id"] == cid for c in conditions):
                return None, "DUPLICATE_EXTERNAL_CONDITION"
            evidence = locate(block, match["clause"])
            if evidence is None:
                return None, "EXTERNAL_CONDITION_CITATION_NOT_LOCATED"
            conditions.append({
                "id": cid, "kind": kind, "document": clean(match["doc"]),
                "question": question.format(doc=clean(match["doc"])),
                "in_scope_when": in_scope_when,
                "verifiable_from_csv": False,
                "document_date": document_date,
                "completion_field": completion_field,
                "evidence": evidence,
            })
            tail = tail[:match.start()] + tail[match.end("clause"):]
    order = {c["id"]: block["body"].find(c["evidence"]["quote"]) for c in conditions}
    return tail, sorted(conditions, key=lambda c: order[c["id"]])


# --- Grammar 2: serial numbers with an explicit literal prefix --------------------
def prefixed_number_set(text):
    """`with a prefix of "RN" 003 through 066 and 068` -> prefix, digit width, ranges."""
    match = re.fullmatch(r"with\s+a\s+prefix\s+of\s+[\"“]([A-Z]{1,4})[\"”]"
                         r"\s+(.+)", text.strip(), re.I)
    if not match:
        return None
    numbers = re.findall(r"\d+", match[2])
    widths = {len(n) for n in numbers}
    ranges = number_set(match[2])
    # Mixed widths (e.g. 003 vs 66) leave zero-padding ambiguous: refuse.
    if ranges is None or len(widths) != 1:
        return None
    return {"prefix": match[1].upper(), "digits": widths.pop(), "ranges": ranges}


def parse_identifier(tail):
    constraint = re.fullmatch(
        r"(?:having\s+)?(serial numbers|line numbers)\s*"
        r"(?:\(\s*(?:S/\s*N|L/\s*N)\s*\))?\s*"
        r"(.+?)(?:[,;]\s*except\s+(?:serial numbers|line numbers)\s+(.+))?",
        tail, re.I,
    )
    if not constraint:
        return None, "UNRESOLVED_ADDITIONAL_CONDITIONS"
    field = ("serial_number" if constraint[1].lower().startswith("serial")
             else "line_number")
    exclude = number_set(constraint[3]) if constraint[3] else []
    prefixed = (prefixed_number_set(constraint[2])
                if re.match(r"with\s+a\s+prefix", constraint[2], re.I) else None)
    if prefixed:
        if field != "serial_number" or exclude is None:
            return None, "UNSUPPORTED_IDENTIFIER_CONDITION"
        return {
            "field": field, "prefix": prefixed["prefix"],
            "digits": prefixed["digits"],
            "include_ranges_inclusive": prefixed["ranges"],
            "exclude_ranges_inclusive": exclude,
            "numeric_only": False,
        }, None
    include = number_set(constraint[2])
    if include is None or exclude is None:
        return None, "UNSUPPORTED_IDENTIFIER_CONDITION"
    return {
        "field": field,
        "include_ranges_inclusive": include,
        "exclude_ranges_inclusive": exclude,
        "numeric_only": True,
    }, None


# --- Grammar 1: "Table 1" of model rows / MSN range / MSN exceptions --------------
TABLE_INTRO = (r"This\s+AD\s+applies\s+to\s+the\s+(?:airplanes|helicopters)\s+"
               r"specified\s+in\s+Table\s+1\s+of\s+this\s+AD\s*[;,]\s*"
               r"(?:certificated|certified)\s+in\s+any\s+category\s*\.?")
TABLE_HEADER = ["For model", "Manufacturer's Serial Number (MSN)", "Except for MSN"]


def parse_table(block):
    """Strict 3-column table; every line must be consumed or the rule is refused."""
    body = block["body"]
    lines = [(m.start(), m.end(), m[0].strip())
             for m in re.finditer(r"[^\n]+", body) if m[0].strip()]
    if len(lines) < 2 or not re.fullmatch(TABLE_INTRO, lines[0][2], re.I):
        return None, "UNSUPPORTED_MODEL_OR_TABLE_SYNTAX"
    if not re.fullmatch(r"Table\s+1", lines[1][2], re.I):
        return None, "UNSUPPORTED_TABLE_SYNTAX"
    header = [clean(line[2]) for line in lines[2:5]]
    if [h.casefold() for h in header] != [h.casefold() for h in TABLE_HEADER]:
        return None, "UNSUPPORTED_TABLE_HEADER"
    cells = lines[5:]
    if not cells or len(cells) % 3:
        return None, "TABLE_ROWS_NOT_ALIGNED"
    rows, seen = [], set()
    for i in range(0, len(cells), 3):
        model_cell, range_cell, except_cell = cells[i:i + 3]
        model_match = re.fullmatch(r"(?:Model\s+)?(.+?)\s+(?:series\s+)?"
                                   r"(?:airplanes|helicopters)", model_cell[2], re.I)
        models = parse_models(model_match[1]) if model_match else None
        include = number_set(range_cell[2])
        exclude = number_set(except_cell[2])
        if not models or include is None or exclude is None:
            return None, "UNSUPPORTED_TABLE_CELL"
        # An exception outside its own row's range signals misaligned cells.
        if any(not (in_ranges(lo, include) and in_ranges(hi, include))
               for lo, hi in exclude):
            return None, "TABLE_EXCEPTION_OUTSIDE_ROW_RANGE"
        if seen & set(models):
            return None, "MODEL_IN_MULTIPLE_TABLE_ROWS"
        seen |= set(models)
        rows.append({
            "models": models,
            "identifier_predicate": {
                "field": "manufacturer_serial_number", "source_label": TABLE_HEADER[1],
                "include_ranges_inclusive": include,
                "exclude_ranges_inclusive": exclude,
                "numeric_only": True,
            },
            "evidence": sub_evidence(block, model_cell[0], except_cell[1]),
        })
    return {
        "models": [m for row in rows for m in row["models"]],
        "model_comparison": "EXACT_NORMALIZED_ONLY",
        "series_word_present": True,
        "identifier_predicate": None,
        "table_rows": rows,
        "external_conditions": [],
        "evidence": block["evidence"],
    }, None


def parse_block(block):
    if not block["boundary_found"]:
        return None, "UNCONFIRMED_BLOCK_BOUNDARY"
    text = clean(block["body"])
    if re.match(r"This\s+AD\s+applies\s+to\s+the\s+\w+\s+specified\s+in\s+Table",
                text, re.I):
        return parse_table(block)
    # Full-string grammar: every unconsumed condition causes refusal.
    head = re.fullmatch(
        r"(?:All\s+)?Model\s+(.+?)\s+(series\s+)?"
        r"(airplanes|helicopters)\b(.*)", text, re.I,
    ) or re.fullmatch(
        # Aircraft word omitted, e.g. "Model 600N, serial numbers ..."
        r"(?:All\s+)?Model\s+([A-Z0-9][A-Z0-9 -]*?)()()\s*,\s*"
        r"((?:serial|line)\s+numbers\b.*)", text, re.I,
    )
    if not head:
        return None, "UNSUPPORTED_MODEL_OR_TABLE_SYNTAX"
    models = parse_models(head[1])
    if not models:
        return None, "UNSUPPORTED_MODEL_LIST"
    tail = head[4].strip()
    tail = re.sub(r"[,;]?\s*(?:certificated|certified)\s+in\s+any\s+category",
                  "", tail, flags=re.I)
    tail, conditions = peel_external_conditions(tail, block)
    if tail is None:
        return None, conditions
    tail = tail.strip(" ,;.")
    predicate = None
    if tail:
        predicate, reason = parse_identifier(tail)
        if reason:
            return None, reason
    return {
        "models": models,
        "model_comparison": "EXACT_NORMALIZED_ONLY",
        "series_word_present": bool(head[2]),
        "identifier_predicate": predicate,
        "external_conditions": conditions,
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
        "parser_version": PARSER_VERSION,
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


def evaluate(parsed, configuration, allow_historical=False):
    """Compare quoted proposed scope; historical comparison requires explicit opt-in."""
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
    historical = not parsed["dataset_history"]["is_latest_observed"]
    if historical and not allow_historical:
        return stop("NOT_LATEST_OBSERVED_RECORD_IN_CSV")
    if parsed["parse_status"] != "SUPPORTED_TEXT_GRAMMAR":
        answer["reason_codes"].extend(parsed["reasons"])
        return stop("APPLICABILITY_NOT_FULLY_PARSED")
    answer['external_conditions'] = parsed['rule'].get('external_conditions', [])
    if not configuration.get("model"):
        answer["missing_fields"] = ["model"]
        return stop("CONFIGURATION_INCOMPLETE", "NEEDS_CLARIFICATION")
    if configuration.get("make"):
        if not parsed["make"]:
            return stop("SOURCE_MAKE_MISSING")
        make_match = make_value(configuration["make"]) == parsed["make"]
        answer["checks"].append({
            "field": "make", "result": "MATCH" if make_match else "NO_MATCH",
            "evidence": parsed["make_evidence"],
        })
        if not make_match:
            return stop("MAKE_MISMATCH_OR_UNREGISTERED_ALIAS", "NEEDS_CLARIFICATION")
    else:
        answer["checks"].append({"field": "make", "result": "NOT_PROVIDED",
                                "note": "Manufacturer omitted; exact model comparison required."})
    scope = compare_scope(parsed["rule"], configuration)
    answer["checks"].extend(scope["checks"])
    for key in ("missing_fields", "required_supplements"):
        if scope.get(key):
            answer[key] = scope[key]
    if scope["decision"] == "UNKNOWN":
        answer["reason_codes"].extend(scope["reason_codes"][1:])
        return stop(scope["reason_codes"][0], "NEEDS_CLARIFICATION")
    answer["scope_decision"] = scope["decision"]
    answer["status"] = "TEXT_SCOPE_COMPARISON_ONLY"
    answer["reason_codes"] = (["PROPOSED_SCOPE_ONLY_EFFECTIVENESS_UNVERIFIED"]
                              + scope["reason_codes"])
    if historical:
        answer["status"] = "HISTORICAL_TEXT_SCOPE_ONLY"
        answer["reason_codes"] = (["HISTORICAL_PROPOSAL_TEXT_ONLY_NOT_CURRENT"]
                                  + scope["reason_codes"])
        answer["historical_text_only"] = True
    return answer


def read_identifier(predicate, supplied):
    """Return (int value, None) or (None, reason). Never guesses a format."""
    text = str(supplied).strip().upper()
    if not predicate.get("prefix"):
        if not re.fullmatch(r"[0-9]+", text):
            return None, "IDENTIFIER_MUST_BE_NUMERIC"
        return int(text), None
    match = re.fullmatch(re.escape(predicate["prefix"])
                         + r"([0-9]{%d})" % predicate["digits"], text)
    if match:
        return int(match[1]), None
    if re.fullmatch(r"[0-9]+", text):
        # "003" could be RN003 or another series: ask, do not assume the prefix.
        return None, "IDENTIFIER_PREFIX_REQUIRED"
    # "RN 003", "RN3", "RX003", ...: separator, padding or prefix differs from the text.
    return None, "IDENTIFIER_FORMAT_NOT_EXACTLY_AS_STATED"


def read_attestation(value):
    if value is True or (isinstance(value, str) and value.strip().lower() == "yes"):
        return True
    if value is False or (isinstance(value, str) and value.strip().lower() == "no"):
        return False
    return None


def compare_scope(rule, configuration):
    """Three-valued AND over every stated condition.

    Any condition definitely false -> NO_MATCH. Otherwise any condition unknown ->
    UNKNOWN (with what must be supplied). MATCH only when every condition holds.
    """
    checks, unknown, missing, supplements, false_found = [], [], [], [], False
    model = normalize_model(configuration["model"])
    rows = rule.get("table_rows")
    row = next((r for r in rows if model in r["models"]), None) if rows else None
    model_match = model in rule["models"]
    checks.append({
        "field": "model", "result": "MATCH" if model_match else "UNKNOWN",
        "allowed_exact_tokens": rule["models"],
        "evidence": row["evidence"] if row else rule["evidence"],
    })
    if not model_match:
        # Series membership or a model alias needs an explicit mapping we do not have;
        # without the model we also cannot tell which table row applies.
        return {"decision": "UNKNOWN", "checks": checks,
                "reason_codes": ["MODEL_NOT_EXACTLY_RESOLVED"]}

    predicate = row["identifier_predicate"] if row else rule["identifier_predicate"]
    if predicate:
        field = predicate["field"]
        supplied = configuration.get(field)
        if (field == 'manufacturer_serial_number' and supplied is not None
                and configuration.get('serial_number') is not None
                and str(supplied).strip() != str(configuration['serial_number']).strip()):
            return {"decision": "UNKNOWN", "checks": checks,
                    "reason_codes": ["CONFLICTING_IDENTIFIERS"]}
        if supplied is None or str(supplied).strip() == "":
            missing.append(field)
            unknown.append("IDENTIFIER_REQUIRED")
        else:
            value, reason = read_identifier(predicate, supplied)
            if reason:
                unknown.append(reason)
            else:
                included = in_ranges(value, predicate["include_ranges_inclusive"])
                excluded = in_ranges(value, predicate["exclude_ranges_inclusive"])
                checks.append({
                    "field": field, "value": str(supplied).strip(),
                    "compared_number": value, "prefix": predicate.get("prefix"),
                    "included": included, "excluded": excluded,
                    "result": "MATCH" if included and not excluded else "NO_MATCH",
                    "evidence": row["evidence"] if row else rule["evidence"],
                })
                false_found |= not (included and not excluded)

    answers = configuration.get("external_conditions") or {}
    if not isinstance(answers, dict):
        return {"decision": "UNKNOWN", "checks": checks,
                "reason_codes": ["EXTERNAL_CONDITION_ANSWERS_MALFORMED"]}
    known_ids = {c["id"] for c in rule.get("external_conditions", [])}
    stray = sorted(set(answers) - known_ids)
    if stray:
        # An answer meant for another record/condition must not be silently ignored.
        return {"decision": "UNKNOWN", "checks": checks,
                "reason_codes": ["UNKNOWN_EXTERNAL_CONDITION_ID"],
                "missing_fields": ["external_conditions." + s for s in stray]}
    attested = False
    for cond in rule.get("external_conditions", []):
        value = read_attestation(answers.get(cond["id"]))
        completion_field = cond.get('completion_field')
        if completion_field and completion_field in configuration:
            completion = read_attestation(configuration[completion_field])
            if value is not None and completion is not None and value != completion:
                return {"decision": "UNKNOWN", "checks": checks,
                        "reason_codes": ["EXTERNAL_CONDITION_ANSWERS_CONFLICT"]}
            if completion is not None:
                value = completion
        if value is None:
            unknown.append("EXTERNAL_CONDITION_UNRESOLVED")
            missing.append("external_conditions." + cond["id"])
            supplements.append({k: cond[k] for k in
                                ("id", "question", "document", "document_date", "in_scope_when",
                                 "evidence")})
            continue
        attested = True
        holds = value == cond["in_scope_when"]
        checks.append({
            "field": "external_conditions." + cond["id"], "answer": value,
            "result": "MATCH" if holds else "NO_MATCH",
            "source": "USER_ATTESTED_NOT_VERIFIED", "evidence": cond["evidence"],
        })
        false_found |= not holds

    if false_found:
        decision, reasons = "NO_MATCH", []
    elif unknown:
        decision, reasons = "UNKNOWN", list(dict.fromkeys(unknown))
    else:
        decision, reasons = "MATCH", []
    if attested and decision != "UNKNOWN":
        reasons.append("RELIES_ON_USER_ATTESTED_EXTERNAL_CONDITION")
    result = {"decision": decision, "checks": checks, "reason_codes": reasons}
    if decision == "UNKNOWN":
        result["missing_fields"] = missing
        result["required_supplements"] = supplements
    return result


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
        rule = item["rule"] or {}
        subs = ([r["evidence"] for r in rule.get("table_rows") or []]
                + [c["evidence"] for c in rule.get("external_conditions") or []])
        for ev in subs:
            if rec["raw"][ev["field"]][ev["start"]:ev["end"]] != ev["quote"]:
                raise ValueError("Sub-citation round-trip failed: " + rec["record_id"])
            evidence_count += 1
    counts = {
        "records": len(parsed),
        "explicit_block_records": sum(bool(r["applicability_blocks"]) for r in parsed),
        "supported_text_grammar_records": sum(
            r["parse_status"] == "SUPPORTED_TEXT_GRAMMAR" for r in parsed),
        "supported_with_identifier_predicate": sum(
            bool(r["rule"] and r["rule"]["identifier_predicate"]) for r in parsed),
        "supported_table_rules": sum(
            bool(r["rule"] and r["rule"].get("table_rows")) for r in parsed),
        "supported_prefixed_identifier_rules": sum(
            bool(r["rule"] and (r["rule"]["identifier_predicate"] or {}).get("prefix"))
            for r in parsed),
        "rules_with_external_conditions": sum(
            bool(r["rule"] and r["rule"].get("external_conditions")) for r in parsed),
        "unsupported_reasons": dict(Counter(
            reason for r in parsed for reason in r["reasons"])),
        "validated_citations": evidence_count,
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
                         "models", "identifier_predicate", "table_rows",
                         "external_conditions", "raw_applicability",
                         "expert_review_status"])
        for item in parsed:
            rule = item["rule"] or {}
            writer.writerow([
                item["record_id"], item["docket"], item["parse_status"],
                " | ".join(item["reasons"]), " | ".join(rule.get("models", [])),
                json.dumps(rule.get("identifier_predicate"), ensure_ascii=False),
                json.dumps([{"models": r["models"],
                             "identifier_predicate": r["identifier_predicate"]}
                            for r in rule.get("table_rows") or []] or None,
                           ensure_ascii=False),
                " | ".join(c["id"] + " -> in scope when " + str(c["in_scope_when"])
                           for c in rule.get("external_conditions") or []),
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
    check.add_argument("--condition", action="append", default=[],
                       metavar="ID=yes|no",
                       help="Your answer to an external condition the result asked for.")
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
        if args.condition:
            pairs = [c.split("=", 1) for c in args.condition]
            if any(len(p) != 2 for p in pairs):
                parser.error("--condition must look like ID=yes or ID=no")
            config["external_conditions"] = {k.strip(): v for k, v in pairs}
        result = evaluate(parse_record(candidates[0]), config)
        print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
