# ADC Intelligence Delta — Pipeline 总结与审核文档

**日期**：2026-08-08（PR #8 审核修订版）
**仓库**：`leezx/ADCdb`，代码路径 `tools/adc_intelligence_delta/`
**范围**：PR #1 – PR #7（全部已合并至 `main`）+ PR #8（未合并，本次修订对应的代码/文档改动）
**用途**：供审核，说明做了什么、为什么这样做、pipeline 逻辑是什么、最终结果是什么

**修订说明**：本文档初版审核后收到 7 点具体反馈——概括为"已实现的能力被写得比代码实际成熟度高一档"。本版本已按反馈修正表述并同步落地对应代码修复（PR #8，尚未合并，等待本轮审核通过）。核心结论没有变化：source acquisition 基本成立、calibration 已做，但 evidence → structured intelligence 这一段（seed 提取、事件分型）仍在建设中，不应被误读为已经完工。

---

## 1. 这个项目在解决什么问题

`ADCdb_Obsidian/`（仓库里已有的历史 ADC 数据库）是从 adcdb.idrblab.net 爬取的**冻结基线**——爬完那一刻起就不再更新，约 6100 条 ADC / 1189 抗体 / 285 抗原条目。

`adc_intelligence_delta` 要解决的问题：**在不重新爬 ADCdb、不碰 `ADCdb_Obsidian/` 里任何一张卡片的前提下，持续追踪新出现的 ADC 情报**——新的临床试验、新的监管进展、新的会议摘要里冒出来的早期候选分子、新的公司披露。

核心设计原则：
- **只读不写**：这个 pipeline 永远不会往 `ADCdb_Obsidian/ADCs/*.md` 里写任何东西。所有产出都在独立的 delta 目录里，方便回答"哪个字段是原始爬虫来的、哪个是这个 pipeline 加的、什么时候加的、从哪个数据源来的"。
- **数据源无关的统一契约**：不管数据来自 ClinicalTrials.gov、FDA、PubMed、AACR/ASCO 摘要还是公司 SEC 文件，都先归一化成同一种 `EvidenceRecord` 形状，下游逻辑（实体消歧、种子提取、事件分型）不需要知道数据来自哪个 API。这样加一个新数据源理论上只需要写一个 `to_evidence()` 函数，不用碰核心代码——PR #2 到 PR #7 反复验证了这个假设成立（`contracts.py` 从 PR #2 之后再没改过一行）。

---

## 2. 四实体数据模型

整个 pipeline 只围绕四个数据类型（定义在 `src/contracts.py`）：

| 实体 | 含义 | 谁负责生产它 |
|------|------|------------|
| **`EvidenceRecord`** | 一条来源可追溯的原始事实（一篇论文摘要、一条临床试验记录、一份监管文件） | 每个数据源适配器（`sources/*.py`）唯一允许产出的东西 |
| **`ADCAsset`** | 一个有正式名字的、在研或已上市的 ADC 候选药物/产品 | 未来的实体消歧逻辑（尚未实现资产ID生成器） |
| **`ADCSeed`** | 一个早期治疗假设：Target × Indication × Modality，**故意不用药物名做身份标识** | `seed_extraction.py`（PR #5） |
| **`ADCEvent`** | 一个有类型、有日期的变化事件（试验启动、监管批准、临床读出） | `event_extraction.py`（PR #5） |

**为什么 `ADCSeed` 不用药物名做身份**：一篇学术论文可能报告"靶向 CDCP1 的抗体偶联物在结直肠癌模型里有效"，这时候还没有任何公司给它起名字。如果种子身份绑定药物名，这类早期证据就无处安放。用 `(target, indication, modality)` 做身份，能让学术论文、公司海报、后来正式命名的资产，都汇聚到同一个种子上积累证据。

---

## 3. 各 PR 做了什么（按合并顺序）

### PR #1 — Foundation v0.1
定义 `contracts.py` 四个实体的形状；修复实体消歧（`entity_resolution.py`）里两个真实 bug：
1. 别名冲突时之前会静默选第一个匹配，改成显式返回 `EXACT_MATCH`/`AMBIGUOUS_ALIAS`/`UNRESOLVED` 三态（真实语料库里有 202 个别名对应不止一张卡片）。
2. 之前只读卡片文件头 6000 字节，导致重度交叉引用的卡片（如 Trastuzumab deruxtecan）的 Synonyms 行被截断在外——`DS-8201`/`T-DXd`/`MK-2870`/`SKB264` 这些最常用别名全部解析失败。改成读全文。

### PR #2 — PubMed 数据源适配器
验证"加新数据源只需要写 `to_evidence()`"这个假设——结果 `contracts.py` 一行没改。

跑真实 45 天窗口时发现两个精度问题：
1. `emtansine` 被 PubMed 自动术语映射悄悄扩展成 `maytansine`（连带整个载荷类都被搜进来）。修法：给每个搜索词加 `[tiab]`（限定 title/abstract 字面匹配）。
2. 粗糙正则会把"trastuzumab **and** deruxtecan"错误提取出"and deruxtecan"，加停用词表修复。

### PR #3 — Calibration（精度/召回率实测，不改生产 query）
用两个独立实验测量当前 `ADC_QUERY_TERM`（定义见下方"生产查询"一节）的表现：

**实验 A（精度）**：515 篇 45 天窗口文章全量 LLM 标注（5 类分类）：

| 类别 | 数量 | 占比 |
|---|---|---|
| ADC_RELATED_BUT_NOT_ASSET_SEED | 159 | 30.9% |
| CLINICAL_ADC | 148 | 28.7% |
| ADC_REVIEW_OR_METHOD | 138 | 26.8% |
| **PRECLINICAL_ADC_SEED** | **62** | **12.0%** |
| IRRELEVANT | 8 | 1.6% |

LLM 判定主题精确率 507/515 = 98.4%（未经人工验证，已生成 67 篇分层抽样供人工核对）。

**实验 B（召回率）**：用完全独立的检索方式（PubMed MeSH `Immunoconjugates[Mesh]` + 前瞻信号词，不是生产环境的自由文本匹配）构建 gold set。第一版有循环论证问题（排除标准里混入了"production query 抓不到"），修正后按纯本体标准重新审查，找回 6 篇被错误排除的真阳性（75→81），最终：

> **生产 query 召回 81/81（100%）**，未发现任何确认的 miss。

### PR #4 — AACR/ASCO 独立 recall gold set
PR #3 的 gold set 建立在 PubMed MeSH 索引之上，这本身就是一种选择偏差——只能测出"PubMed 认为重要"的论文有没有被抓到。PR #4 换一个完全独立的语料库来源：**Zhixins-KB 已有的 2016–2026 AACR/ASCO 会议摘要**（2456 条，Crossref 获取，**复用不重新爬**）。

**四层测量设计**（详见下方 §4 pipeline 逻辑）：
- Layer 1（分类）：2456 篇摘要 LLM 5 类分类 → 51 篇 `PRECLINICAL_ADC_SEED`（2.1%）
- Layer 2（索引覆盖率）：会议摘要自己的 DOI 有多少被 PubMed 收录 → 0/2149（仅统计有 DOI 的记录；另 307 条无 DOI，不可测，不计入分母；预期结果，会议摘要 DOI 本来就不进 PubMed 索引）
- Layer 3（后续发表发现）：51 个种子里有多少能独立查到后续正式发表 → 首版只测了前 3 个抗体样本（12/51 种子，15 篇 PMID）
- Layer 4（生产 query 召回率）：对 Layer 3 找到的 PMID 测 `ADC_QUERY_TERM` → 样本 15/15 = 100%

### PR #5 — ADCSeed/ADCEvent 提取骨架 v0.1
实现 `seed_extraction.py`（按 target×indication 假设提取种子，去重）和 `event_extraction.py`（事件分型）。**审核后修正定位：这是契约验证用的 toy extractor，不是已落地的提取能力。** `seed_extraction.py` 对一条记录的 `mentioned_targets` × `mentioned_indications` 直接做笛卡尔积——如果一篇摘要同时提到两个靶点、两个适应症，会生成全部四种组合，包括原文从未声称过的组合（这两个 mention 列表相互独立，不是带方向的 target-supported_in-indication claim）。**在 claim-level 关系提取落地之前，不应该让 Rule Engine 消费这个输出。** 实体消歧（把种子/事件关联到已知资产）和 claim-level 关系提取都明确留给后续 PR。

`event_extraction.py` 原本对 ClinicalTrials.gov 记录也用文本匹配（在 `evidence_text` 里搜 "recruiting"/"completed" 等词），但 CT.gov adapter 早就把状态放进了结构化字段 `provenance["overall_status"]`——文本匹配既绕过了已有的结构化数据，又把 `COMPLETED` 和 `TERMINATED`（试验按计划完成 vs 提前终止，语义完全不同）合并成同一个事件类型。**PR #8 修复**：CT.gov 事件分型改成直接读 `overall_status` 做确定性映射（RECRUITING/NOT_YET_RECRUITING/ACTIVE_NOT_RECRUITING/COMPLETED/TERMINATED/WITHDRAWN 各自独立映射），不再是启发式文本搜索。PubMed/AACR/ASCO/FDA 由于没有对应的结构化字段，仍是启发式规则，LLM 细粒度分型留给这些自由文本源的后续 PR。

### PR #6 — SEC 8-K Disclosure Detector（原称"Company PR / Pipeline 数据源"，PR #8 更名）
v0.1 设计里点名的四个数据源（CT.gov + FDA + AACR/ASCO/ESMO + Company PR）里最后一个落地。**用 SEC EDGAR 全文检索**（`https://efts.sec.gov/LATEST/search-index`），而不是逐家公司官网爬取——后者没有统一 API/feed 格式，逐一爬 ~400+ 家 biotech IR 页面正是"每源一个爬虫"式蔓延，与本 pipeline 的设计原则相悖。SEC EDGAR 是官方、免费、无需 key 的统一入口，覆盖所有美股上市公司的 8-K 重大披露文件。

**审核后修正定位**：原名称"Company PR/Pipeline 数据源"暗示已经在监控公司管线新闻内容，但实际上 `company_pr.py` 只做 8-K **命中/元数据**检索——`EvidenceRecord.evidence_text` 是"公司名+日期+item 编号+附件类型"的确定性拼接文本，不是新闻稿正文；`mentioned_assets`/`mentioned_targets`/`mentioned_indications` 三个字段目前全部是空数组，没有从 8-K 里解析出任何具体资产/靶点/适应症提及。更准确的定位是"**8-K 披露信号探测器**"：能告诉你"某公司某天提交了一份提到 ADC 相关词的 8-K"，不能告诉你那份 8-K 具体说了什么。真正意义上解析出新闻稿正文和管线信息的 Company PR 数据源是后续 PR 的工作（抓取 `provenance["filing_url"]` 指向的 EX-99.1 exhibit 并解析正文）。

实测 45 天窗口：**37 份 8-K 文件，覆盖 30 家不同公司**（ADC Therapeutics、AbbVie、Gilead、Amgen 等）——这个数字本身没有错，但它衡量的是"披露信号数量"，不是"抓到的管线情报条数"。

已知边界（非 bug）：只覆盖美股上市/SEC 报告公司；只抓文件元数据，不解析附件正文（正文抓取解析超出 v0.1 范围）。

**PR #8 顺带修复**：SEC EDGAR 要求带真实联系方式的 User-Agent，之前硬编码占位符 `adc-research@example.com`，生产长期使用假地址有被限流/屏蔽风险；改成读环境变量 `ADCDB_EDGAR_USER_AGENT`，未设置时退化成一个明确提示"请配置真实联系方式"的字符串，而不是静默用假地址跑下去。

### PR #7 — 穷尽版 Layer 3/4（把 PR #4 的样本测量补完）
PR #4 的 Layer 3/4 只测了 51 个种子里的 12 个（top 3 抗体样本）。PR #7 对**全部 51 个种子**做穷尽测量，详细逻辑见下方 §4。

### PR #8 — Truthfulness / Event Correctness（本次修订，未合并，不新增数据源）
针对审核反馈的收敛性修正：本文档的能力声明、`event_extraction.py` 的 CT.gov 分型 bug、`company_pr.py` 的 User-Agent、Layer 3/4 的置信度分级、Layer 2 的 headline 数字。不新增 ESMO/Patent 等数据源——评审意见明确指出当前瓶颈不是"有没有数据源"，而是"evidence 已经进来了，但 evidence → structured intelligence 这一段还不可靠"，所以这一轮只收敛既有能力的准确性，不扩张范围。

**第二轮：把这次 PR 本身的 diff 提交给 ChatGPT 复核**，发现并修复了 4 个真实问题（不是措辞问题，是会实际出错的 bug）：
1. `company_pr.py` 里"未配置 User-Agent 时用的占位字符串"本身带了个 Unicode 长破折号，实测会让 `requests.get()` 在发请求时直接抛 `UnicodeEncodeError` 崩溃——改成未配置/为空/编码不了时直接抛出明确的 `MissingUserAgentError`，而不是发送任何占位符。
2. `classify_identifier_confidence()` 的 MEDIUM 档之前是"HIGH 和 LOW 都判不出就归 MEDIUM"的兜底桶，垃圾字符串、公司名、没被那份只有一条记录的已上市药名单覆盖到的其他已上市 ADC 通用名都会被误判进主 benchmark——重新设计成"LOW 判定看 identifier 是否直接包含生产查询的触发词"（而不是手工维护名单），拆成独立的 `identifier_confidence.py` 共享模块，顺便也修了"靶点名会被误判成 HIGH"的问题（HER2 这种）。
3. CT.gov 状态映射表漏了 6 个官方枚举值（5 个 Expanded Access 状态 + `UNKNOWN`，`UNKNOWN` 本身是正式状态，不该跟"未识别值"混在一起落进 `TRIAL_OTHER`），另外修了一个 `provenance` 里 `overall_status` 显式为 `None` 时会崩溃的边界 bug。
4. `summarize_layer34.py` 改成每次都用当前分类逻辑重新计算置信度，不再信任结果文件里可能过时的存量值；一处硬编码"X/X"的说明文字改成真实计数。

用真实的 8 个已链接种子重新验证：置信度分级结果完全不变（7 HIGH + 1 LOW + 0 MEDIUM），`REPORT_AACR_ASCO.md` 里的数字不需要改——这一轮修的是代码健壮性和正确性，不是当前数据集的结论。新增 13 个测试（`pytest tests/` 从 30 个变成 43 个全过）。

**第三轮：把第二轮的修复 diff 再发回同一个 ChatGPT 对话审核**，又发现 4 个真实问题：
1. HIGH 判定仍会把常见 ADC 靶点（ROR1/DLL3/GPC3/HER3/PDL1/CEACAM5）误判成公司代号——排除表之前只覆盖了代码里已经用到过的靶点。扩充了排除表，但代码注释里明确承认这只是缓解不是根治：靠字符串形状排除永远追不完新靶点，真正的根治需要上游提取时就标注"这是代号还是靶点"，明确推迟到后续 PR。
2. MEDIUM 既漏判真实 ADC（`mirvetuximab soravtansine`/`anetumab ravtansine` 这类 maytansinoid 载荷药物之前被排除在外），又误判非 ADC 抗体（`faricimab`）——补了载荷后缀，并把 MEDIUM 的语义改成如实说明"像抗体通用名"而不是"确认是 ADC"。改的过程中还发现自己引入了一个新 bug（去空格后整串搜索导致"random abstract"意外命中"mab"跨词子串），改成按词匹配修掉了。
3. `summarize_layer34.py` 里"unclassified 排除在每个 recall 数字之外"的说明文字和实际计算不一致——当前数据集碰巧 0 条 unclassified 所以显示数字没错，但这是结构性 bug 不是巧合。加了显式的 UNCLASSIFIED 档和一个真的会报错的一致性检查（4 档之和必须等于 linked 总数），不再只靠文档承诺。
4. User-Agent 的 Latin-1 检查不拒绝换行符，加了 CR/LF 显式拒绝。

再次用真实数据验证：数字仍不变（7 HIGH + 1 LOW + 0 MEDIUM + 0 UNCLASSIFIED）。新增 12 个测试（`pytest tests/` 从 43 个变成 55 个全过，含新建的 `test_summarize_layer34.py`）。

**第四轮：直接问 ChatGPT"现在能不能合并"**，得到明确结论：有 1 个阻塞问题——第三轮重构时字段改名，意外把 `total_misses`（总 miss 数）整个删掉了，是真实的信息丢失（当前数据集恰好 0 miss 所以看不出来，但结构性是个 bug）。除此之外它明确说"没有新的阻塞性问题"，把 `build_summary()` 会修改传入对象、User-Agent 检查没拦截 CR/LF 之外的其他控制字符这两点标注为"不阻塞合并"。三点都顺手修了；**没有采纳**它建议的"给改名的字段保留旧名字做兼容别名"——检查过仓库里没有其他脚本或文档按精确字段名读取这份 JSON，加别名是给不存在的消费者做的过度设计。新增 5 个测试，`pytest tests/` 从 55 个变成 57 个全过。ChatGPT 的最终结论：修完 `total_misses` 之后"可以合并"，分类器剩余的已知局限在这个固定人工核实的数据集范围内是"可接受的已知技术债"。

---

## 4. AACR/ASCO Gold Set 的完整 Pipeline 逻辑（PR #4 + PR #7）

这是本轮工作里方法论最复杂的部分，逐步说明：

```
┌─────────────────────────────────────────────────────────────┐
│ 输入：Zhixins-KB 已有的 2456 条 AACR/ASCO 会议摘要 (2016–2026)  │
│       (Crossref 获取，本 pipeline 复用不重新爬)                 │
└───────────────────────┬─────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: LLM 5类分类 (2386条有摘要正文的记录参与分类)             │
│   PRECLINICAL_ADC_SEED / CLINICAL_ADC / ADC_REVIEW_OR_METHOD /│
│   ADC_RELATED_BUT_NOT_ASSET_SEED / IRRELEVANT                 │
│   → 51 条 PRECLINICAL_ADC_SEED (2.1%)，去重后仍是 51 条唯一种子   │
└───────────────────────┬─────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: 会议摘要自己的 DOI 有没有被 PubMed 索引？                │
│   对全部 2456 条查 PubMed esearch?term=<doi>[doi]              │
│   → 0/2149 命中 (仅统计2149条"DOI存在但PubMed查不到"的记录；      │
│     另有307条无DOI，不可测，不计入分母)                           │
│   结论：这只是"来源索引覆盖率"，跟"后续有没有发表"是两个问题        │
└───────────────────────┬─────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: 51个种子里，哪些能独立查到后续正式发表的论文？             │
│   对每个种子：                                                 │
│   1. 提取最具体的构建体标识符（优先用公司内部代号，如 OBI-992，      │
│      而不是抗体通用名——通用名在早期摘要里经常是已上市对照药，        │
│      如 "Sacituzumab govitecan...已获批...本研究评估新型          │
│      TROP2抗体OBI-992" 这种情况，OBI-992才是真正的研究对象）      │
│   2. 用该标识符独立查 PubMed（不基于DOI），限定日期在会议年份之后    │
│   3. 谱系确认(lineage confirmation)：抓取候选PMID的标题+摘要，     │
│      验证标识符真的出现在里面——排除"PubMed碰巧搜到一篇提到          │
│      相同关键词但其实不相关的论文"这种假阳性                       │
│   → 31/51 种子提取出标识符；20/51 结构性无法链接                  │
│     (摘要本身没有点名具体候选分子，是方法学/靶点生物学论文)          │
│   → 提取出标识符的31个种子里，23个暂无候选(多为2024年太新还没       │
│     后续发表)，8个成功链接到32篇谱系确认的后续发表论文               │
└───────────────────────┬─────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 4: 生产环境 ADC_QUERY_TERM 能不能抓到这32篇论文？            │
│   对每篇PMID测: (ADC_QUERY_TERM) AND <pmid>[uid]                │
│   每个标识符先按置信度分级(PR #8新增)：                            │
│     HIGH=公司专属代号(如OBI-992) / LOW=已上市药通用名             │
│   → 主benchmark数字(仅HIGH+MEDIUM,7个种子,13篇论文): 13/13=100%  │
│   → LOW置信度种子(trastuzumab deruxtecan,1个种子,19篇论文)        │
│     单独报告: 19/19=100%,不并入主数字(通用名匹配query近乎同义反复) │
│   → 两者合计: 32/32=100%,0 miss                                │
│   → 说明：这只覆盖能独立链接到后续论文的8个种子；43个种子          │
│     (23个已找标识符暂无后续发表 + 20个结构性无法链接)不在任何       │
│     分母里，不代表"这些种子被query漏掉了"，只是尚未可测            │
└─────────────────────────────────────────────────────────────┘
```

**关键方法论决策**（为什么这样设计）：

1. **Layer 2 和 Layer 3/4 必须分开报告，不能合并成一个数字**——这是本项目最早期（在这轮工作之前）就发现的架构性教训：把"这条记录自己的 DOI 有没有被索引"和"这个种子后来有没有发表、生产 query 抓不抓得到"混为一谈，会导致循环论证。前者是数据源本身的索引覆盖率问题，后者才是真正的 recall 指标。

2. **标识符提取一开始用正则表达式，后来改成人工核实**——正则会把 payload 代号（DM1/DM4）、靶点基因符号（PD-1、GD2、FGFR4）、细胞系名称（A549、HEK293、THP-1）误判成构建体代号。因为只有 51 条记录，直接逐条阅读标题和摘要人工核实标识符（保留在代码里作为透明记录），比继续堆砌正则规则更可靠。

3. **谱系确认（lineage confirmation）是必需的一步**——只是"用某个词查到了论文"不能证明这篇论文真的是该种子的后续发表，必须再验证该标识符确实出现在候选论文正文里。

4. **UNLINKABLE 和"未找到候选"要分开统计，都不算作 recall miss**——20 个种子摘要本身没有点名任何具体分子（比如"ICAM1 抗体偶联物靶向治疗子宫内膜癌"这种只报告靶点生物学发现的摘要），23 个种子虽然有标识符但暂时查不到后续发表（多数是 2024 年的种子，时间太短还没来得及产出正式论文）。这两类都不是"query 抓不到"，如果把它们算进 miss 会人为拉低一个本不该被拉低的数字。

---

## 5. 生产查询：`ADC_QUERY_TERM`

**PR #1-#8 全部没有修改这个查询**（包括本次的 PR #8）——这是明确的原则，calibration 只做测量，不调参。

```python
_TERMS = (
    "antibody-drug conjugate", "antibody drug conjugate",
    "antibody-drug conjugates", "vedotin", "deruxtecan", "govitecan",
    "mafodotin", "tesirine", "emtansine", "ozogamicin", "tirumotecan",
)
ADC_QUERY_TERM = " OR ".join(f'"{term}"[tiab]' for term in _TERMS)
```

只用多词短语和已知载荷后缀，刻意不用裸词"ADC"（在 PubMed 更大的语料库里会撞上大量无关缩写）。`[tiab]` 限定 title/abstract 字面匹配，禁用 MeSH 自动术语扩展。

---

## 6. 最终结果汇总

### 6.1 两个独立 Gold Set 的交叉验证

| | PR #3 (PubMed MeSH) | PR #4+#7+#8 (AACR/ASCO 会议摘要) |
|---|---|---|
| 数据来源 | PubMed 自身索引 | 会议摘要（与 PubMed 索引零重叠） |
| 偏差方向 | 偏向 MeSH 索引好的期刊论文 | 偏向会议发表的早期临床前工作 |
| Gold set 规模 | 81 篇（最终版） | 51 个种子 → 8 个可链接（7 HIGH置信度 + 1 LOW置信度）→ 32 篇谱系确认后续论文 |
| **生产 query 召回率（主数字）** | **81/81 = 100%** | **13/13 = 100%**（仅 HIGH/MEDIUM 置信度标识符；LOW 置信度的已上市药种子单独报告 19/19，合计 32/32） |

**两个独立构建的 benchmark，在各自能测到的子集里都没有发现确认的 `ADC_QUERY_TERM` miss。**注意这不等于"query 没有盲区"——AACR/ASCO 这边 51 个种子里有 43 个（20 个结构性无法链接 + 23 个已有标识符但暂无后续发表）从未进入过任何一次 recall 测量，这部分的真实 recall 目前无法评估，只能等这些种子随时间产出后续论文后重跑 `task57_exhaustive_layer34.py` 才能逐步补上。

### 6.2 四数据源覆盖情况（v0.1 设计目标）

**审核后修正**：原表格把 PubMed 和 AACR/ASCO 合并成一行"✅ PR #2, #4"，容易让人理解成两者都有正式的持续摄取适配器（`sources/*.py` → `EvidenceRecord`）。实际上只有 PubMed 是这样；AACR/ASCO 是 `calibration/` 目录下复用的一份**静态语料**（Crossref 获取的 2016–2026 历史摘要），用于测量 `ADC_QUERY_TERM` 的 recall，不是每月滚动摄取的生产数据源。拆开重新报告：

| 数据源 | 状态 | 性质 | 覆盖范围 | 已知边界 |
|--------|------|------|---------|---------|
| ClinicalTrials.gov | ✅ PR #1-2 | 生产 source adapter | 全球注册临床试验 | — |
| FDA | ✅ PR #1 | 生产 source adapter | 美国监管提交记录 | 无专属 ADC 分面，靠命名后缀过滤 |
| PubMed | ✅ PR #2 | 生产 source adapter | 期刊论文（滚动窗口） | — |
| AACR / ASCO | ✅ PR #4/#7（calibration 语料） / ❌（生产摄取适配器） | 一次性复用的静态会议摘要语料 | 2016–2026 历史摘要，用于 recall 测量 | 没有持续摄取适配器；如需滚动监控新会议摘要，需要新写一个 `sources/aacr.py`/`sources/asco.py`（未开始） |
| ESMO | ❌ | — | — | 尚未接入，静态语料和适配器都没有 |
| SEC 8-K Disclosure Detector（原"Company PR/Pipeline"） | ✅ PR #6 | 生产 source adapter，**元数据级** | 美股上市公司 8-K 披露信号 | 只覆盖 SEC 报告公司；只抓文件元数据不解析正文；`mentioned_assets/targets/indications` 目前全部为空 |

### 6.3 测试覆盖

57 个单元测试全部通过（`pytest tests/ -v`）：实体消歧（含 6000 字节截断回归测试）、CT.gov/FDA/PubMed/Company PR 归一化、PubMed 停用词过滤回归测试、PR #8 新增的 CT.gov 事件分型确定性映射回归测试（含"COMPLETED/TERMINATED 不再合并成同一事件类型"、Expanded Access 状态族、UNKNOWN 状态、None/空格健壮性）、`identifier_confidence.py` 置信度分级测试（含常见 ADC 靶点排除、maytansinoid 载荷、跨词误匹配回归、生产 query 词表一致性检查）、`summarize_layer34.py` 聚合逻辑测试、SEC EDGAR User-Agent 崩溃防护及网络请求防护测试。

---

## 7. 明确标记、尚未开始的后续工作

以下事项在各 PR 里都有明确"不在本次范围"的记录，不是被遗漏，而是刻意分阶段推迟。PR #8 审核后，优先级收敛成：**PR #9 claim-level seed 提取 → PR #10 8-K EX-99.1 正文抓取**，暂不做 ESMO/Patent 等新数据源——当前瓶颈已经从"有没有数据源"变成"evidence → structured intelligence 这一段还不可靠"。

1. **claim-level 的 target-indication 关系提取（原"seed 提取"，优先级最高）**——`seed_extraction.py` 目前是 mention 列表的笛卡尔积占位，见 §3 PR #5。真正的下一步是抽取 `target — supported_in → indication` 这种带方向的关系，而不是继续在两个独立 mention 列表上做组合。
2. **8-K 附件正文抓取解析**——PR #6（现 SEC 8-K Disclosure Detector）目前只存文件元数据，没有抓取解析新闻稿正文内容，`mentioned_assets/targets/indications` 全部为空。
3. **实体消歧（Entity Resolution）for ADCSeed/ADCEvent**——`asset_id`/`seed_id` 目前全部是 `None`，还没有把提取出的种子/事件关联到已知资产。
4. **LLM 细粒度事件分型**——仅剩 FDA/PubMed/AACR/ASCO 的自由文本需要；ClinicalTrials.gov 已在 PR #8 改成读结构化字段确定性映射，不再需要 LLM。
5. **ESMO 数据源**——尚未实现适配器，也没有像 AACR/ASCO 那样的静态语料可复用。
6. **AACR/ASCO 持续摄取适配器**——目前只有一次性复用的历史语料，没有滚动监控新会议摘要的 `sources/aacr.py`/`sources/asco.py`。
7. **Patent 数据源整合**——`ADCpatent/` 现有的专利监控工具还没有和这个 pipeline 打通。
8. **Rule Engine 对接**——StelligenOS 的 Rule Engine 目前还没有消费这个 pipeline 的任何输出，且在 claim-level seed 提取落地前也不应该消费。
9. **Layer 3/4 的时间衰减重跑**——PR #7 报告里指出 42/51 个种子来自 2024 年，太新还没来得及产出后续论文；43/51 个种子目前完全在 recall 测量的分母之外。建议未来定期重跑 `task57_exhaustive_layer34.py` 观察这个数字如何随时间增长。

---

## 8. 审核要点建议

如果您要重点核查，建议关注：

1. **Layer 3 标识符提取的人工核实表**（`calibration/aacr_asco_gold_set/task57_exhaustive_layer34.py` 里的 `CURATED_IDENTIFIERS`）——这是唯一一处用人工判断代替自动化规则的地方，51 条记录的判断是否准确直接决定 Layer 3/4 数字是否可信。
2. **PR #8 新增的置信度分级逻辑**（`classify_identifier_confidence()`）——HIGH/MEDIUM/LOW 三档的判定规则（公司代号 vs 已上市药通用名 vs 其他构建体名）是否合理，尤其是"trastuzumab deruxtecan"划为 LOW、不进主 benchmark 数字这个处理方式是否同意。**已知未完全解决的局限**（写在 `identifier_confidence.py` 模块 docstring 里，不是隐藏的）：HIGH 判定靠一份靶点/细胞系排除表，新靶点仍可能漏判；MEDIUM 判定只能识别"长得像抗体通用名"，不能确认真的是 ADC。对当前 51 条人工核实过的数据集来说风险有界，但如果以后要把这份 51-record 的经验直接套到别的、非人工核实的数据集上，这两点需要重新评估。
3. **PR #6（SEC 8-K Disclosure Detector）选择 SEC EDGAR 而非逐公司爬取的取舍**——是否接受"只覆盖美股上市公司"这个结构性缺口，以及"目前只有元数据、没有正文"这个能力边界，还是需要提前安排 PR #10（EX-99.1 正文抓取）。
4. **PR #5 骨架实现的范围划分**——是否同意"claim-level 关系提取"（PR #9）作为下一步优先级最高的工作，而不是先扩展新数据源。
5. **event_extraction.py 的 CT.gov 修复**（`CT_STATUS_TO_EVENT_TYPE` 映射表）——状态到事件类型的映射是否完整覆盖了您关心的 CT.gov 状态值，未识别状态目前落到 `TRIAL_OTHER`。

---

## 附：所有 PR 链接

已合并至 `main`：
- PR #1: https://github.com/leezx/ADCdb/pull/1
- PR #2: https://github.com/leezx/ADCdb/pull/2
- PR #3: https://github.com/leezx/ADCdb/pull/3
- PR #4: https://github.com/leezx/ADCdb/pull/4
- PR #5: https://github.com/leezx/ADCdb/pull/5
- PR #6: https://github.com/leezx/ADCdb/pull/6
- PR #7: https://github.com/leezx/ADCdb/pull/7

未合并，等待本次审核：
- PR #8: https://github.com/leezx/ADCdb/pull/8
