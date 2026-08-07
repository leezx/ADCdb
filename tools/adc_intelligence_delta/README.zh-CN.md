# ADC Intelligence Delta

给 `ADCdb_Obsidian`（冻结的历史基线，爬自 adcdb.idrblab.net）接上持续更新能力的工具。详细设计见 [DESIGN.md](DESIGN.md)。

## 目录

```
tools/adc_intelligence_delta/
  README.zh-CN.md
  DESIGN.md
  requirements.txt
  src/
    contracts.py          # EvidenceRecord / ADCAsset / ADCSeed / ADCEvent 四个最小契约
    entity_resolution.py  # 对 ADCdb_Obsidian/ADCs/*.md 做 alias 消歧，只读
    sources/
      clinicaltrials.py   # CT.gov -> EvidenceRecord
      fda.py               # openFDA -> EvidenceRecord
      pubmed.py            # PubMed（NCBI E-utilities）-> EvidenceRecord
  tests/
  calibration/             # PR #3：precision/recall 实验数据+工具，不含生产代码
```

## PR #3：PubMed Radar Calibration v0.1（precision + recall 实测）

不改 `ADC_QUERY_TERM`，只测量。完整方法和结论见 [calibration/REPORT.md](calibration/REPORT.md)。

- **Precision**：515 篇 45 天窗口文章全量 LLM 标注（5 类：`PRECLINICAL_ADC_SEED`/`CLINICAL_ADC`/`ADC_REVIEW_OR_METHOD`/`ADC_RELATED_BUT_NOT_ASSET_SEED`/`IRRELEVANT`），主题精确率 98.4%，但真正有价值的 `PRECLINICAL_ADC_SEED` 只占 12%。8 条假阳性的真实成因和最初猜测的不一样——不是"conjugate 疫苗"这类同形异义词，而是小分子药物偶联物、光免疫偶联物、抗菌 ADC 这类相邻但不同的药物模态。已生成 67 篇分层抽样文件（`human_audit_sample.md`）供你人工核对 LLM 标注是否准确。
- **Recall**：用完全独立的检索方式（PubMed MeSH `Immunoconjugates[Mesh]` + preclinical 信号词，不是生产环境的自由文本匹配）构建了一个 75 篇 gold set，结果生产 query 召回 100%——但这个数字有结构性偏差：gold set 要求 MeSH 已经标注为 immunoconjugates，而这种论文本身就很可能同时使用标准 ADC 词汇，所以最初担心的 company-code-only/新型 payload/不用"ADC"说法这几种 recall gap，**这次实验根本没测到，不代表不存在**。报告里写清楚了，没有拿这个 100% 冒充"recall 没问题"。

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

## 有意不做的事（PR #2 范围外）

AACR/ASCO/ESMO/company/patent 数据源、fuzzy matching、任何对 `ADCdb_Obsidian/` 卡片的写入、`ADCSeed`/`ADCEvent` 的实际提取逻辑、和 `ADCpatent/` 的整合、Rule Engine 对接——理由见 DESIGN.md。

## 跑测试

```bash
cd tools/adc_intelligence_delta
pip install -r requirements.txt
python3 -m pytest tests/ -v
```

21 个测试全过：Synonyms 解析（含 6000 字节截断回归测试）、精确匹配、歧义匹配、未匹配、CT.gov/FDA/PubMed 归一化、PubMed 停用词过滤回归测试。
