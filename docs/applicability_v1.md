# 适用范围解析第一版

日期：2026-10-31

负责人（POC）：项目团队

TL;DR：已实现明确 Applicability 段落的保守解析与配置比较。结果只解释提案文本，不构成有效 AD 判定或维修指令。

## 已完成

全量处理 431 条记录，发现 43 条带明确 Applicability 标题的记录。首版语法完整支持其中 15 条，7 条含数字序列号或生产线号条件。其余条款保留证据并标记不支持。没有明确标题的 388 条记录不能据此认为没有适用条件。

支持机型精确匹配、可明确展开的共享前缀型号列表、数字闭区间、单值与区间的并集、数字排除列表。生产线号与序列号分别处理。包含零件、改装、外部服务通告、复杂表格等未消费条件时，整条规则不自动执行。

只读取本地 CSV 清洗结果，不联网、不调用语言模型，也没有引入额外依赖。覆盖率不是正确率；规则尚未经过航空领域专家审核。

## 复现

从项目根目录运行：

```powershell
python applicability.py build
python -m unittest -v test_prepare_data test_applicability
python demo_applicability.py
python applicability.py check --record-id row-0383 --make Embraer --model EMB-120 --serial-number 120006
```

最后一条命令对比 FAA-2003-16645 的拟议范围。原文写明序列号 120004，以及 120006 至 120352（包含边界）。120005 不在此范围；缺少序列号时要求补充信息。

## 输出含义

- `scope_decision`：MATCH、NO_MATCH 或 UNKNOWN，仅表示与所引文本范围的比较。
- `legal_applicability`：始终 UNVERIFIED，数据不足以证明法律效力。
- `operational_answer`：始终 REFUSE，不输出实际维修操作建议。
- `checks`：制造商、机型和数字条件的逐项比较及证据。
- `evidence`：CSV 记录号、字段、字符位置、原文及来源元数据。
- `dataset_history`：仅本 CSV 内的同 Docket 历史，不代表全球最新有效规则。

旧于同 Docket 最新观察记录的条目拒绝自动比较。撤回公告及文档类型待审核记录也拒绝自动比较。型号系列关系和未登记制造商别名不靠猜测补全。同日期并列记录仍需人工核对，程序保留全部最新观察 ID。

出版页码仍是文档页范围，不声称定位到精确单页。引文能回溯原文不等于语义已通过专家验证。

## 文件

- `applicability.py`：解析、判断和命令行入口。
- `test_applicability.py`：语法、边界、真实条款和拒答测试。
- `demo_applicability.py`：六个真实数据演示。
- `data/rules/applicability_rules.jsonl`：全部记录的解析结果及证据。
- `data/rules/review_queue.csv`：人工审核表，初始均 NOT_REVIEWED。
- `data/rules/extraction_report.json`：提取统计。
- `data/rules/demo_results.json`：演示的完整结构化结果。

## 下一阶段

先审核支持的 15 条规则与复杂条款样例，建立独立人工标注集，再扩大解析覆盖率。随后加本地全文检索，让用户从问题定位到记录，再执行配置比较。当前没有完成自然语言问答、前端或 1000 页规模延迟评测。
