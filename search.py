"""Local evidence search with SQLite FTS5 and conservative configuration checks.

Two routes, chosen from the question:
* DOCKET_LOOKUP: the question names a docket that exists in the index. All versions
  of that docket are returned, newest first. Make/model do not hide the record,
  because the user named it; the configuration check decides what can be said.
* KEYWORD_COVERAGE: otherwise, ranked BM25 over passages, but a passage is only a
  hit when it covers at least COVERAGE_MIN of the meaningful question terms.
  That threshold is the safety valve: without it, OR matching would return
  unrelated documents.
A docket-shaped identifier that is not in the index is refused, never replaced by
a similar-looking search.
"""
import argparse
import hashlib
import json
import math
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from answerability import assess
from applicability import evaluate, load_records, normalize_model, parse_record
from prepare_data import make_value

INPUT = "data/processed/records.jsonl"
DATABASE = "data/search/search.sqlite3"
SCHEMA_VERSION = "2"
COVERAGE_MIN = 0.6
FIELDS = ("Subject Heading", "Summary", "Comments",
          "Link for Submitting Public Comment")
FIELD_PRIORITY = {"Summary": 0, "Comments": 1, "Subject Heading": 2,
                  "Link for Submitting Public Comment": 3}
STOPWORDS = set("""
the a an of for to in on and or with is are does do which what how find show
me about related applicable apply applies serial number numbers model engine
aircraft airplane airplanes helicopter helicopters directives directive ad
proposals proposal nprm sn
my our your i we it its this that these those be been can could should would
need needs any there by at from has have affect affected affects cover covers
covered include included including
match matches matched stated summary evidence active record records give using
based whether prove confirm establish enough alone exact
""".split())
CHINESE_INTENTS = (
    "有哪些相关提案", "有哪些相关规定", "有哪些相关指令", "有哪些", "查找",
    "搜索", "查询", "相关", "提案", "适航指令", "指令", "适用于", "适用",
    "是否", "哪些", "的", "请", "帮我", "型号", "发动机", "飞机",
)
TRANSLATIONS = {
    "腐蚀": "corrosion", "裂纹": "crack", "检查": "inspection",
    "撤回": "withdraw", "燃油": "fuel",
}
VALUE = r"(?:is\s+)?[:：#=]?\s*([A-Za-z]*\d[A-Za-z0-9-]*)"  # must contain a digit
IDENTIFIER = re.compile(
    r"(?:序列号|(?<![A-Za-z])serial(?:\s+(?:numbers?|no\.?))?|(?<![A-Za-z])s/n)\s*" + VALUE
    + r"|(?:生产线号|(?<![A-Za-z])line\s+(?:numbers?|no\.?)|(?<![A-Za-z])l/n)\s*" + VALUE,
    re.I,
)
# Shape only: used to catch identifiers that LOOK like dockets but are not indexed.
DOCKET_SHAPE = re.compile(
    r"(?<![A-Za-z0-9-])(?:FAA-\d{4}-\d+(?:R\d+)?|\d{2,4}-[A-Z]{2,4}-\d+-AD)(?:-[A-Z])?(?![A-Za-z0-9_-])",
    re.I)
AD_REFERENCE = re.compile(r"(?<![A-Za-z0-9-])(\d{4}-\d{2}-\d{2})(?![A-Za-z0-9-])")


def input_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def chunks(text, size=1200, overlap=160):
    for start in range(0, len(text), size - overlap):
        end = min(start + size, len(text))
        if text[start:end].strip():
            yield start, end, text[start:end]
        if end == len(text):
            break


def build_index(input_path=INPUT, database=DATABASE):
    records = load_records(input_path)
    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Build separately, then replace only after the transaction succeeds.
    temporary = path.with_suffix(".building")
    if temporary.exists():
        temporary.unlink()
    connection = sqlite3.connect(temporary)
    count = 0
    try:
        connection.executescript("""
            CREATE TABLE manifest (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE documents (
                id TEXT PRIMARY KEY, make TEXT, latest INTEGER NOT NULL,
                docket TEXT NOT NULL, pub_date TEXT, kind TEXT,
                record_json TEXT NOT NULL, parsed_json TEXT NOT NULL
            );
            CREATE INDEX document_docket ON documents(docket);
            CREATE TABLE models (
                doc_id TEXT NOT NULL, value TEXT NOT NULL,
                PRIMARY KEY(doc_id, value)
            );
            CREATE INDEX model_value ON models(value);
            CREATE VIRTUAL TABLE passages USING fts5(
                doc_id UNINDEXED, field UNINDEXED, start UNINDEXED, end UNINDEXED,
                content, identifiers, tokenize='porter unicode61'
            );
        """)
        for rec in records:
            parsed = parse_record(rec)
            connection.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (rec["record_id"], rec["normalized"]["make"],
                 int(rec["dataset_history"]["is_latest_observed"]),
                 rec["normalized"]["docket"], rec["normalized"]["publication_date"],
                 rec["normalized"]["document_kind"],
                 json.dumps(rec, ensure_ascii=False),
                 json.dumps(parsed, ensure_ascii=False)),
            )
            models = {
                normalize_model(item["value"])
                for item in rec["normalized"]["models_from_column"]
                + rec["normalized"]["model_candidates"]
            }
            if parsed["rule"]:
                models.update(parsed["rule"]["models"])
            connection.executemany(
                "INSERT INTO models VALUES (?, ?)",
                [(rec["record_id"], model) for model in sorted(models)],
            )
            identifiers = " ".join([
                rec["normalized"]["docket"],
                rec["normalized"]["make"] or "",
                rec["raw"]["AD Number"], *sorted(models),
            ])
            for field in FIELDS:
                text = rec["raw"][field]
                if field == FIELDS[-1] and rec["source"]["url"]:
                    continue
                for start, end, content in chunks(text):
                    connection.execute(
                        "INSERT INTO passages VALUES (?, ?, ?, ?, ?, ?)",
                        (rec["record_id"], field, start, end, content, identifiers),
                    )
                    count += 1
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "input_sha256": input_hash(input_path),
            "records": str(len(records)), "passages": str(count),
        }
        connection.executemany("INSERT INTO manifest VALUES (?, ?)", manifest.items())
        connection.commit()
        connection.execute("INSERT INTO passages(passages) VALUES ('integrity-check')")
    finally:
        connection.close()
    temporary.replace(path)
    return {"records": len(records), "passages": count, "database": str(path)}


def quote_fts(value):
    return '"' + value.replace('"', '""') + '"'


def extract_dockets(text, known_dockets):
    """Return (canonical known dockets, unknown docket-shaped strings, remaining text)."""
    found, remaining = [], text
    # Longest first so "X-W" is taken before "X".
    for docket in sorted(known_dockets, key=lambda d: (-len(d), d)):
        pattern = re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(docket) + r"(?![A-Za-z0-9_-])", re.I)
        if pattern.search(remaining):
            found.append(docket)
            remaining = pattern.sub(" ", remaining)
    unknown = []
    for match in DOCKET_SHAPE.finditer(remaining):
        unknown.append(match.group(0))
    remaining = DOCKET_SHAPE.sub(" ", remaining)
    return found, unknown, remaining


def query_plan(query, configuration, known_models, known_dockets=()):
    """Recognize identifiers and a small explicit vocabulary; refuse unexplained Chinese terms."""
    config = {k: str(v).strip() for k, v in configuration.items()
              if v is not None and str(v).strip()}
    inferred = {}
    for match in IDENTIFIER.finditer(query):
        field = "serial_number" if match[1] else "line_number"
        value = match[1] or match[2]
        if field in inferred and inferred[field] != value:
            return None, "CONFLICTING_IDENTIFIERS"
        inferred[field] = value
    remaining = IDENTIFIER.sub(" ", query)
    for field, value in inferred.items():
        if field in config and config[field] != value:
            return None, "QUERY_CONFIGURATION_CONFLICT"
        config[field] = value
    dockets, unknown, remaining = extract_dockets(remaining, known_dockets)
    ad_references = list(dict.fromkeys(AD_REFERENCE.findall(remaining)))
    remaining = AD_REFERENCE.sub(" ", remaining)
    # Longer models first prevent treating A330 inside A330-941 as a second model.
    found = []
    for model in sorted(known_models, key=lambda x: (-len(x), x)):
        pattern = re.compile(r"(?<![A-Za-z0-9-])" + re.escape(model)
                             + r"(?![A-Za-z0-9-])", re.I)
        if pattern.search(remaining):
            found.append(model)
            remaining = pattern.sub(" ", remaining)
    if len(found) > 1:
        return None, "MULTIPLE_MODELS_NEED_CLARIFICATION"
    if found:
        if config.get("model") and normalize_model(config["model"]) != found[0]:
            return None, "QUERY_CONFIGURATION_CONFLICT"
        config["model"] = found[0]
    for phrase in CHINESE_INTENTS:
        remaining = remaining.replace(phrase, " ")
    for phrase, english in TRANSLATIONS.items():
        remaining = remaining.replace(phrase, " " + english + " ")
    if re.search(r"[\u3400-\u9fff]", remaining):
        return None, "UNSUPPORTED_QUERY_LANGUAGE_OR_TERMS"
    words = re.findall(r"[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*", remaining)
    terms = list(dict.fromkeys(w for w in words if w.lower() not in STOPWORDS))
    # Strip manufacturer names already represented by an explicit filter.
    if config.get("make"):
        make_words = set(re.findall(r"[a-z0-9]+", config["make"].lower()))
        terms = [w for w in terms if w.lower() not in make_words]
    if len(terms) > 16:
        return None, "QUERY_TOO_COMPLEX"
    if config.get("model"):
        terms.insert(0, normalize_model(config["model"]))
    # An AD number named in the question must appear in the passage.
    required = list(ad_references)
    terms = list(dict.fromkeys(required + terms))
    if not terms and not dockets and not unknown:
        return None, "SEARCH_TERMS_REQUIRED"
    return {
        "configuration": config,
        "lexical_terms": terms,
        "required_terms": required,
        "identifiers": {"dockets": dockets, "ad_references": ad_references,
                        "unknown": unknown},
        "fts_expression": " OR ".join(quote_fts(term) for term in terms),
        "coverage_min": COVERAGE_MIN,
        "mode": "LIMITED_BILINGUAL_LEXICAL_SEARCH",
    }, None


class SearchEngine:
    def __init__(self, input_path=INPUT, database=DATABASE):
        if not Path(database).is_file():
            raise ValueError("Search index missing; run: python search.py build")
        self.connection = sqlite3.connect(
            Path(database).resolve().as_uri() + "?mode=ro", uri=True)
        self.connection.row_factory = sqlite3.Row
        try:
            manifest = dict(self.connection.execute("SELECT key, value FROM manifest"))
            if (manifest.get("schema_version") != SCHEMA_VERSION
                    or manifest.get("input_sha256") != input_hash(input_path)):
                raise ValueError("Search index stale; run: python search.py build")
            self.models = [r[0] for r in self.connection.execute(
                "SELECT DISTINCT value FROM models")]
            self.makes = [r[0] for r in self.connection.execute(
                "SELECT DISTINCT make FROM documents WHERE make IS NOT NULL")]
            self.dockets = [r[0] for r in self.connection.execute(
                "SELECT DISTINCT docket FROM documents")]
        except Exception:
            self.connection.close()
            raise

    def close(self):
        self.connection.close()

    # ---- routes ---------------------------------------------------------
    def _docket_hits(self, dockets, limit):
        hits = []
        for docket in dockets:
            rows = self.connection.execute(
                "SELECT id FROM documents WHERE docket=? "
                "ORDER BY latest DESC, pub_date DESC, id", (docket,)).fetchall()
            hits.extend(r[0] for r in rows)
        return hits[:limit]

    def _keyword_hits(self, plan, limit, include_history):
        terms, required = plan["lexical_terms"], plan["required_terms"]
        config = plan["configuration"]
        where, params = [], []
        if not include_history:
            where.append("d.latest = 1")
        if config.get("make"):
            where.append("d.make = ?")
            params.append(make_value(config["make"]))
        if config.get("model"):
            where.append("EXISTS (SELECT 1 FROM models m WHERE m.doc_id=d.id AND m.value=?)")
            params.append(normalize_model(config["model"]))
        extra = (" AND " + " AND ".join(where)) if where else ""
        base = "FROM passages JOIN documents d ON d.id=passages.doc_id WHERE passages MATCH ?"
        term_rows = {}
        for term in terms:
            rows = self.connection.execute(
                "SELECT passages.rowid " + base + extra, [quote_fts(term), *params])
            term_rows[term] = {r[0] for r in rows}
        rows = self.connection.execute(
            "WITH ranked AS MATERIALIZED (SELECT passages.rowid AS rid, passages.doc_id AS doc_id, "
            "passages.field AS field, passages.start AS start, passages.end AS end, "
            "passages.content AS content, bm25(passages, 0, 0, 0, 0, 1, 0.25) AS score "
            + base + extra + ") SELECT * FROM ranked",
            [plan["fts_expression"], *params]).fetchall()
        need = max(1, math.ceil(COVERAGE_MIN * len(terms)))
        by_doc = defaultdict(list)
        for row in rows:
            matched = [t for t in terms if row["rid"] in term_rows[t]]
            if any(t not in matched for t in required) or len(matched) < need:
                continue
            by_doc[row["doc_id"]].append((-len(matched), row["score"], row["field"],
                                          row["start"], row["end"], row["content"], matched))
        ranked = sorted(by_doc.items(), key=lambda kv: (min(kv[1])[:2], kv[0]))
        return [(doc, sorted(passages)[:3]) for doc, passages in ranked[:limit]]

    def _versions(self, docket):
        rows = self.connection.execute(
            "SELECT id, pub_date, kind, latest FROM documents WHERE docket=? "
            "ORDER BY latest DESC, pub_date DESC, id", (docket,)).fetchall()
        return [{"record_id": r[0], "publication_date": r[1], "document_kind": r[2],
                 "is_latest": bool(r[3])} for r in rows]

    def _evidence_for(self, rec, passages):
        evidence = []
        for field, start, end, content in passages:
            if rec["raw"][field][start:end] != content:
                raise ValueError("Stored search citation does not match source")
            evidence.append({"field": field, "start": start, "end": end,
                             "quote": content, "source": rec["source"]})
        return evidence

    # ---- public ---------------------------------------------------------
    def search(self, query, configuration=None, limit=5, include_history=False,
               historical_comparison=False):
        started = time.perf_counter()
        response = {
            "status": "REFUSE", "query": query, "results": [],
            "scope": "SUPPLIED_CSV_ONLY", "operational_answer": "REFUSE",
            "legal_applicability": "UNVERIFIED",
            "history_included": include_history,
            "warning": "Search hits are evidence candidates, not applicable or effective ADs.",
        }

        def finish():
            response["retrieval_ms"] = round((time.perf_counter() - started) * 1000, 3)
            return response

        if not isinstance(query, str) or len(query) > 1000 or not 1 <= limit <= 20:
            response["reason_codes"] = ["INVALID_QUERY_OR_LIMIT"]
            return finish()
        plan, error = query_plan(query, configuration or {}, self.models, self.dockets)
        if error:
            response["reason_codes"] = [error]
            response["status"] = "NEEDS_CLARIFICATION"
            return finish()
        response["query_plan"] = plan
        config, ids = plan["configuration"], plan["identifiers"]
        if ids["unknown"]:
            # Never substitute a similar-looking search for an identifier that is not indexed.
            response["reason_codes"] = ["IDENTIFIER_NOT_FOUND"]
            response["unknown_identifiers"] = [
                {"value": value, "did_you_mean": [d for d in self.dockets
                                                  if d.lower().startswith(value.lower())][:5]}
                for value in ids["unknown"]]
            return finish()
        if ids["dockets"]:
            response["route"] = "DOCKET_LOOKUP"
            hits = [(doc, None) for doc in self._docket_hits(ids["dockets"], limit)]
        else:
            response["route"] = "KEYWORD_COVERAGE"
            hits = self._keyword_hits(plan, limit, include_history) if plan["lexical_terms"] else []
        terms = plan["lexical_terms"]
        for doc_id, ranked_passages in hits:
            doc = self.connection.execute(
                "SELECT record_json, parsed_json FROM documents WHERE id=?", (doc_id,)).fetchone()
            rec, parsed = json.loads(doc[0]), json.loads(doc[1])
            if ranked_passages is None:
                rows = self.connection.execute(
                    "SELECT field, start, end, content FROM passages WHERE doc_id=?",
                    (doc_id,)).fetchall()
                def overlap(row):
                    text = row["content"].lower()
                    return -sum(t.lower() in text for t in terms)
                rows = sorted(rows, key=lambda r: (overlap(r), FIELD_PRIORITY[r["field"]], r["start"]))[:3]
                passages = [(r["field"], r["start"], r["end"], r["content"]) for r in rows]
                text = " ".join(p[3] for p in passages).lower()
                matched = [t for t in terms if t.lower() in text]
            else:
                passages = [(p[2], p[3], p[4], p[5]) for p in ranked_passages]
                matched = ranked_passages[0][6]
            missing = [t for t in terms if t not in matched]
            title = rec["raw"]["Subject Heading"]
            versions = self._versions(rec["normalized"]["docket"])
            response["results"].append({
                "record_id": rec["record_id"], "docket": parsed["docket"],
                "title": {"text": title, "evidence": {
                    "field": "Subject Heading", "start": 0, "end": len(title),
                    "quote": title, "source": rec["source"]}},
                "match": {"via": response["route"],
                          "matched_terms": matched, "unmatched_terms": missing,
                          "term_coverage": round(len(matched) / len(terms), 2) if terms else None},
                "revision": {
                    "versions_in_csv": len(versions),
                    "this_is_latest": parsed["dataset_history"]["is_latest_observed"],
                    "versions": versions,
                    "supersession_mentions": [
                        {**m["evidence"], "kind": m["kind"], "source": rec["source"]}
                        for m in rec["extracted"]["revision_mentions"]
                        if m["kind"] in ("proposed_supersession", "supersession_mention_unverified")],
                    "note": "Latest means latest in this CSV, not a verified legal version."},
                # False for summary-only records: they hold no procedural text to quote.
                "full_text_available": bool(rec["extracted"]["proposed_text"]),
                "score_is_probability": False,
                "evidence": self._evidence_for(rec, passages),
                "configuration_check": evaluate(parsed, config, historical_comparison),
            })
        response["status"] = "EVIDENCE_FOUND" if hits else "REFUSE"
        response["reason_codes"] = ([] if hits else ["NO_EVIDENCE_FOR_QUERY_AND_FILTERS"])
        response["historical_comparison"] = historical_comparison
        response["answerability"] = assess(query, response["results"])
        if response["answerability"]["unmet_requests"]:
            # Records were found, but they cannot answer what was asked. Say so explicitly.
            response["status"] = "INSUFFICIENT_EVIDENCE"
            response["reason_codes"] = [u["code"] for u in response["answerability"]["unmet_requests"]]
        response["result_limit"] = limit
        response["not_exhaustive"] = True
        return finish()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=INPUT)
    parser.add_argument("--database", default=DATABASE)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    query = sub.add_parser("query")
    query.add_argument("text")
    for key in ("make", "model", "serial-number", "line-number"):
        query.add_argument("--" + key)
    query.add_argument("--include-history", action="store_true")
    query.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    try:
        if args.command == "build":
            output = build_index(args.input, args.database)
        else:
            engine = SearchEngine(args.input, args.database)
            try:
                config = {key: getattr(args, key) for key in
                          ("make", "model", "serial_number", "line_number")}
                output = engine.search(args.text, config, args.limit, args.include_history)
            finally:
                engine.close()
        print(json.dumps(output, ensure_ascii=True, indent=2))
    except (ValueError, sqlite3.Error, OSError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
