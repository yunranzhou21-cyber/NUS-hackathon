"""Constrained query understanding.

An optional LLM may only turn the user's question into structured fields. Every
value it returns must appear in the user's own text, otherwise it is dropped.
The LLM never judges applicability and never sees the documents. If no model is
configured, or the model fails, a deterministic local interpreter is used.

Optional environment variables (the key is never written to disk or logs):
    AI_API_KEY, AI_BASE_URL (default https://api.openai.com/v1), AI_MODEL
"""
import json
import os
import re
import urllib.error
import urllib.request

from search import IDENTIFIER

INTENTS = ("search_documents", "check_proposed_scope", "find_history")
FIELDS = ("make", "model", "serial_number", "line_number")
HISTORY_WORDS = re.compile(r"\b(history|historical|older|previous|superseded|earlier versions?)\b", re.I)
SYSTEM_PROMPT = (
    "You convert an aviation-regulation search question into JSON. Use ONLY "
    "words present in the question. Never add knowledge of your own. Return "
    'exactly: {"intent": one of ["search_documents","check_proposed_scope",'
    '"find_history"], "make": string|null, "model": string|null, '
    '"serial_number": string|null, "line_number": string|null}. '
    "Use null for anything not stated. Output JSON only."
)


def squash(value):
    return re.sub(r"\s+", "", value).lower()


def rules_interpret(query, known_makes=()):
    """Deterministic fallback: identifiers, indexed manufacturers, intent. No guessing."""
    fields = {}
    for name in sorted(known_makes, key=len, reverse=True):
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", query, re.I):
            fields["make"] = name
            break
    for match in IDENTIFIER.finditer(query):
        if match[1]:
            fields.setdefault("serial_number", match[1])
        else:
            fields.setdefault("line_number", match[2])
    intent = "check_proposed_scope" if fields else "search_documents"
    if HISTORY_WORDS.search(query):
        intent = "find_history"
    return {"intent": intent, "fields": fields}


def validate(raw, query):
    """Return (intent, fields, rejected) or None when the output is unusable."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("intent") not in INTENTS:
        return None
    fields, rejected = {}, []
    haystack = squash(query)
    for key in FIELDS:
        value = data.get(key)
        if value is None:
            continue
        if (not isinstance(value, str) or not value.strip() or len(value) > 60
                or squash(value) not in haystack):
            rejected.append({"field": key, "value": str(value)[:60],
                             "reason": "NOT_FOUND_IN_USER_TEXT"})
            continue
        fields[key] = value.strip()
    return data["intent"], fields, rejected


def openai_compatible_provider():
    key = os.environ.get("AI_API_KEY")
    if not key:
        return None
    base = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("AI_MODEL", "gpt-4o-mini")

    def call(query):
        body = json.dumps({
            "model": model, "temperature": 0,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": query}],
        }).encode("utf-8")
        request = urllib.request.Request(
            base + "/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + key})
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.load(response)
        return payload["choices"][0]["message"]["content"]

    call.model = model
    return call


def get_provider():
    return openai_compatible_provider()


def interpret(query, provider=None, known_makes=()):
    """Always returns a usable interpretation; `source` says who produced it."""
    result = rules_interpret(query, known_makes)
    result.update({"source": "local_rules", "rejected": [], "notice": None,
                   "sent_to_external_service": False})
    if provider is None:
        return result
    result["sent_to_external_service"] = True
    try:
        checked = validate(provider(query), query)
    except (OSError, urllib.error.URLError, KeyError, IndexError, ValueError, TypeError):
        result["notice"] = "AI_CALL_FAILED_USING_LOCAL_RULES"
        return result
    if checked is None:
        result["notice"] = "AI_OUTPUT_INVALID_USING_LOCAL_RULES"
        return result
    intent, fields, rejected = checked
    # Deterministic identifiers always win over model output.
    fields.update(result["fields"])
    result.update({"intent": intent, "fields": fields, "rejected": rejected,
                   "source": "llm_validated"})
    return result
