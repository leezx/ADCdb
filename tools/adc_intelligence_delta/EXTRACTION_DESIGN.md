# ADCSeed & ADCEvent Extraction (PR #5, updated PR #8/#9)

**Status**: ADCEvent typing is deterministic for ClinicalTrials.gov, heuristic elsewhere (PR #8); ADCSeed extraction is LLM-based claim extraction (PR #9)  
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

### Implementation (v0.2, PR #9) — LLM claim extraction

**History**: v0.1 (PR #5) was a toy extractor that took a record's
`mentioned_targets` and `mentioned_indications` — two independently
extracted free-text mention lists — and emitted one ADCSeed per
Cartesian-product pair. This was wrong whenever a record mentioned more
than one target or indication (it invents `target × indication` pairings
the source text never claims, e.g. pairing a background-only target with
an indication only a different target was actually tested in). PR #9
review additionally found this bug was moot in practice: no source
adapter (`clinicaltrials.py`, `fda.py`, `pubmed.py`, `company_pr.py`) ever
populated `mentioned_targets` at all — it was always `[]` — so the v0.1
function produced **zero seeds in production, always**, not just
occasional false ones.

**v0.2 (current)**: `seed_extraction.py`'s `extract_seeds_from_records()`
reads `evidence_text` directly (not the mention lists) and calls the
Anthropic API (same calling convention as
`calibration/aacr_asco_gold_set/classify_all_batches.py` — raw HTTP via
`requests`, `ANTHROPIC_API_KEY` env var, batched in chunks of 50) to
extract explicit `target — supported_in → indication` claims: pairs the
source text actually links together as an ADC being evaluated in that
indication, not just co-mentioned. Most records are expected to yield
zero claims (routine trial status updates, safety reports, reviews,
records discussing a target/indication that were never tested together)
— an empty claims list is the common case, not a failure.

**Testability**: the LLM call is injectable (`llm_call` parameter), so
`tests/test_seed_extraction.py` exercises the parsing/validation logic
(claim extraction, hallucinated-evidence-id rejection, malformed-JSON
tolerance, batching, dedup) against constructed fake responses — no
network call in the test suite, and `ANTHROPIC_API_KEY` is only required
at actual pipeline run time, not for `pytest tests/`.

**Seed ID Format**: `TARGET|INDICATION|ADC` (e.g., `TROP2|COLORECTAL_CANCER|ADC`)

**Deduplication**: Same seed_id → merge, accumulate evidence_ids

**Limitations Flagged for Later**:
- No normalization (Target "TROP-2" vs "TROP2" treated as different) —
  relies entirely on the LLM's own consistency
- No seed-level confidence scoring
- Non-deterministic: re-running the same record batch can yield slightly
  different claims run-to-run (inherent to LLM extraction, unlike the
  fully deterministic CT.gov/FDA/pubmed source adapters)
- Cost/latency: every pipeline run now makes Anthropic API calls
  proportional to batch size (50 records/call) — acceptable for the
  monthly-run cadence this pipeline is designed for, not for high-frequency
  or per-record synchronous use
- No entity resolution yet (`asset_id`/matching to known `ADCAsset`s is
  still later-PR work, unchanged from v0.1)

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

`tests/test_event_extraction.py` (PR #8) and `tests/test_seed_extraction.py`
(PR #9) cover both modules — event-type mapping determinism/robustness and
seed claim-extraction parsing/validation respectively, both without any
network calls (seed extraction's LLM call is injectable; see above).
Still not yet validated against the real AACR/ASCO corpus at scale — that
remains follow-up work once this runs in a real pipeline pass.

---

## Files in This PR

- `seed_extraction.py`: LLM-based target×indication claim extraction from EvidenceRecords (PR #9)
- `event_extraction.py`: Event type inference and date extraction
- `pipeline.py`: Integration and batch processing
- `EXTRACTION_DESIGN.md`: This document
