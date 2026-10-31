"""Answerability: can the records that were found actually answer what was asked?

Finding the right record is not the same as being able to answer. Someone can name
the correct docket and still ask for something the data does not contain: an exact
torque value, a page number for one sentence, a release-to-service decision.
Answering those from "general knowledge" is exactly the hallucination this project
exists to prevent.

Method: PATTERN_BASED and deterministic. It recognizes a short list of request
kinds (below) and checks the evidence that was returned. It does NOT understand
arbitrary questions. A request kind that is not listed here is not caught, so
"no unmet request detected" must never be read as "the question is answered".
To add a request kind, add a detector and a test with both a phrasing that must be
flagged and a near-miss phrasing that must not.
"""
import re

KINDS = {
    "EXACT_PAGE_UNAVAILABLE": "Exact page number",
    "OPERATIONAL_DECISION_OUT_OF_SCOPE": "Release, airworthiness or compliance decision",
    "REQUESTED_VALUE_NOT_IN_RECORD": "Specific technical value",
    "PROCEDURE_NOT_IN_RECORD": "Procedure or repair steps",
    "EFFECTIVE_STATUS_NOT_ESTABLISHED": "Whether a rule is already in effect",
}

_GAP = r"[^.?!]{0,50}"
PAGE = re.compile(
    r"\b(?:what|which|exact|specific)\b" + _GAP + r"\bpages?\b|\bpage\s+numbers?\b|\bon\s+(?:what|which)\s+page\b", re.I)
RELEASE = re.compile(
    r"\b(?:release|released|return|returned)\b[^.?!]{0,30}\bservice\b|\bairworth(?:y|iness status)\b"
    r"|\bsafe\s+to\s+(?:fly|operate|dispatch)\b|\bcan\s+i\s+(?:fly|operate|dispatch|release)\b"
    r"|\bam\s+i\s+(?:compliant|legal)\b|\b(?:is|are)\s+(?:it|this|my|the)\b[^.?!]{0,25}\b(?:compliant|legal|airworthy)\b"
    r"|\bcompliance\s+status\b", re.I)
VALUE = re.compile(
    r"\b(?:exact|specific|precise|what\s+is\s+the|give\s+me\s+the|state\s+the)\b" + _GAP
    + r"\b(?:torque|pressure|clearance|tolerance|limit|interval|dimension|value|setting)\b"
    r"|\bhow\s+(?:many|much|long)\b[^.?!]{0,30}\b(?:hours|cycles|torque|pressure|time)\b"
    r"|\bin\s+(?:nm|n\W?m|ft\W?lbs?|in\W?lbs?|psi|mm)\b", re.I)
HAS_VALUE = re.compile(
    r"\d[\d,.]*\s*(?:-|\s)?(?:nm|n\W?m|ft\W?lbs?|in\W?lbs?|psi|mm|inch(?:es)?|"
    r"flight\s+(?:hours|cycles)|hours|cycles|days|months)\b", re.I)
PROCEDURE = re.compile(
    r"\b(?:complete|full|detailed|step\W?by\W?step|entire)\b[^.?!]{0,30}\b(?:procedure|instructions?|steps|method|repair)\b"
    r"|\bhow\s+(?:do|to|should|can)\s+(?:i|we|you)\b[^.?!]{0,40}\b(?:repair|fix|replace|perform|carry\s+out)\b", re.I)
EFFECTIVE = re.compile(
    r"\b(?:already|currently|now|officially)\b[^.?!]{0,30}\b(?:superseded|in\s+effect|in\s+force|adopted|finalized|final\s+rule|replaced)\b"
    r"|\b(?:has|have|had)\b[^.?!]{0,20}\b(?:been|become)\b[^.?!]{0,15}\b(?:superseded|adopted|finalized|replaced)\b"
    r"|\bis\s+(?:it|this|that)\s+(?:in\s+effect|in\s+force|final|effective)\b", re.I)
# Document kinds that cannot be an effective final rule.
NOT_FINAL = {"proposal", "supplemental_or_revised_proposal", "withdrawal_notice", "unknown"}


def _first_result_source(results):
    return results[0]["title"]["evidence"]["source"]


def assess(query, results):
    """Return the answerability block. `results` are search results (may be empty)."""
    unmet = []
    if not results or not isinstance(query, str):
        return _block(unmet)
    evidence_text = " ".join(ev["quote"] for r in results for ev in r["evidence"])
    ids = [r["record_id"] for r in results]

    match = PAGE.search(query)
    if match:
        source = _first_result_source(results)
        unmet.append({
            "code": "EXACT_PAGE_UNAVAILABLE", "asked": match.group(0),
            "explanation": ("Only a document-level page range is available "
                            f"({source['published_page_range_raw']}, {source['citation']}). "
                            "Pages are not assigned to individual sentences."),
            "record_ids": ids[:1], "details": {"published_page_range": source["published_page_range"]}})

    match = RELEASE.search(query)
    if match:
        unmet.append({
            "code": "OPERATIONAL_DECISION_OUT_OF_SCOPE", "asked": match.group(0),
            "explanation": ("These records are rulemaking notices. They cannot establish an "
                            "aircraft's compliance or release-to-service status. That decision "
                            "rests with the operator's approved maintenance data and authority."),
            "record_ids": ids[:1]})

    match = VALUE.search(query)
    if match and not HAS_VALUE.search(evidence_text):
        unmet.append({
            "code": "REQUESTED_VALUE_NOT_IN_RECORD", "asked": match.group(0),
            "explanation": ("The returned text names the subject but gives no value with a unit. "
                            "The value was not supplied from memory. Consult the technical source "
                            "the record points to."),
            "record_ids": ids[:1]})

    match = PROCEDURE.search(query)
    if match and not any(r.get("full_text_available") for r in results):
        unmet.append({
            "code": "PROCEDURE_NOT_IN_RECORD", "asked": match.group(0),
            "explanation": ("This record holds the FAA's summary only. The full text with "
                            "procedural steps is not in the supplied data."),
            "record_ids": ids[:1]})

    match = EFFECTIVE.search(query)
    if match and all(r["configuration_check"]["document_kind"] in NOT_FINAL for r in results):
        mentions = [m for r in results for m in r.get("revision", {}).get("supersession_mentions", [])]
        unmet.append({
            "code": "EFFECTIVE_STATUS_NOT_ESTABLISHED", "asked": match.group(0),
            "explanation": ("The supplied records propose or withdraw rules. None is shown to be an "
                            "effective final rule, so it cannot be said that anything is already "
                            "superseded or in effect."),
            "record_ids": ids[:1], "evidence": mentions[:2]})
    return _block(unmet)


def _block(unmet):
    return {
        "verdict": "NOT_ANSWERABLE" if unmet else "NO_UNMET_REQUEST_DETECTED",
        "unmet_requests": unmet,
        "method": "PATTERN_BASED",
        "limits": ("Only the request kinds in answerability.KINDS are detected. "
                   "NO_UNMET_REQUEST_DETECTED does not mean the question has been answered."),
    }
