# ADC Intelligence Delta

给 `ADCdb_Obsidian`（冻结的历史基线，爬自 adcdb.idrblab.net）接上持续更新能力的工具。**这次 PR 只做地基（Foundation v0.1）**：定义跨数据源统一的证据契约 + 修好实体消歧的歧义处理，不接新数据源、不改任何 ADCdb 卡片。详细设计见 [DESIGN.md](DESIGN.md)。

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
  tests/
```

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

## 有意不做的事（这次 PR 范围外）

PubMed/AACR/ASCO/ESMO/company/patent 数据源、fuzzy matching、任何对 `ADCdb_Obsidian/` 卡片的写入、`ADCSeed`/`ADCEvent` 的实际提取逻辑、和 `ADCpatent/` 的整合、Rule Engine 对接——理由见 DESIGN.md 末尾。

## 跑测试

```bash
cd tools/adc_intelligence_delta
pip install -r requirements.txt
python3 -m pytest tests/ -v
```

13 个测试全过：Synonyms 解析（含 6000 字节截断回归测试）、精确匹配、歧义匹配、未匹配、CT.gov/FDA 归一化。
