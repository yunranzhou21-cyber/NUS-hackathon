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
import threading
import urllib.error
import urllib.parse
import urllib.request

from search import IDENTIFIER

INTENTS = ("search_documents", "check_proposed_scope", "find_history")
FIELDS = ("make", "model", "serial_number", "line_number")
HISTORY_WORDS = re.compile(r"\b(history|historical|older|previous|earlier versions?)\b", re.I)
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


_lock = threading.Lock()
_runtime = {}  # in memory only; never written to disk, logs, or API responses
DEFAULT_BASE = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


def validate_settings(api_key, model, base_url):
    """Return (clean_settings, error_code). Never echoes the key."""
    if (not isinstance(api_key, str) or not 8 <= len(api_key) <= 300
            or re.search(r"[\s\x00-\x1f]", api_key)):
        return None, "INVALID_API_KEY_FORMAT"
    model = (model or DEFAULT_MODEL).strip()
    if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,100}", model):
        return None, "INVALID_MODEL_NAME"
    base = (base_url or DEFAULT_BASE).strip().rstrip("/")
    parts = urllib.parse.urlsplit(base)
    local = parts.hostname in ("127.0.0.1", "localhost")
    # The key travels to this address, so plain http is allowed only for loopback.
    if (parts.scheme not in ("https", "http") or (parts.scheme == "http" and not local)
            or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment):
        return None, "INVALID_BASE_URL"
    return {"api_key": api_key, "model": model, "base_url": base}, None


def set_runtime(settings):
    with _lock:
        _runtime.clear()
        _runtime.update(settings)


def clear_runtime():
    with _lock:
        _runtime.clear()


def status():
    """Safe to send to the browser: no key material."""
    with _lock:
        if _runtime:
            return {"configured": True, "source": "session", "model": _runtime["model"],
                    "host": urllib.parse.urlsplit(_runtime["base_url"]).netloc}
    if os.environ.get("AI_API_KEY"):
        return {"configured": True, "source": "environment",
                "model": os.environ.get("AI_MODEL", DEFAULT_MODEL),
                "host": urllib.parse.urlsplit(
                    os.environ.get("AI_BASE_URL", DEFAULT_BASE)).netloc}
    return {"configured": False, "source": None, "model": None, "host": None}


def test_connection(provider):
    """One tiny call with a fixed harmless question. Returns (ok, reason)."""
    probe = "EMB-120 serial number 100"
    try:
        checked = validate(provider(probe), probe)
    except urllib.error.HTTPError as exc:
        return False, "AUTH_REJECTED" if exc.code in (401, 403) else "SERVICE_ERROR"
    except (OSError, urllib.error.URLError, TimeoutError):
        return False, "UNREACHABLE"
    except (KeyError, IndexError, ValueError, TypeError):
        return False, "UNEXPECTED_RESPONSE"
    return (True, None) if checked else (False, "MODEL_OUTPUT_UNUSABLE")


def openai_compatible_provider():
    with _lock:
        runtime = dict(_runtime)
    if runtime:
        return provider_for(runtime)
    key = os.environ.get("AI_API_KEY")
    if not key:
        return None
    return provider_for({"api_key": key, "model": os.environ.get("AI_MODEL", DEFAULT_MODEL),
                         "base_url": os.environ.get("AI_BASE_URL", DEFAULT_BASE).rstrip("/")})


def provider_for(settings):
    key, base, model = settings["api_key"], settings["base_url"], settings["model"]

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
