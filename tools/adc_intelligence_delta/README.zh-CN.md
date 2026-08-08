# ADC Intelligence Delta

给 `ADCdb_Obsidian`（冻结的历史基线，爬自 adcdb.idrblab.net）接上持续更新能力的工具。详细设计见 [DESIGN.md](DESIGN.md)。

## 目录

```
tools/adc_intelligence_delta/
  README.zh-CN.md
  DESIGN.md
  requirements.txt
  src/
    contracts.py           # EvidenceRecord / ADCAsset / ADCSeed / ADCEvent 四个最小契约
    entity_resolution.py   # 对 ADCdb_Obsidian/ADCs/*.md 做 alias 消歧，只读
    seed_extraction.py     # EvidenceRecord -> ADCSeed（PR #5，按 target×indication 假设去重）
    event_extraction.py    # EvidenceRecord -> ADCEvent（PR #5，启发式事件分型）
    pipeline.py             # 串联 seed/event 提取的整合层（PR #5）
    sources/
      clinicaltrials.py    # CT.gov -> EvidenceRecord
      fda.py                 # openFDA -> EvidenceRecord
      pubmed.py              # PubMed（NCBI E-utilities）-> EvidenceRecord
      company_pr.py          # SEC EDGAR 全文检索（8-K 文件）-> EvidenceRecord（PR #6）
  tests/
  calibration/               # PR #3：precision/recall 实验数据+工具，不含生产代码
    aacr_asco_gold_set/      # PR #4：AACR/ASCO 独立 recall gold set（四层测量）
```

## PR #8：Truthfulness / Event Correctness（不加新数据源）

对 PR #1-#7 的能力声明和一处真实 bug 做的收敛性修正，不新增 ESMO/Patent 等数据源：

1. 本文档 §PR #6 更名为 "SEC 8-K Disclosure Detector"，明确它只探测披露信号、不解析正文。
2. 本文档 §PR #5 明确标注笛卡尔积产生假种子的问题，并声明 Rule Engine 不应消费其输出。
3. `event_extraction.py` 的 ClinicalTrials.gov 事件分型改成读结构化 `provenance["overall_status"]` 字段做确定性映射，不再用 `evidence_text` 文本搜索；`COMPLETED`/`TERMINATED` 不再合并成同一事件类型。
4. `sources/company_pr.py` 的 SEC User-Agent 改成环境变量 `ADCDB_EDGAR_USER_AGENT` 可配置，不再硬编码占位地址。
5. AACR/ASCO Layer 3 的谱系确认加入置信度分级（HIGH/MEDIUM/LOW），Layer 4 的主要 benchmark 数字改成只统计 HIGH/MEDIUM，已上市药物通用名（trastuzumab deruxtecan）的匹配单独报告。详见 `calibration/aacr_asco_gold_set/REPORT_AACR_ASCO.md`。
6. Layer 2 的 headline 数字从"0/2456"改成"0/2149（仅统计有 DOI 的记录，307 条无 DOI 不可测）"。

**第二轮审核（提交给 ChatGPT 复核 PR #8 本身的 diff）发现并修复的真实 bug**（不是措辞问题）：

7. **`sources/company_pr.py` 的 User-Agent 占位符会导致请求崩溃**——第 4 点改完之后，未配置环境变量时退化用的占位字符串里带了一个 Unicode 长破折号"—"，而 `http.client.putheader()` 发送 HTTP header 时要求值能编码成 Latin-1——用真实网络请求验证过，这会在 `requests.get()` 内部直接抛 `UnicodeEncodeError`，崩溃点在业务代码之外，很难排查。修复：不再发送任何占位符，改成环境变量未设置/为空/编码不了 Latin-1 时直接 `raise MissingUserAgentError`，把问题在配置阶段就暴露出来。
8. **`classify_identifier_confidence()` 的 MEDIUM 档实际上是"HIGH 和 LOW 都不是就归 MEDIUM"的兜底桶**——任意垃圾字符串、公司名、未被那个只有一条记录的 `KNOWN_APPROVED_ADC_GENERIC_NAMES` 覆盖到的其他已上市 ADC 通用名（如 sacituzumab govitecan）、甚至加了品牌后缀的"trastuzumab deruxtecan-nxki"，都会被误判成 MEDIUM 并计入主 benchmark 分子——这是会实质性污染统计口径的 bug，不是风格问题。修复：新建 `identifier_confidence.py` 共享模块，把 LOW 的判定从"手工维护的已上市药名单"改成"identifier 文本是否直接包含 `ADC_QUERY_TERM` 的触发词"（更贴近"为什么算 tautological"这件事本身，且不用每出一个新批准的 ADC 就手动加一条）；HIGH 判定复用已有的靶点/CD抗原/NCT号排除表（之前漏了，导致 HER2 这种靶点符号会被误判成 HIGH）；无法归类的一律排除出 benchmark，不再默认落进 MEDIUM。同时把这个纯函数从 `task57_exhaustive_layer34.py`（一个会打 PubMed 网络请求的脚本）搬到独立的 `identifier_confidence.py`，去掉 `summarize_layer34.py` 不必要的运行时耦合。
9. **`event_extraction.py` 的 `CT_STATUS_TO_EVENT_TYPE` 漏了 6 个官方 `OverallStatus` 枚举值**——`AVAILABLE`/`NO_LONGER_AVAILABLE`/`TEMPORARILY_NOT_AVAILABLE`/`APPROVED_FOR_MARKETING`/`WITHHELD`（Expanded Access 相关状态，现在映射到独立的 `EXPANDED_ACCESS_*` 类型，不和 `TRIAL_*` 混在一起）以及 `UNKNOWN`（这本身是一个正式定义的状态值，不是"未识别"，现在映射到 `TRIAL_STATUS_UNKNOWN`，不再落进 `TRIAL_OTHER`）。另外修了一个真实的 `None` 处理 bug：`provenance.get("overall_status", "")` 在 key 存在但值是 `None` 时会返回 `None` 而不是默认值，之前会在 `.upper()` 上崩溃；现在加了 `isinstance` 检查并对状态值做 `.strip()`，避免多余空格让合法状态误落 `TRIAL_OTHER`。
10. **`summarize_layer34.py` 不再信任已存储的 `identifier_confidence` 值**，每次都用当前版本的 `classify_identifier_confidence()` 重新计算，避免分类逻辑改了以后旧结果文件里的过时等级还在悄悄生效；`seed_level_recall_note` 之前硬编码"X/X"没有真正验证任何东西，现在改成对 `lineage_confirmed_pmids` 非空这件事做真实计数。
11. 新增 13 个测试覆盖以上修复（`test_identifier_confidence.py` 全新；`test_event_extraction.py`/`test_company_pr_source.py` 补充）。

用真实 8 个已链接种子重新验证：置信度分级结果不变（7 HIGH + 1 LOW + 0 MEDIUM），`REPORT_AACR_ASCO.md` 里报告的数字不需要改——这一轮修的是代码的健壮性/正确性，不是当前数据集的结论。

**第三轮审核（把第二轮的修复 diff 再发给同一个 ChatGPT 对话审核）又发现 4 个真实问题**：

12. **HIGH 判定仍会把常见 ADC 靶点误判成公司代号**——ROR1/DLL3/GPC3/HER3/PDL1/CEACAM5 这些字母+数字形状的靶点符号，之前没被加进排除表，会被误判 HIGH。扩充了排除表，并在代码注释里明确承认这**只是缓解，不是根治**——排除表天然是"你想到什么就排除什么"，新靶点仍可能漏网；真正的根治需要上游 identifier extraction 直接标注"这是代号/靶点/通用名"这种结构化类型，而不是靠字符串形状猜，这个更大的改动明确推迟到后续 PR。对这个数据集（51 条记录的 `CURATED_IDENTIFIERS` 全部是人工核实过的字符串）来说，这个分类器只是核实之上的第二道保险，不是唯一防线，风险有界。
13. **MEDIUM 既有假阴性也有假阳性**——`mirvetuximab soravtansine`、`anetumab ravtansine` 这类真实 ADC（用 maytansinoid/DM 载荷）之前会被判 None 直接排除（因为它们既不含生产 query 的触发词，后缀也不在 `ANTIBODY_SUFFIXES` 里）；反过来 `faricimab` 这种根本不是 ADC 的普通抗体也会被判 MEDIUM。修复：把 `ravtansine`/`soravtansine`/`mertansine` 加进后缀表；MEDIUM 的 docstring 改成如实说明它检测的是"长得像抗体通用名"而不是"确认是 ADC"，`faricimab` 的测试保留但改成明确注释成"已知局限性，不是正确答案"。顺手还修了这一改动引入的一个新 bug：把匹配范围从"整个字符串锚定末尾"改成"允许品牌后缀"时，一开始用了去空格后整串搜索，结果"random abstract"去空格拼接成"randomabstract"意外包含"mab"子串——改成按空格分词、每个词单独匹配。
14. **`summarize_layer34.py` 的"unclassified 排除在外"说法和实际计算不一致**——`overall_recall_pct_all_confidence_tiers` 的分子分母其实是对全部 linked 行求和（包括 identifier_confidence 分类不出来的行），但旁边的说明文字却写"排除在每个 recall 数字之外"，两者矛盾。当前数据集碰巧 0 条 unclassified，所以显示的数字没错，但这是代码结构性问题，不是巧合。修复：加一个显式的 `UNCLASSIFIED` 档（和 HIGH/MEDIUM/LOW 并列），保证"全部 4 档之和 = linked 总数"这个不变量，并在代码里加一个真的会报错的一致性检查（不只是文档承诺）；`total_confirmed_pmids`/`total_matches`/`overall_recall_pct_all_confidence_tiers` 相应改名成 `_all_tiers` 后缀，明确这个数字包含 UNCLASSIFIED，不是"只统计已分类的"。
15. **`_user_agent()` 的 Latin-1 编码检查不拒绝换行符**——`"contact\ncontact@x.org"` 能通过编码检查（换行符在 Latin-1 范围内），要等到 `requests` 内部才会报错，绕过了"在配置边界清晰报错"这个设计初衷（虽然不会造成真正的 header 注入，`requests` 自己会拦）。加了 CR/LF 的显式拒绝。

第三轮同时指出 `identifier_confidence.py` 里的 `QUERY_TRIGGER_TERMS`/`ANTIBODY_SUFFIXES` 是手抄自 `src/sources/pubmed.py` 的生产查询词，不是导入的，未来生产查询改了这里不会自动同步——采纳的缓解方案是加一致性测试（`test_query_trigger_terms_do_not_silently_drift_from_production_query`），而不是做更大的模块重构。

再次用真实 8 个已链接种子验证：数字仍然不变（7 HIGH + 1 LOW + 0 MEDIUM + 0 UNCLASSIFIED）。新增 12 个测试（`test_summarize_layer34.py` 全新，覆盖 `build_summary()` 的聚合逻辑；`test_identifier_confidence.py`/`test_company_pr_source.py` 继续补充），`pytest tests/` 从 43 个变成 55 个全过。

## PR #7：穷尽版 AACR/ASCO Layer 3/4

PR #4 的 Layer 3/4 只抽样测了 51 个种子里的 12 个（top 3 抗体样本）。PR #7 对全部 51 个种子做穷尽测量：31/51 提取出标识符（20/51 结构性无法链接），31 个里 8 个成功链接到 32 篇谱系确认的后续发表论文，`ADC_QUERY_TERM` 全部命中（PR #8 之后按置信度拆分，见上）。完整方法见 [calibration/aacr_asco_gold_set/REPORT_AACR_ASCO.md](calibration/aacr_asco_gold_set/REPORT_AACR_ASCO.md)。

## PR #6：SEC 8-K Disclosure Detector（原称"Company PR / Pipeline 数据源"，PR #8 更名）

**命名修正（PR #8）**：这个模块之前称为"Company PR/pipeline 数据源"，但这个名字暗示它已经在监控公司管线新闻内容——实际不是。`company_pr.py` 目前做的是 SEC EDGAR 8-K 全文检索的**命中/元数据**，`EvidenceRecord.evidence_text` 只是"公司名+日期+item 编号+附件类型"的确定性拼接文本，不是新闻稿正文；`mentioned_assets`/`mentioned_targets`/`mentioned_indications` 三个字段目前全部为空数组（没有从 8-K 里解析出任何具体资产/靶点/适应症提及）。更准确的定位是"8-K 披露信号探测器"：它能告诉你"某公司在某天提交了一份提到 ADC 相关词的 8-K"，但不能告诉你那份 8-K 具体说了什么。真正意义上的"Company PR/pipeline 数据源"（解析出实际新闻稿正文和管线信息）是后续 PR 的工作（抓取 `provenance["filing_url"]` 指向的 EX-99.1 exhibit HTML 并解析正文）。

**为什么用 SEC EDGAR 而不是逐家公司 IR 页面抓取**：公司新闻稿页面没有统一 API 或 feed 格式——每家公司自建网站，逐一爬取 ~400+ 家 biotech 的 IR 页面正是 source-adapter 模式想避免的"每个源一个爬虫"式蔓延（见 DESIGN.md）。SEC EDGAR 全文检索（`https://efts.sec.gov/LATEST/search-index`）反而提供一个官方、免费、无需 key 的统一入口，覆盖所有美股上市公司的 8-K 文件——对临床阶段 biotech 来说，重大事件（临床读出、监管进展、管线更新）几乎总会以 8-K 附件形式披露（通常是 EX-99.1，4 个工作日内必须提交）。

**结构性覆盖缺口**（不是 bug，是已知边界）：只覆盖美股上市/SEC 报告公司，私有 biotech 和非美股上市公司（常见于早期学术衍生公司和部分海外药企）不在覆盖范围内。

**精度权衡**：EDGAR 全文检索是对整份 8-K 文件正文做关键词匹配，不像 PubMed `[tiab]` 那样能限定 title/abstract 字段——EDGAR 没有暴露这个粒度。但 8-K 附件本身就是范围很窄的新闻稿文档（不像 PubMed 语料库里的完整期刊论文），所以关键词误报风险相对更低。

**`evidence_text` 的性质**：和 FDA adapter 一样，EDGAR 全文检索只返回文件元数据，不返回附件正文——完整抓取解析每份 8-K 附件的 HTML 超出 v0.1 范围。`evidence_text` 是元数据的确定性文本化表示，真正可引用的原文见 `provenance["filing_url"]`。

**User-Agent（PR #8 修复）**：SEC EDGAR 要求请求带真实联系方式的 User-Agent，之前硬编码成占位符 `adc-research@example.com`——生产环境长期使用这种假地址有被限流/屏蔽的风险。现在改成读环境变量 `ADCDB_EDGAR_USER_AGENT`，未设置时退化成一个明确写着"请设置真实联系方式"的字符串，而不是静默用假地址跑下去。

**实测**（45 天窗口）：37 份 8-K 文件，覆盖 30 家不同公司，含 ADC Therapeutics、AbbVie、Gilead、Amgen 等。

## PR #5：ADCSeed/ADCEvent 提取骨架 v0.1

从 EvidenceRecord 中提取"未必已有资产名"的早期治疗假设（target × indication，与药物名解耦）以及"有类型有日期"的事件（试验起止、监管进展、临床/临床前读出）。**当前是契约验证用的 toy extractor，不是已经落地的提取能力**——`seed_extraction.py` 目前对一条记录的 `mentioned_targets` × `mentioned_indications` 直接做笛卡尔积，如果一篇摘要同时提到两个靶点和两个适应症，会生成全部四种组合，包括原文从未声称过的组合（这两个 mention 列表是相互独立的自由文本提取，不是"该靶点在该适应症下有效"这种带方向的 claim）。实体消歧（把种子/事件关联到已知资产）、细粒度事件分型（LLM 分类）、以及真正的 claim-level 关系提取都留给后续 PR。**在 claim-level 提取落地之前，不应该让 Rule Engine 或其他自动化下游消费 `seed_extraction.py` 的输出。** ClinicalTrials.gov 的事件分型已在 PR #8 改成读结构化 `overall_status` 字段（确定性映射），不再是启发式文本匹配；PubMed/AACR/ASCO/FDA 仍是启发式规则。详细设计见 [EXTRACTION_DESIGN.md](EXTRACTION_DESIGN.md)。

## PR #4：AACR/ASCO 独立 Recall Gold Set

用 AACR/ASCO 会议摘要（而非 PubMed MeSH 词）构建一个与 PR #3 正交的 recall benchmark，避免 MeSH 索引偏差。四层测量分别独立报告（分类收率、会议 DOI 索引覆盖率、后续发表发现、ADC_QUERY_TERM 回召），不合并成一个数字。完整方法和结论见 [calibration/aacr_asco_gold_set/REPORT_AACR_ASCO.md](calibration/aacr_asco_gold_set/REPORT_AACR_ASCO.md)。

## PR #3：PubMed Radar Calibration v0.1（precision + recall 实测）

不改 `ADC_QUERY_TERM`，只测量。完整方法和结论见 [calibration/REPORT.md](calibration/REPORT.md)。

- **Precision（LLM 估计值，未经人工验证）**：515 篇 45 天窗口文章全量 LLM 标注（5 类：`PRECLINICAL_ADC_SEED`/`CLINICAL_ADC`/`ADC_REVIEW_OR_METHOD`/`ADC_RELATED_BUT_NOT_ASSET_SEED`/`IRRELEVANT`），LLM 判定主题精确率 98.4%，真正有价值的 `PRECLINICAL_ADC_SEED` 只占 12%——这两个数字都还没经过人工核实，已生成 67 篇分层抽样文件（`human_audit_sample.md`）供你核对。8 条假阳性的真实成因和最初猜测的不一样——不是"conjugate 疫苗"这类同形异义词，而是小分子药物偶联物、光免疫偶联物、抗菌 ADC 这类相邻但不同的药物模态。
- **Recall**：用完全独立的检索方式（PubMed MeSH `Immunoconjugates[Mesh]` + preclinical 信号词，不是生产环境的自由文本匹配）构建 gold set。第一版有个方法学问题：筛选时把"production query 结构上抓不到"也当成了排除理由之一，这是循环论证——正确做法是排除标准必须完全独立于 production query 的能力，只按目标领域本体（是不是抗体+细胞毒小分子载荷+靶向递送+新的临床前证据）来判定。已经把 190 个候选里被排除的 115 篇按纯本体标准重新过一遍，找到 **6 篇被错误排除的真阳性**，另有 **1 篇是本体边界案例**（不是"错误排除"，而是排除标准本身有歧义）。复核 `recall_hits.jsonl` 发现，这 6 篇其实早就是 production query 能抓到的 hit（比如 PMID 41549487 标题本身就叫"...for Antibody-Drug Conjugates"）——说明原始筛选把它们排除掉纯粹是判断失误，跟 query 抓不抓得到无关，这是 gold-set 筛选质量问题，不是 recall gap。这 6 篇补回 gold set（75→81），重跑 `check_recall.py`：**生产 query 召回 81/81（100%）**。那 1 篇边界案例（PMID 39816690，ROR1-PROTAC "degrader-antibody conjugate"）单独处理：它的 payload 是靶向蛋白降解剂而不是经典细胞毒小分子，算不算"本体"里定义的 ADC 本身就是一个尚待决定的产品/ontology 判断，`recheck_excluded.jsonl` 里它的 verdict 也相应标成 `ONTOLOGY_BOUNDARY` 而不是 `SHOULD_BE_INCLUDED`，没有算进 gold set，而是记录在 `calibration/adjacent_modality_watchlist.jsonl` 里，作为以后若扩展 ADC 定义到 degrader-antibody conjugate 时的具体证据。**在当前严格 ADC 定义下，这次 benchmark 没有找到任何确认的 production-query miss。**

## PR #2：PubMed 滚动雷达（相对 Foundation v0.1）

验证 Foundation 定下的抽象是否真的成立——加一个新数据源应该只需要写 `to_evidence()`，不该动 `contracts.py`。**结果：`contracts.py` 一行没改。** PubMed 也是第一个 `evidence_text` 真正是原文摘要（而不是像 FDA 那样程序拼出来的描述）的数据源。

跑真实 45 天窗口时发现并修复了两个真实精度问题（同样是"真实数据才能发现"，不是 fixture 测试能覆盖的）：

1. `emtansine` 这个词被 PubMed 自动术语映射悄悄扩展成 `maytansine`（连带整个 maytansinoid 载荷类都被搜进来），查 API 返回的 `querytranslation` 字段才发现。修法是给每个搜索词加 `[tiab]`（限定 title/abstract 字面匹配，禁用 MeSH 自动扩展）——45天窗口结果数从529降到515，且 `translationset` 从非空变成空（确认扩展被关掉了）。
2. `mentioned_assets` 的粗糙正则提取会把英文连接词当成药名一部分，比如"trastuzumab **and** deruxtecan"会提取出"and deruxtecan"。加了一个停用词表过滤掉。

`mentioned_assets` 提取本来就是 coarse heuristic 不是 NER——PubMed API 没有像 CT.gov intervention 或 FDA generic_name 那样的结构化药名字段，这次也没打算加 LLM/NER，宁可召回率低一点也不要提取错。

## 这次 PR 改了什么（相对上一版）

1. **搬家**：从 `ADCdb_Obsidian/tools/` 挪到仓库顶层 `tools/`——vault 是数据产物，不该包含可执行的 pipeline 代码。
2. **实体消歧修了一个真实 bug**：
   - alias 冲突从"静默选第一个"改成显式返回 `EXACT_MATCH` / `AMBIGUOUS_ALIAS` / `UNRESOLVED` 三态——真实语料库里有 202 个 alias 对应不止一张卡片，之前会静默选错。
   - 读取范围从"只读文件头 6000 字节"改成读全文——被截断的头部读取会漏掉重度交叉引用卡片（比如 Trastuzumab deruxtecan）的 Synonyms 行，导致 `DS-8201`/`T-DXd`/`MK-2870`/`SKB264` 这些最常用的别名反而全部解析失败，是这次跑真实语料库时才发现的。
3. **新增 `contracts.py`**：定义了跨数据源统一的 `EvidenceRecord`，以及 `ADCAsset`/`ADCSeed`/`ADCEvent` 三个契约占位（还没实现提取逻辑，只固定形状，避免以后加 PubMed/AACR 时再改一遍 schema）。
4. **CT.gov/FDA adapter 改成输出 `EvidenceRecord`**，不再直接产出 CT.gov/FDA 专属字段的临时字典。

## Review 后追加的两处修复

1. **`ADCAsset.asset_id` 不再等同于 ADCdb 卡片路径**——原设计把两者划了等号，但月更系统的核心价值恰恰是发现 ADCdb 里不存在的全新资产，那种资产没有合法的 `asset_id` 可分配。改成 `asset_id` 归本系统自己所有，`baseline_ref`（可为 `None`）才是指向 ADCdb 卡片的可选引用。这次不实现 ID 生成器，只固定这个语义。
2. **`EvidenceRecord.raw_text` 改名 `evidence_text`**——FDA adapter 产出的其实是程序拼接的描述字符串，不是源文本原文，叫 `raw_text` 并声称"never paraphrased"是名不副实。改名后语义改成"可能是原文，也可能是结构化字段的确定性文本化表示"，真正的结构化字段始终保留在 `provenance` 里。
3. 新增一个回归测试：构造一张 Synonyms 行在 10KB+ 之后才出现的卡片，锁定"只读文件头导致漏解析"这个真实 bug，防止以后有人为了性能又加回固定大小的读取窗口。

## 有意不做的事（截至 PR #8 仍未做）

ESMO/patent 数据源、AACR/ASCO 的持续摄取适配器（目前只有 PubMed 有生产 source adapter；AACR/ASCO 数据是 `calibration/` 下复用的静态语料，不是滚动数据源）、8-K 附件正文抓取解析、fuzzy matching、任何对 `ADCdb_Obsidian/` 卡片的写入、`ADCSeed`/`ADCEvent` 的实体消歧、claim-level 的 target-indication 关系提取（目前是笛卡尔积占位，见 PR #5 章节）、LLM 细粒度事件分型（ClinicalTrials.gov 除外，已确定性化）、和 `ADCpatent/` 的整合、Rule Engine 对接——理由见 DESIGN.md / EXTRACTION_DESIGN.md。

## 跑测试

```bash
cd tools/adc_intelligence_delta
pip install -r requirements.txt
python3 -m pytest tests/ -v
```

55 个测试全过：Synonyms 解析（含 6000 字节截断回归测试）、精确匹配、歧义匹配、未匹配、CT.gov/FDA/PubMed/Company PR（SEC EDGAR）归一化、PubMed 停用词过滤回归测试、CT.gov 事件分型确定性映射回归测试（含 COMPLETED/TERMINATED 不再合并、Expanded Access 状态族、UNKNOWN 状态、None/空格健壮性）、`identifier_confidence.py` 置信度分级测试（含常见 ADC 靶点排除、maytansinoid 载荷、跨词误匹配回归、Unicode 规范化、与生产 query 词表的一致性检查）、`summarize_layer34.py` 的 `build_summary()` 聚合逻辑测试、SEC EDGAR User-Agent 未配置/空值/非 Latin-1/CR-LF 时的崩溃防护测试及"从不发出网络请求"集成测试（PR #8）。
