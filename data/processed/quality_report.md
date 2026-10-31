# 数据清洗报告

日期：2026-10-31
负责人（POC）：项目团队
TL;DR：保留原始记录并提取可追溯候选字段；尚不能据此判定维修适用性。

- 输入：AD Notice of Proposed Rulemaking (AD NPRM).xlsx - AD Notice of Proposed Rulemaki.csv
- SHA-256：`81bf3b81d6b16593af5ab3deb61d0b89e7c65489f45962075f2b41ecfb4fff34`
- 共 431 条记录、22 列；没有删除或合并记录。
- 唯一 Docket：323；重复 Docket 组：99。
- 原 Model 列有值：86 条。
- 原 Model 缺失但提取到机型候选：189 条。
- 任一机型信号覆盖：275/431（63.8%）。
- 评论链接列中的非 URL 文本：192 条，共 578389 字符。
- 包含序列号相关文字：52 条。
- 修订或撤回文字：210 条。
- 已核验原文位置的证据片段：4035 个。

## 使用边界

提取覆盖率不是准确率，尚未做人工标注评估。候选正则只支持部分机型命名。
Model 原列与正文候选分开保存，不将旧 AD 的适用范围写入新提案。
不展开 -941 等省略型号，不把某系列默认当作所有子型号。
没有足够上下文的序列号条件只保留原文，不生成可执行适用规则。
拟议 supersede 不表示已经取代旧 AD；Current 元数据不表示规则已生效。
相同 Docket 仅用于历史分组；最新发布日期只指本 CSV 内的最新观察。
引用保留 CSV 记录号、物理行、字段名、字符位置和原文。Federal Register 页码只是条目的出版页范围，不能声称每个片段对应其中的某一具体页。

## 产物

- records.jsonl：完整结构化记录及原文证据。
- cleaned_preview.csv：可用 Excel 查看，便于人工审核。
- quality_report.json：机器可读统计。
- 人工审核建议：先检查缩小或扩大适用范围、撤回公告和有正文的记录。

## 运行统计

```json
{
  "record_count": 431,
  "column_count": 22,
  "unique_dockets": 323,
  "repeated_docket_groups": 99,
  "exact_duplicate_raw_records": 0,
  "missing_by_column": {
    "Docket Number": 0,
    "Subject Heading": 0,
    "Status": 0,
    "CFR Part Reference": 0,
    "Issue Date": 0,
    "Service/Office": 257,
    "Office of Primary Responsibility": 257,
    "AD Number": 410,
    "Citation Publish Date": 0,
    "Comments Due Date": 4,
    "Make": 3,
    "Model": 345,
    "Product Type": 3,
    "Product Subtype": 61,
    "AC Reference": 431,
    "AIR Policy Reference": 431,
    "Additional Reference": 431,
    "Link for Submitting Public Comment": 25,
    "Citation": 0,
    "Page Number": 0,
    "Summary": 1,
    "Comments": 426
  },
  "make_unique_before": 91,
  "make_unique_after": 88,
  "models_in_column_records": 86,
  "model_candidate_records": 244,
  "missing_model_records_with_candidates": 189,
  "records_with_any_model_signal": 275,
  "proposed_text_in_link_column_records": 192,
  "proposed_text_in_link_column_characters": 578389,
  "valid_source_url_records": 214,
  "serial_mention_records": 52,
  "revision_mention_records": 210,
  "document_kind_counts": {
    "proposal": 336,
    "supplemental_or_revised_proposal": 6,
    "withdrawal_notice": 76,
    "needs_review": 11,
    "unknown": 2
  },
  "flags": {
    "SUMMARY_OR_PROPOSED_TEXT_NOT_VERIFIED_EFFECTIVE_RULE": 431,
    "APPLICABILITY_REQUIRES_REVIEW": 431,
    "CERTAIN_SCOPE_UNRESOLVED": 326,
    "SERIAL_MENTION_NOT_PARSED_AS_EXECUTABLE_RULE": 52,
    "APPLICABILITY_CHANGED": 16,
    "CURRENT_METADATA_DOES_NOT_MEAN_ACTIVE_PROPOSAL": 76,
    "MODEL_COLUMN_MISSING": 345,
    "COMMENT_LINK_COLUMN_CONTAINS_TEXT": 192,
    "SUMMARY_MISSING": 1
  },
  "validated_evidence_spans": 4035,
  "extraction_accuracy_measured": false,
  "executable_applicability_rule_count": 0
}
```
