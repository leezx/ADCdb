# ADCSeed & ADCEvent Extraction (PR #5)

**Status**: v0.1 skeleton implementation  
**Date**: 2026-08-08

## Overview

This module extracts therapeutic hypotheses (ADCSeeds) and interpreted changes (ADCEvents) from source-agnostic EvidenceRecords. The design separates "raw evidence interpretation" (this PR) from "entity resolution" and "fine-grained event typing" (later PRs).

---

## ADCSeed Extraction

### Design Principle

A **seed** is a therapeutic hypothesis: (target, indication, modality), completely independent of any drug name or asset. This enables:

- Academic papers exploring a target × indication pair to accumulate evidence
- Company posters about the same hypothesis to reference the same seed
- Later-named assets to contribute evidence backward-in-time
- Un-named early-stage hypotheses to exist in the system

### Implementation (v0.1) — contract-validation only, not a landed capability

**Status: this is a toy extractor that exercises the `ADCSeed` contract's
shape. It is not yet a claim-level extraction capability and its output
should not be consumed by anything downstream (in particular, the Rule
Engine) until claim-level extraction lands.**

**Input**: EvidenceRecord with `mentioned_targets` and `mentioned_indications`

**Output**: One ADCSeed per (target, indication) Cartesian product

**Known correctness issue with the Cartesian product**: if a single
EvidenceRecord mentions two targets and two indications — e.g. an abstract
that discusses both HER2 and TROP2 as background, evaluated only in
gastric cancer — this code currently emits all four `target × indication`
combinations, including two the source text never actually claims (e.g.
`TROP2 × gastric cancer`, if the abstract's TROP2 mention was purely
comparative and never tested in that indication). `mentioned_targets` and
`mentioned_indications` are independent free-text mention lists, not
claims, so their Cartesian product is not a valid substitute for the
relation the seed is actually supposed to represent
(`target — supported_in → indication`). This makes the current output
directionally useful for validating the contract shape but not reliable
enough to feed a Rule Engine or any other automated consumer.

**Seed ID Format**: `TARGET|INDICATION|ADC` (e.g., `TROP2|COLORECTAL_CANCER|ADC`)

**Deduplication**: Same seed_id → merge, accumulate evidence_ids

**Limitations Flagged for Later**:
- No LLM entity recognition (mentioned_targets/indications must come from source adapters)
- No normalization (Target "TROP-2" vs "TROP2" treated as different)
- No seed-level confidence scoring
- No claim-level relation extraction (see Cartesian-product issue above) —
  the real fix for a later PR is extracting the actual
  `target — supported_in → indication` relation instead of pairing two
  independent mention lists

---

## ADCEvent Extraction

### Design Principle

An **event** is a dated, interpreted change:
- `TRIAL_START`, `TRIAL_ACTIVE`, `TRIAL_COMPLETED` (from ClinicalTrials.gov)
- `FDA_APPROVAL`, `FDA_DESIGNATION`, `FDA_LABEL_CHANGE` (from FDA)
- `PRECLINICAL_READOUT`, `CLINICAL_READOUT` (from PubMed/AACR/ASCO)

Events attach to an **asset** (known ADC drug), a **seed** (hypothesis), or both.

### Implementation (v0.1)

**Input**: EvidenceRecord + optional (asset_id, seed_id)

**Output**: One ADCEvent per record (zero if no asset/seed attachment)

**Event Type Inference**:
- ClinicalTrials.gov: deterministic mapping from the structured
  `provenance["overall_status"]` field to a distinct `TRIAL_*` type per
  status (RECRUITING/NOT_YET_RECRUITING/ACTIVE_NOT_RECRUITING/COMPLETED/
  TERMINATED/WITHDRAWN/...) — not a text-search heuristic, and COMPLETED
  vs TERMINATED are never merged, since a trial finishing as planned and
  one stopped early are different facts
- FDA submission text → regulatory event type (heuristic)
- PubMed/AACR/ASCO → research readout type (heuristic)
- Others → `UNTYPED` (requires LLM classification)

LLM-based fine-grained typing is reserved for the free-text sources
(PubMed/AACR/company); ClinicalTrials.gov already has a structured status
field, so no LLM step is needed there.

**Event Date**:
- Prefer `record.publication_date`
- Fallback to FDA-specific submission_status_date
- Last resort: `record.retrieved_at`

**Limitations Flagged for Later**:
- Heuristic (not LLM) event type rules for FDA/PubMed/AACR/ASCO free text — ClinicalTrials.gov is deterministic (structured status field), not heuristic
- No date extraction from free text (e.g., parsing "trial started in Q2 2024")
- No event merging (same trial start from multiple sources = multiple events)
- No entity resolution (all asset_id/seed_id are None in v0.1)

---

## Pipeline Integration

```python
from pipeline import process_records

# Collect EvidenceRecords from any source (clinicaltrials, fda, pubmed, ...)
records = [...]  # EvidenceRecord list

# Extract
seeds_dict, events_list = process_records(records)

# Output
print(f"Extracted {len(seeds_dict)} unique seeds")
print(f"Extracted {len(events_list)} events")
```

---

## Later-PR Work

### Entity Resolution (Follow-up PR)

Match extracted seeds/events to **known assets** in ADCdb_Obsidian or Stelligen-owned registry.

**Input**: A seed + EvidenceRecord (mentioned_assets, title, abstract)  
**Output**: asset_id (if matched) or None (new asset)

**Complexity**: Fuzzy matching, multi-language aliases, disambiguation.

### Fine-Grained Event Typing (Follow-up PR)

Use LLM to classify EvidenceRecord text into rich event types.

**Input**: EvidenceRecord.evidence_text + source_type + asset context  
**Output**: event_type from extended taxonomy + confidence + event_date from extracted text

**Example**: "Phase 2 trial began in Q1 2025" → `TRIAL_START` + 2025-04-01 (extracted)

### Seed Normalization (Follow-up PR)

Normalize mentions of the same target/indication across sources.

**Example**: Trop-2, TROP2, TACSTD2 → canonical `TROP2`

---

## Testing & Validation

Currently no unit tests (v0.1 is skeleton). Tests will be added when:
- Seed extraction is applied to real AACR/ASCO corpus → validate seed counts
- Event extraction runs on ClinicalTrials.gov records → validate event type accuracy

---

## Files in This PR

- `seed_extraction.py`: Hypothesis extraction from EvidenceRecords
- `event_extraction.py`: Event type inference and date extraction
- `pipeline.py`: Integration and batch processing
- `EXTRACTION_DESIGN.md`: This document
