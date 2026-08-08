# AACR/ASCO Recall Gold Set — Calibration Report

**Date**: 2026-08-08  
**Corpus**: 2456 AACR/ASCO conference abstracts (2016–2026), sourced from Zhixins-KB via Crossref  
**Purpose**: Measure `ADC_QUERY_TERM` recall against a source independent of MeSH bias (PR #3's identified flaw)

---

## Design Overview

Three-layer methodology to separate and measure:
- **Layer 1 (Classification)**: What fraction of conference abstracts are genuine ADC preclinical seeds?
- **Layer 2 (Indexing Coverage)**: How many of those seeds' conference DOIs are indexed in PubMed?
- **Layer 3 (Later-Publication Linkage)**: Of seeds with known later publications, how many does `ADC_QUERY_TERM` retrieve?

This design avoids conflating indexing coverage (layer 2) with translational recall (layer 3).

---

## Findings

### Layer 1: Classification & Seed Yield

**Input**: 2456 AACR/ASCO conference abstracts (2016–2026)

**Classification results**:
| Category | Count | % |
|----------|-------|---|
| CLINICAL_ADC | 1,974 | 82.7% |
| IRRELEVANT | 233 | 9.8% |
| ADC_REVIEW_OR_METHOD | 99 | 4.1% |
| PRECLINICAL_ADC_SEED | 51 | **2.1%** |
| ADC_RELATED_BUT_NOT_ASSET_SEED | 29 | 1.2% |

**Seed Yield**: **51 PRECLINICAL_ADC_SEED** records identified as novel preclinical evidence.  
Breakdown: AACR 46, ASCO 5  
By year: 2024 (42), 2026 (6), 2025 (2), 2017 (1)

**Quality**: 89.1% classified with HIGH confidence; 10.9% MEDIUM.

---

### Layer 2: Same-Record PubMed DOI-Exact Linkage

**Methodology**: For each of 2456 records with a DOI, query PubMed using `esearch?term=<doi>[doi]` to check if that same DOI is indexed.

**Results**:
| Status | Count | % |
|--------|-------|---|
| DOI_LINKAGE_UNAVAILABLE | 307 | 12.5% (no DOI in source — not testable) |
| NO_EXACT_DOI_PMID_MATCH | 2,149 | 87.5% (DOI exists, not in PubMed) |
| SAME_RECORD_PUBMED_MATCH | 0 | **0.0%** |

**Headline number**: **0/2,149** same-record PubMed exact-DOI matches, among the DOI-bearing conference records. (Not "0/2,456" — the 307 records with no DOI at all were excluded as untestable, not counted as misses; reporting them as part of the denominator would overstate this as a query/coverage failure when it is a source-metadata gap.)

**Conclusion**: Conference abstract DOIs (proprietary JCO/Cancer Research supplemental schemes) are essentially never indexed in PubMed as standalone records. This is expected and validates the Layer 2 → Layer 3 separation: DOI-matching measures source-specific indexing coverage, not later-publication discovery.

---

### Layer 3: Later-Publication Independent Candidate Retrieval (Exhaustive, All 51 Seeds)

**Methodology**: For each of the 51 PRECLINICAL_ADC_SEED records, extract the single most specific construct identifier (company asset code preferred over antibody generic name, which is often an approved comparator drug mentioned in the background — see `task57_exhaustive_layer34.py` for why regex-only extraction was unreliable and was replaced with a manually verified identifier per seed). Query PubMed independently (not DOI-based) for that identifier, restricted to dates after the seed's conference year. For every candidate PMID returned, fetch its title/abstract and **confirm lineage**: verify the identifier actually appears in the candidate text, distinguishing a genuine later-publication of this seed from a coincidental keyword match.

**Identifier extraction coverage**:
| Outcome | Count | % |
|---------|-------|---|
| Identifier extracted (asset code or antibody name) | 31 | 60.8% |
| **UNLINKABLE** (no specific construct named — methodology papers, general target-biology papers) | 20 | 39.2% |

UNLINKABLE seeds are not a measurement failure — they are abstracts that never name a specific candidate (e.g., "Therapeutically targeting endometrial cancer by ICAM1 antibody-drug conjugates" describes a target-biology finding, not a tested construct with a name). These seeds structurally cannot be independently linked to a later publication and are excluded from the recall denominator, not silently counted as misses.

**Candidate retrieval, of the 31 seeds with an identifier**:
| Outcome | Count |
|---------|-------|
| NO_CANDIDATES_FOUND (identifier queried, nothing later found) | 23 |
| LINKED_AND_TESTED (candidates found + lineage-confirmed) | **8** |

**8 linked seeds** (full list), with a **lineage confidence tier** per identifier — see `classify_identifier_confidence()` in `task57_exhaustive_layer34.py`. HIGH = proprietary company asset code (implausible to match incidentally); MEDIUM = a non-approved antibody/construct generic name; LOW = the generic name of an already-FDA-approved ADC, whose match against `ADC_QUERY_TERM` is close to tautological since the identifier itself overlaps the query's own vocabulary:

| Source | Record ID | Identifier | Confidence | Confirmed Later-Pub PMIDs |
|--------|-----------|------------|------------|---------------------------|
| ASCO | e14039 | MEN1309 | HIGH | 2 |
| AACR | 3130 | OBI-992 | HIGH | 3 |
| AACR | 7179 | OBI-992 | HIGH | 3 |
| AACR | 1884 | DXC006 | HIGH | 1 |
| AACR | 3142 | NN3201 | HIGH | 1 |
| AACR | 6341 | RGX-019 | HIGH | 2 |
| AACR | 738 | MBRC-101 | HIGH | 1 |
| ASCO | 10049 | trastuzumab deruxtecan | LOW | 19 |

**Total lineage-confirmed later-published PMIDs: 32** (across 8 seeds; 13 across the 7 HIGH-confidence seeds, 19 from the 1 LOW-confidence seed).

**Note on the ASCO 10049 seed**: 19 of the 32 confirmed PMIDs come from one seed whose identifier is "trastuzumab deruxtecan" — an already-FDA-approved ADC evaluated here in a new pediatric indication. See Layer 4 for why this seed's LOW confidence tier keeps it out of the primary benchmark number.

---

### Layer 4: ADC_QUERY_TERM Recall Against Later-Published Candidates (Exhaustive)

**Methodology**: For all 32 lineage-confirmed later-published PMIDs from Layer 3, apply `ADC_QUERY_TERM` (`"antibody-drug conjugate" OR "antibody drug conjugate" OR "vedotin" OR "deruxtecan" OR "govitecan" OR "mafodotin" OR "tesirine" OR "emtansine" OR "ozogamicin" OR "tirumotecan"[tiab]`) via `<query> AND <pmid>[uid]` and compute recall.

**Primary benchmark (HIGH/MEDIUM-confidence identifiers only)** — see confidence tiers above; this is the number to cite as "does `ADC_QUERY_TERM` catch later publications of newly-discovered constructs":

| Metric | Value |
|--------|-------|
| Seeds (HIGH+MEDIUM confidence) | 7 |
| Confirmed later-published PMIDs | 13 |
| ADC_QUERY_TERM matches | 13 |
| **Recall (HIGH/MEDIUM confidence)** | **100%** |

**Seed-level recall**: 7/7 HIGH/MEDIUM-confidence linked seeds had ≥1 lineage-confirmed later-published paper — true by construction of the `LINKED_AND_TESTED` status, not an independent measurement. It is reported to make explicit what this benchmark does and does not cover: it says nothing about the 23 seeds with an identifier but no located later publication, or the 20 structurally UNLINKABLE seeds — both are excluded from every denominator above, not counted as recall failures.

**All confidence tiers combined** (for transparency; not the headline number — see below for why):

| Metric | Value |
|--------|-------|
| Confirmed later-published PMIDs tested | 32 |
| ADC_QUERY_TERM matches | 32 |
| ADC_QUERY_TERM misses | 0 |
| Recall (all tiers) | 100% |

**Why the LOW-confidence seed is reported separately, not blended in**: the trastuzumab deruxtecan seed alone contributes 19 of the 32 confirmed PMIDs. Its identifier is the generic name of an already-FDA-approved ADC, and "deruxtecan" is literally one of `ADC_QUERY_TERM`'s own terms — a match here is close to tautological and would otherwise dominate a benchmark meant to measure discovery of genuinely new constructs. It is not excluded because the match was wrong (it is a legitimate lineage-confirmed later-publication), only because it is weak evidence for the specific claim this benchmark is trying to support.

**Miss taxonomy**: **Empty.** Zero misses found across all 32 lineage-confirmed PMIDs (13 HIGH-confidence + 19 LOW-confidence) — every later-published paper on an AACR/ASCO-discovered ADC seed that this benchmark could independently verify was also caught by the production `ADC_QUERY_TERM`.

**What this measurement does not show**: no confirmed miss was observed in either evaluated benchmark subset (this AACR/ASCO set and PR #3's independent PubMed MeSH set — see Comparison below). That is a fact about the seeds this benchmark could link, not a general claim that `ADC_QUERY_TERM` has no blind spots. General left-edge seed recall — i.e., recall over the full universe of preclinical ADC constructs, including the 23 identified-but-not-yet-published and 20 UNLINKABLE seeds this benchmark could not test at all — remains incompletely measured, and by construction cannot be measured until (or unless) those seeds accumulate a later publication to test against.

---

## Comparison to PR #3 (MeSH-Based Benchmark)

| Aspect | PR #3 (MeSH) | This PR (AACR/ASCO) |
|--------|--------------|-------------------|
| Source | PubMed index (MeSH terms) | Conference abstracts (Crossref) |
| Bias | Favors papers in MeSH-indexed journals | Favors conference-published preclinical work |
| Seed Count | 81 (final gold set) | 51 (8 linked to a later publication: 7 HIGH-confidence + 1 LOW-confidence) |
| Indexing Coverage | N/A (source is PubMed itself) | 0/2,149 DOI-bearing records (conference DOI-based; 307 records had no DOI, untestable) |
| Later-Pub Recall | 100% (81/81) | 100% (13/13 HIGH/MEDIUM-confidence, primary benchmark; 32/32 if all confidence tiers combined) |

Both independently-constructed benchmarks — one built from PubMed MeSH indexing, one built from AACR/ASCO conference abstracts with zero PubMed-indexing overlap — converge on the same finding: **no confirmed `ADC_QUERY_TERM` miss was observed in either evaluated benchmark subset.** This is not the same claim as "the query has no blind spots" — see the Known Limitations and Conclusions sections below for what remains untested.

---

## Known Limitations

1. **No DOI indexing in PubMed**: Conference abstract DOIs do not appear in PubMed, so Layer 2 yield is 0. This is not a tool failure but a structural reality of publishing practice.
2. **Identifier extraction requires a named construct**: 20/51 seeds (39.2%) describe target biology or methodology without naming a specific candidate, and are structurally UNLINKABLE — this is a property of what conference abstracts report, not a gap in the extraction method.
3. **Recall denominator is small (32 PMIDs, 8 seeds)**: Most seeds are recent (42/51 from 2024) and have not yet accumulated a later publication (23/31 identified seeds returned NO_CANDIDATES_FOUND) — the 100% recall figure is exact for what could be measured today, but the measurable population will grow as more 2024–2026 seeds mature into published follow-up work. Re-running `task57_exhaustive_layer34.py` periodically would track this.
4. **One dominant seed**: The trastuzumab deruxtecan seed alone contributes 19/32 (59%) of confirmed PMIDs — classified LOW confidence and reported separately (see Layer 4) so it cannot silently inflate a "novel discovery" recall claim.
5. **The recall figure only covers what could be linked**: 23/31 identified seeds returned NO_CANDIDATES_FOUND and 20/51 seeds are structurally UNLINKABLE — together, 43/51 seeds (84%) are outside every recall denominator in this report. The 100% figures above describe the 8 seeds (13 PMIDs at HIGH/MEDIUM confidence) this benchmark could actually test, not general recall across all preclinical ADC seeds.

---

## Conclusions

### Four Measurements (Complete, Exhaustive)

1. **ADC seed yield in AACR/ASCO (2016–2026)**: **2.1%** (51/2456 abstracts)
2. **Conference DOI indexing in PubMed**: **0/2,149** among DOI-bearing records (307/2456 records had no DOI and were not testable)
3. **Later-publication discovery** (exhaustive, all 51 seeds): **8/51 seeds** independently linked to **32 confirmed later-published PMIDs**; 20/51 structurally unlinkable (no named construct), 23/31 identified-but-not-yet-published
4. **ADC_QUERY_TERM recall, primary benchmark** (HIGH/MEDIUM-confidence identifiers only): **100%** (13/13 confirmed PMIDs matched, across 7 seeds). All-tiers figure (including the LOW-confidence trastuzumab deruxtecan seed): 32/32.

### Summary

This report demonstrates that:
- AACR/ASCO conference abstracts yield a small but consistent set of ADC preclinical seeds (2.1%)
- Conference DOI-based indexing is not a useful retrieval strategy (0/2,149 DOI-bearing records matched in PubMed)
- Independent, lineage-confirmed PubMed queries can identify later-published work on 8 of 51 seeds (the rest are either too recent or never name a specific construct)
- `ADC_QUERY_TERM` achieves perfect recall (zero misses) on every later-published paper this benchmark could verify traces back to an AACR/ASCO-discovered seed, across both the HIGH/MEDIUM-confidence primary benchmark (13/13) and the LOW-confidence already-approved-drug seed reported separately (19/19)

This three-layer design validates both the architectural flaw in PR #3 (conflating indexing ≠ recall) and provides a bias-orthogonal benchmark: AACR/ASCO corpus (conference bias) vs MeSH-indexed corpus (indexing bias). **No confirmed `ADC_QUERY_TERM` miss was observed in either evaluated benchmark subset.** General left-edge seed recall — across the 43/51 seeds this benchmark could not link to a later publication at all — remains incompletely measured; re-running `task57_exhaustive_layer34.py` periodically as those seeds mature is the way to close that gap over time, not a claim this report is making now.

**Reproducibility**: `task57_exhaustive_layer34.py` (extraction + retrieval + lineage confirmation + recall) and `summarize_layer34.py` (aggregation) are both included in this PR. Full per-seed results in `layer34_exhaustive_results.jsonl`; summary statistics in `layer34_summary.json`.
