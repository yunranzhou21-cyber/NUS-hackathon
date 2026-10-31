"""Local evidence search with SQLite FTS5 and conservative configuration checks."""
import argparse
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

from applicability import evaluate, load_records, normalize_model, parse_record
from prepare_data import make_value

INPUT = "data/processed/records.jsonl"
DATABASE = "data/search/search.sqlite3"
SCHEMA_VERSION = "1"
FIELDS = ("Subject Heading", "Summary", "Comments",
          "Link for Submitting Public Comment")
STOPWORDS = set("""
the a an of for to in on and or with is are does do which what how find show
me about related applicable apply applies serial number numbers model engine
aircraft airplane airplanes helicopter helicopters directives directive ad
proposals proposal nprm sn
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
IDENTIFIER = re.compile(
    r"(?:序列号|serial\s+(?:number|no\.?)|s/n)\s*[:：#]?\s*([A-Za-z0-9-]+)"
    r"|(?:生产线号|line\s+(?:number|no\.?)|l/n)\s*[:：#]?\s*([A-Za-z0-9-]+)",
    re.I,
)


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
                record_json TEXT NOT NULL, parsed_json TEXT NOT NULL
            );
            CREATE TABLE models (
                doc_id TEXT NOT NULL, value TEXT NOT NULL,
                PRIMARY KEY(doc_id, value)
            );
            CREATE INDEX model_value ON models(value);
            CREATE VIRTUAL TABLE passages USING fts5(
                doc_id UNINDEXED, field UNINDEXED, start UNINDEXED, end UNINDEXED,
                content, identifiers, tokenize='unicode61'
            );
        """)
        for rec in records:
            parsed = parse_record(rec)
            connection.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?)",
                (rec["record_id"], rec["normalized"]["make"],
                 int(rec["dataset_history"]["is_latest_observed"]),
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


def query_plan(query, configuration, known_models):
    """Recognize a small explicit vocabulary; refuse unexplained Chinese terms."""
    config = {k: str(v).strip() for k, v in configuration.items()
              if v is not None and str(v).strip()}
    inferred = {}
    remaining = query
    for match in IDENTIFIER.finditer(query):
        field = "serial_number" if match[1] else "line_number"
        value = match[1] or match[2]
        if field in inferred and inferred[field] != value:
            return None, "CONFLICTING_IDENTIFIERS"
        inferred[field] = value
    remaining = IDENTIFIER.sub(" ", remaining)
    for field, value in inferred.items():
        if field in config and config[field] != value:
            return None, "QUERY_CONFIGURATION_CONFLICT"
        config[field] = value
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
    if not terms:
        return None, "SEARCH_TERMS_REQUIRED"
    return {
        "configuration": config,
        "lexical_terms": terms,
        "fts_expression": " AND ".join(quote_fts(term) for term in terms),
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
        except Exception:
            self.connection.close()
            raise

    def close(self):
        self.connection.close()

    def search(self, query, configuration=None, limit=5, include_history=False):
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
        plan, error = query_plan(query, configuration or {}, self.models)
        if error:
            response["reason_codes"] = [error]
            response["status"] = "NEEDS_CLARIFICATION"
            return finish()
        response["query_plan"] = plan
        where = ["passages MATCH ?"]
        params = [plan["fts_expression"]]
        config = plan["configuration"]
        if not include_history:
            where.append("d.latest = 1")
        if config.get("make"):
            where.append("d.make = ?")
            params.append(make_value(config["make"]))
        if config.get("model"):
            where.append("EXISTS (SELECT 1 FROM models m WHERE m.doc_id=d.id AND m.value=?)")
            params.append(normalize_model(config["model"]))
        # SQL groups ranked passages into distinct documents without arbitrary hit caps.
        sql = """
            WITH ranked AS MATERIALIZED (
                SELECT passages.doc_id, passages.field, passages.start, passages.end,
                       passages.content, bm25(passages, 0, 0, 0, 0, 1, 0.25) AS score
                FROM passages JOIN documents d ON d.id=passages.doc_id
                WHERE """ + " AND ".join(where) + """
            )
            SELECT doc_id, min(score) AS best_score FROM ranked
            GROUP BY doc_id ORDER BY best_score, doc_id LIMIT ?
        """
        hits = self.connection.execute(sql, [*params, limit]).fetchall()
        for hit in hits:
            doc = self.connection.execute(
                "SELECT record_json, parsed_json FROM documents WHERE id=?",
                (hit["doc_id"],)).fetchone()
            rec, parsed = json.loads(doc[0]), json.loads(doc[1])
            passages = self.connection.execute("""
                SELECT field, start, end, content,
                       bm25(passages, 0, 0, 0, 0, 1, 0.25) AS score
                FROM passages WHERE passages MATCH ? AND doc_id=?
                ORDER BY score, rowid LIMIT 3
            """, (plan["fts_expression"], hit["doc_id"])).fetchall()
            evidence = []
            for passage in passages:
                field, start, end = passage["field"], int(passage["start"]), int(passage["end"])
                quote = passage["content"]
                if rec["raw"][field][start:end] != quote:
                    raise ValueError("Stored search citation does not match source")
                evidence.append({
                    "field": field, "start": start, "end": end, "quote": quote,
                    "source": rec["source"],
                })
            title = rec["raw"]["Subject Heading"]
            response["results"].append({
                "record_id": rec["record_id"], "docket": parsed["docket"],
                "title": {"text": title, "evidence": {
                    "field": "Subject Heading", "start": 0, "end": len(title),
                    "quote": title, "source": rec["source"]}},
                "rank_score": hit["best_score"],
                "score_is_probability": False,
                "evidence": evidence,
                "configuration_check": evaluate(parsed, config),
            })
        response["status"] = "EVIDENCE_FOUND" if hits else "REFUSE"
        response["reason_codes"] = ([] if hits else ["NO_EVIDENCE_FOR_QUERY_AND_FILTERS"])
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
