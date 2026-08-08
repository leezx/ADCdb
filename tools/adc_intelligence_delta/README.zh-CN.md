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

## PR #6：Company PR / Pipeline 数据源（SEC EDGAR 全文检索）

v0.1 设计里点名的四个数据源（CT.gov + FDA + AACR/ASCO/ESMO + Company PR/pipeline）中最后一个落地。

**为什么用 SEC EDGAR 而不是逐家公司 IR 页面抓取**：公司新闻稿页面没有统一 API 或 feed 格式——每家公司自建网站，逐一爬取 ~400+ 家 biotech 的 IR 页面正是 source-adapter 模式想避免的"每个源一个爬虫"式蔓延（见 DESIGN.md）。SEC EDGAR 全文检索（`https://efts.sec.gov/LATEST/search-index`）反而提供一个官方、免费、无需 key 的统一入口，覆盖所有美股上市公司的 8-K 文件——对临床阶段 biotech 来说，重大事件（临床读出、监管进展、管线更新）几乎总会以 8-K 附件形式披露（通常是 EX-99.1，4 个工作日内必须提交）。

**结构性覆盖缺口**（不是 bug，是已知边界）：只覆盖美股上市/SEC 报告公司，私有 biotech 和非美股上市公司（常见于早期学术衍生公司和部分海外药企）不在覆盖范围内。

**精度权衡**：EDGAR 全文检索是对整份 8-K 文件正文做关键词匹配，不像 PubMed `[tiab]` 那样能限定 title/abstract 字段——EDGAR 没有暴露这个粒度。但 8-K 附件本身就是范围很窄的新闻稿文档（不像 PubMed 语料库里的完整期刊论文），所以关键词误报风险相对更低。

**`evidence_text` 的性质**：和 FDA adapter 一样，EDGAR 全文检索只返回文件元数据，不返回附件正文——完整抓取解析每份 8-K 附件的 HTML 超出 v0.1 范围。`evidence_text` 是元数据的确定性文本化表示，真正可引用的原文见 `provenance["filing_url"]`。

**实测**（45 天窗口）：37 份 8-K 文件，覆盖 30 家不同公司，含 ADC Therapeutics、AbbVie、Gilead、Amgen 等。

## PR #5：ADCSeed/ADCEvent 提取骨架 v0.1

从 EvidenceRecord 中提取"未必已有资产名"的早期治疗假设（target × indication，与药物名解耦）以及"有类型有日期"的事件（试验起止、监管进展、临床/临床前读出）。当前是骨架实现——实体消歧（把种子/事件关联到已知资产）和细粒度事件分型（LLM 分类）留给后续 PR。详细设计见 [EXTRACTION_DESIGN.md](EXTRACTION_DESIGN.md)。

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

## 有意不做的事（截至 PR #6 仍未做）

ESMO/patent 数据源、fuzzy matching、任何对 `ADCdb_Obsidian/` 卡片的写入、`ADCSeed`/`ADCEvent` 的实体消歧与 LLM 细粒度事件分型、和 `ADCpatent/` 的整合、Rule Engine 对接——理由见 DESIGN.md / EXTRACTION_DESIGN.md。

## 跑测试

```bash
cd tools/adc_intelligence_delta
pip install -r requirements.txt
python3 -m pytest tests/ -v
```

25 个测试全过：Synonyms 解析（含 6000 字节截断回归测试）、精确匹配、歧义匹配、未匹配、CT.gov/FDA/PubMed/Company PR（SEC EDGAR）归一化、PubMed 停用词过滤回归测试。
