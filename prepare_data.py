"""Conservative, standard-library-only preparation of the supplied FAA CSV."""
import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

VERSION = "1.0"
AD_RE = re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b")
MODEL_RE = re.compile(
    r"(?<![\w-])(?:"
    r"(?:A3\d{2}|A2\d{2}|B737|737|747|757|767|777|787)(?:-[A-Z0-9]+)?|"
    r"(?:AS|EC|AW|PW|CFM|CF|JT|RB|H|GEnx|GE)\d[A-Z0-9]*(?:-[A-Z0-9]+)*|"
    r"EMB[- ]\d{3}[A-Z0-9]*(?:-[A-Z0-9]+)*|"
    r"AE ?\d{4}[A-Z0-9/]*|"
    r"MBB-BK ?117(?: [A-Z]-\d)?|"
    r"Falcon \d{2,4}[A-Z]*|"
    r"P-180|DC-\d{1,2}(?:-\d+)?|MD-\d{1,2}|"
    r"DHC-\d(?:-\d+)?|CL-\d{3}(?:-\d[A-Z]\d{2})?"
    r")(?![\w-])", re.I
)
ALIASES = {
    "boeing": "Boeing",
    "the boeing company": "Boeing",
    "airbus": "Airbus",
    "airbus sas": "Airbus",
    "leonardo s.p.a.": "Leonardo S.p.A.",
}
# No merger of subsidiaries or former type-certificate holders.
TEXT_COLUMNS = ("Summary", "Subject Heading", "Comments",
                "Link for Submitting Public Comment")
REQUIRED = ("Docket Number", "Summary", "Model", "Make", "Issue Date")


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def date_value(value):
    value = clean(value)
    if not value:
        return None
    try:
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def make_value(value):
    value = clean(value)
    return ALIASES.get(value.casefold(), value) or None


def span(row, field, start=0, end=None):
    text = row.get(field, "")
    end = len(text) if end is None else end
    return {"field": field, "start": start, "end": end,
            "quote": text[start:end]}


def evidence_matches(row, regex, fields=TEXT_COLUMNS, flags=re.I):
    found = []
    for field in fields:
        for match in re.finditer(regex, row.get(field, ""), flags):
            found.append(span(row, field, match.start(), match.end()))
    return found


def clauses(row, field):
    """Restrict model candidates to explicit model/applicability wording."""
    text = row.get(field, "")
    pattern = (
        r"\bApplicability\s*:[\s\S]{0,3000}?(?=\n\s*(?:Note\s+1|Compliance)\s*:|$)"
        r"|\bModels?\s+[^.;\n]{1,900}"
    )
    return [span(row, field, m.start(), m.end())
            for m in re.finditer(pattern, text, re.I)]


def model_candidates(row):
    values = []
    for field in ("Summary", "Subject Heading",
                  "Link for Submitting Public Comment"):
        if field == "Link for Submitting Public Comment" and is_url(row[field]):
            continue
        for clause in clauses(row, field):
            for match in MODEL_RE.finditer(clause["quote"]):
                start = clause["start"] + match.start()
                values.append({
                    "value": clean(match.group()),
                    "scope": "mentioned_only_not_verified_current_applicability",
                    "evidence": span(row, field, start,
                                     clause["start"] + match.end()),
                })
    # Preserve distinct source locations, but avoid overlapping-clause duplicates.
    unique = {}
    for value in values:
        ev = value["evidence"]
        unique[(ev["field"], ev["start"], ev["end"])] = value
    return list(unique.values())


def is_url(value):
    return bool(re.fullmatch(r"https?://[^\s]+", (value or "").strip()))


def classify(row):
    summary = row["Summary"]
    if re.search(r"\b(?:this action withdraws|proposed rule is withdrawn|"
                 r"withdrawing (?:a|the) notice)\b", summary, re.I):
        return "withdrawal_notice", [span(row, "Summary")]
    if re.search(r"\b(?:final rule|adopting a new airworthiness directive)\b",
                 summary, re.I):
        return "needs_review", [span(row, "Summary")]
    if re.search(r"\b(?:SNPRM|supplemental notice|revising a notice of proposed)\b",
                 summary, re.I):
        return "supplemental_or_revised_proposal", [span(row, "Summary")]
    if re.search(r"\b(?:propos\w*|NPRM)\b", summary, re.I):
        return "proposal", [span(row, "Summary")]
    return "unknown", []


def relations(row):
    results = []
    # Keep the whole source sentence; no automatic effective-supersession edge.
    for field in ("Summary", "Comments"):
        text = row[field]
        for m in re.finditer(r"[^.!?\n]*(?:supersed\w*|withdraw\w*)[^.!?\n]*",
                             text, re.I):
            quote = m.group()
            kind = ("supersession_mention_unverified"
                    if re.search("supersed", quote, re.I)
                    else "withdrawal_mention_unverified")
            if re.search(r"\b(?:propos\w*|would|NPRM)\b", quote, re.I):
                kind = ("proposed_supersession" if "supersession" in kind
                        else "proposal_withdrawal_mention")
            results.append({
                "kind": kind,
                "referenced_ad_numbers": sorted(set(AD_RE.findall(quote))),
                "referenced_dockets": sorted(set(re.findall(
                    r"\bFAA-\d{4}-\d+(?:R\d+)?\b", quote))),
                "effective_relationship_verified": False,
                "evidence": span(row, field, m.start(), m.end()),
            })
    return results


def record(row, ordinal, line_start, line_end, filename, file_hash):
    link = row["Link for Submitting Public Comment"]
    kind, kind_evidence = classify(row)
    structured_models = [
        {"value": clean(m.group()), "evidence": span(
            row, "Model", m.start(), m.end())}
        for m in re.finditer(r"[^|]+", row["Model"]) if clean(m.group())
    ]
    candidates = model_candidates(row)
    applicability = []
    for field in ("Summary", "Link for Submitting Public Comment"):
        if is_url(row[field]):
            continue
        applicability.extend(clauses(row, field))
    serial_evidence = evidence_matches(
        row, r"[^.\n]*(?:serial numbers?|\bS/N\b)[^.\n]*",
        fields=("Summary", "Link for Submitting Public Comment"))
    ad_mentions = evidence_matches(row, AD_RE.pattern,
                                   fields=("Summary", "AD Number"))
    page_match = re.search(r"(\d+)\s*[-–]\s*(\d+)", row["Page Number"])
    pages = ([int(page_match[1]), int(page_match[2])]
             if page_match else None)
    flags = ["SUMMARY_OR_PROPOSED_TEXT_NOT_VERIFIED_EFFECTIVE_RULE",
             "APPLICABILITY_REQUIRES_REVIEW"]
    if not structured_models:
        flags.append("MODEL_COLUMN_MISSING")
    if not clean(row["Summary"]):
        flags.append("SUMMARY_MISSING")
    if re.search(r"\bcertain\b", row["Summary"], re.I):
        flags.append("CERTAIN_SCOPE_UNRESOLVED")
    if re.search(r"(?:reduces?|expands?|expanding|revises?).{0,35}applicability",
                 row["Summary"], re.I):
        flags.append("APPLICABILITY_CHANGED")
    if serial_evidence:
        flags.append("SERIAL_MENTION_NOT_PARSED_AS_EXECUTABLE_RULE")
    if link.strip() and not is_url(link):
        flags.append("COMMENT_LINK_COLUMN_CONTAINS_TEXT")
    if kind == "withdrawal_notice" and row["Status"] == "Current":
        flags.append("CURRENT_METADATA_DOES_NOT_MEAN_ACTIVE_PROPOSAL")
    for field in ("Issue Date", "Citation Publish Date", "Comments Due Date"):
        if row[field].strip() and date_value(row[field]) is None:
            flags.append("INVALID_DATE:" + field)
    return {
        "record_id": f"row-{ordinal:04d}",
        "source": {
            "file": filename, "file_sha256": file_hash,
            "csv_record_number": ordinal,
            "physical_lines": [line_start, line_end],
            "docket": clean(row["Docket Number"]),
            "citation": row["Citation"],
            "published_page_range_raw": row["Page Number"],
            "published_page_range": pages,
            "page_precision": "document_range_only_not_exact_claim_page",
            "url": link.strip() if is_url(link) else None,
        },
        "normalized": {
            "docket": clean(row["Docket Number"]),
            "make": make_value(row["Make"]),
            "make_evidence": span(row, "Make"),
            "models_from_column": structured_models,
            "model_candidates": candidates,
            "product_types": [clean(x) for x in row["Product Type"].split("|")
                              if clean(x)],
            "product_subtype": clean(row["Product Subtype"]) or None,
            "issue_date": date_value(row["Issue Date"]),
            "publication_date": date_value(row["Citation Publish Date"]),
            "comments_due_date": date_value(row["Comments Due Date"]),
            "ad_number_metadata": clean(row["AD Number"]) or None,
            "document_kind": kind,
            "document_kind_evidence": kind_evidence,
            "status_metadata": row["Status"],
        },
        "extracted": {
            "applicability_text_candidates": applicability,
            "serial_mentions": serial_evidence,
            "ad_mentions": ad_mentions,
            "revision_mentions": relations(row),
            "proposed_text": (link if link.strip() and not is_url(link) else None),
        },
        "safety": {
            "applicability_decision": "UNKNOWN",
            "executable_applicability_rules": [],
            "effective_rule_verified": False,
            "flags": flags,
        },
        "raw": row,
    }


def annotate_groups(records):
    groups = defaultdict(list)
    for rec in records:
        # Blank dockets must not form a fake revision group.
        groups[rec["normalized"]["docket"] or rec["record_id"]].append(rec)
    for group in groups.values():
        dated = [r for r in group if r["normalized"]["publication_date"]]
        latest = max((r["normalized"]["publication_date"] for r in dated),
                     default=None)
        latest_ids = [r["record_id"] for r in dated
                      if r["normalized"]["publication_date"] == latest]
        for rec in group:
            rec["dataset_history"] = {
                "same_docket_record_ids": [r["record_id"] for r in group],
                "latest_observed_publication_date": latest,
                "latest_observed_record_ids": latest_ids,
                "is_latest_observed": rec["record_id"] in latest_ids,
                "latest_legal_version_verified": False,
                "note": "Same docket is a history grouping, not proof of revision "
                        "or effective supersession. Latest means within this CSV only.",
            }
    return groups


def validate(records):
    count = 0
    def walk(value, raw):
        nonlocal count
        if isinstance(value, dict):
            if {"field", "start", "end", "quote"} <= value.keys():
                assert raw[value["field"]][value["start"]:value["end"]] == value["quote"]
                count += 1
            for child in value.values():
                walk(child, raw)
        elif isinstance(value, list):
            for child in value:
                walk(child, raw)
    for rec in records:
        walk(rec, rec["raw"])
        assert rec["safety"]["applicability_decision"] == "UNKNOWN"
        assert not rec["safety"]["executable_applicability_rules"]
    return count


def run(input_path, output_dir):
    path = Path(input_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    csv.field_size_limit(10**8)
    records = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = set(REQUIRED) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing columns: {sorted(missing)}")
        previous_line = reader.line_num
        columns = reader.fieldnames
        for i, row in enumerate(reader, 1):
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"Malformed CSV record {i}")
            records.append(record(row, i, previous_line + 1, reader.line_num,
                                  path.name, digest))
            previous_line = reader.line_num
    if not records:
        raise ValueError("CSV contains no data records")
    groups = annotate_groups(records)
    evidence_count = validate(records)
    flags = Counter(f for r in records for f in r["safety"]["flags"])
    body_records = [r for r in records if r["extracted"]["proposed_text"]]
    model_column_count = sum(bool(r["normalized"]["models_from_column"])
                             for r in records)
    candidate_count = sum(bool(r["normalized"]["model_candidates"])
                          for r in records)
    missing_with_candidates = sum(
        not r["normalized"]["models_from_column"]
        and bool(r["normalized"]["model_candidates"]) for r in records)
    stats = {
        "record_count": len(records),
        "column_count": len(columns),
        "unique_dockets": len(groups),
        "repeated_docket_groups": sum(len(g) > 1 for g in groups.values()),
        "exact_duplicate_raw_records": len(records) - len({
            json.dumps(r["raw"], sort_keys=True) for r in records}),
        "missing_by_column": {
            col: sum(not r["raw"][col].strip() for r in records)
            for col in columns},
        "make_unique_before": len({r["raw"]["Make"] for r in records
                                   if r["raw"]["Make"].strip()}),
        "make_unique_after": len({r["normalized"]["make"] for r in records
                                  if r["normalized"]["make"]}),
        "models_in_column_records": model_column_count,
        "model_candidate_records": candidate_count,
        "missing_model_records_with_candidates": missing_with_candidates,
        "records_with_any_model_signal": sum(
            bool(r["normalized"]["models_from_column"]
                 or r["normalized"]["model_candidates"]) for r in records),
        "proposed_text_in_link_column_records": len(body_records),
        "proposed_text_in_link_column_characters": sum(
            len(r["extracted"]["proposed_text"]) for r in body_records),
        "valid_source_url_records": sum(bool(r["source"]["url"]) for r in records),
        "serial_mention_records": sum(bool(r["extracted"]["serial_mentions"])
                                      for r in records),
        "revision_mention_records": sum(bool(r["extracted"]["revision_mentions"])
                                        for r in records),
        "document_kind_counts": dict(Counter(
            r["normalized"]["document_kind"] for r in records)),
        "flags": dict(flags),
        "validated_evidence_spans": evidence_count,
        "extraction_accuracy_measured": False,
        "executable_applicability_rule_count": 0,
    }
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "records.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    (out / "quality_report.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    preview_columns = [
        "record_id", "docket", "make", "models_from_column", "model_candidates",
        "document_kind", "publication_date", "source_url", "flags"]
    with (out / "cleaned_preview.csv").open("w", encoding="utf-8-sig",
                                           newline="") as f:
        writer = csv.DictWriter(f, fieldnames=preview_columns)
        writer.writeheader()
        for rec in records:
            n = rec["normalized"]
            writer.writerow({
                "record_id": rec["record_id"], "docket": n["docket"],
                "make": n["make"],
                "models_from_column": " | ".join(
                    v["value"] for v in n["models_from_column"]),
                "model_candidates": " | ".join(sorted({
                    v["value"] for v in n["model_candidates"]})),
                "document_kind": n["document_kind"],
                "publication_date": n["publication_date"],
                "source_url": rec["source"]["url"],
                "flags": " | ".join(rec["safety"]["flags"]),
            })
    report = [
        "# 数据清洗报告", "",
        f"日期：{datetime.now().date()}",
        "负责人（POC）：项目团队",
        "TL;DR：保留原始记录并提取可追溯候选字段；尚不能据此判定维修适用性。", "",
        f"- 输入：{path.name}",
        f"- SHA-256：`{digest}`",
        f"- 共 {len(records)} 条记录、{len(columns)} 列；没有删除或合并记录。",
        f"- 唯一 Docket：{len(groups)}；重复 Docket 组：{stats['repeated_docket_groups']}。",
        f"- 原 Model 列有值：{model_column_count} 条。",
        f"- 原 Model 缺失但提取到机型候选：{missing_with_candidates} 条。",
        f"- 任一机型信号覆盖：{stats['records_with_any_model_signal']}/{len(records)}"
        f"（{stats['records_with_any_model_signal']/len(records):.1%}）。",
        f"- 评论链接列中的非 URL 文本：{len(body_records)} 条，"
        f"共 {stats['proposed_text_in_link_column_characters']} 字符。",
        f"- 包含序列号相关文字：{stats['serial_mention_records']} 条。",
        f"- 修订或撤回文字：{stats['revision_mention_records']} 条。",
        f"- 已核验原文位置的证据片段：{evidence_count} 个。", "",
        "## 使用边界", "",
        "提取覆盖率不是准确率，尚未做人工标注评估。候选正则只支持部分机型命名。",
        "Model 原列与正文候选分开保存，不将旧 AD 的适用范围写入新提案。",
        "不展开 -941 等省略型号，不把某系列默认当作所有子型号。",
        "没有足够上下文的序列号条件只保留原文，不生成可执行适用规则。",
        "拟议 supersede 不表示已经取代旧 AD；Current 元数据不表示规则已生效。",
        "相同 Docket 仅用于历史分组；最新发布日期只指本 CSV 内的最新观察。",
        "引用保留 CSV 记录号、物理行、字段名、字符位置和原文。Federal Register 页码"
        "只是条目的出版页范围，不能声称每个片段对应其中的某一具体页。", "",
        "## 产物", "",
        "- records.jsonl：完整结构化记录及原文证据。",
        "- cleaned_preview.csv：可用 Excel 查看，便于人工审核。",
        "- quality_report.json：机器可读统计。",
        "- 人工审核建议：先检查缩小或扩大适用范围、撤回公告和有正文的记录。", "",
        "## 运行统计", "", "```json",
        json.dumps(stats, ensure_ascii=False, indent=2), "```", "",
    ]
    (out / "quality_report.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=True, indent=2))
    return records, stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="data/processed")
    args = parser.parse_args()
    run(args.input, args.output)
