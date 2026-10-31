# 本地证据搜索第一版

日期：2026-10-31

负责人（POC）：项目团队

TL;DR：SQLite FTS5 搜索已接入配置比较。返回结构化原文证据，不生成维修建议。原始 CSV、代码、测试和报告保存在本地 Git；数据库可重建，不提交。

## 运行

在项目根目录运行，无需安装第三方依赖：

```powershell
python search.py build
python search.py query "EMB-120 序列号 120006 有哪些相关提案" --make Embraer
python search.py query "corrosion" --make Boeing --limit 5
python search.py query "757-200" --make Boeing --line-number 95
python search.py query "2000-NM-120-AD" --include-history
python -m unittest discover -q
python benchmark_search.py
```

索引从 `data/processed/records.jsonl` 构建，写入 `data/search/search.sqlite3`。输入内容变化时，旧索引将拒绝加载并要求重新构建。索引 schema 变更时也需更新版本并重建。

## 查询行为

检索分两条路线（第二版）：

1. **编号查找（DOCKET_LOOKUP）**：问题里写了索引中存在的 docket，直接定位，返回该 docket 的全部版本，最新的在前。用户点名的记录不会被制造商或型号过滤隐藏，由配置检查决定能说什么。
2. **关键词覆盖（KEYWORD_COVERAGE）**：其余问题用词干化（porter）的 BM25 排序。一个片段必须覆盖至少 60% 的有效关键词才算命中，这是防止无关文档混入的安全阀。问题里写了 AD 编号（如 2025-09-07）时，该编号必须出现在命中的片段中。

形似 docket 但索引中不存在的编号（如 FAA-2003-99999）直接拒答，只列出以它开头的已有编号作为提示，不会改搜相似内容。没有同义词扩展，也没有语义向量召回。排名分数不是置信概率。0.6 这个门槛是在开发集上初定的，尚未用独立用例校准。

可识别已收录机型，以及“序列号 / serial number / S/N”“生产线号 / line number / L/N”后的编号。中文只支持小词表及常见搜索句式，例如“腐蚀”“裂纹”“检查”“撤回”“燃油”。其他中文表达要求澄清，不静默删除。英文查询按关键词处理，不承诺理解任意自然语言问题。

建议用 `--make` 明确制造商。机型和制造商过滤只帮助定位候选文档；正文候选可能提及旧规则，并不证明当前适用性。机型系列和子型号之间没有隐式推断，未知型号可能漏检。输入机型与显式配置冲突、多机型、编号冲突时要求澄清。

默认仅搜索同 Docket 在 CSV 中发布日期最新的记录，可能因此隐藏旧正文。用 `--include-history` 检索历史记录；规则比较仍拒绝直接使用较早记录。这不是已验证的法律版本链，也不能证明当前有效 AD。

## 返回数据

`status=EVIDENCE_FOUND` 表示找到检索候选，不代表问题已得到完整答案。`not_exhaustive=true` 提醒结果有数量限制，不能宣称已列出所有适用指令。

每条结果含标题引用、Docket、原文片段、CSV 行号与字段字符偏移、出版页范围及配置比较。关键词可能命中索引的标识元数据，不能声称每个原文片段都包含查询的全部关键词。

找不到证据时不生成答案。所有结果保留 `legal_applicability=UNVERIFIED` 和 `operational_answer=REFUSE`。配置判断仅针对支持的拟议条文语法；未支持的改装、零件和外部服务通告条件继续拒绝自动判断。

## 验证范围

`test_search.py` 覆盖真实序列号匹配与间隙、引用回溯、机型和制造商过滤、历史记录、冲突、无证据、恶意查询字符、缺失与过期索引。测试会在临时目录重建自己的索引。

`benchmark_search.py` 保存 `docs/search_benchmark.json`。测试对象是实际 CSV 的 431 条记录，不是已验证的 1000 页语料。耗时不包含前端、网络或生成；打开索引后的检索也不等于操作系统冷缓存测试。尚未建立独立的人工标注搜索召回率评估。

下一步可以加本地网页：查询框、配置表单、原文证据展开、历史提醒和拒答原因。自然语言理解和规则覆盖率仍需单独改进。
