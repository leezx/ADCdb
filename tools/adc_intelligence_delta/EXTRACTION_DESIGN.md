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
(claim extraction, batch-integrity checks, quote verification,
malformed-JSON tolerance, batching, dedup) against constructed fake
responses — no network call in the test suite, and `ANTHROPIC_API_KEY` is
only required at actual pipeline run time, not for `pytest tests/`.

**v0.2 review round (before merge) — three integrity fixes**, all found
by external review and verified before fixing rather than taken on faith:

1. **Response-shape bug, confirmed against Anthropic's own docs**:
   the initial implementation assumed `response["content"][0]["text"]`
   was always the answer. Claude Opus 5 has thinking on by default (no
   configuration needed — see
   https://platform.claude.com/docs/en/build-with-claude/thinking),
   so `content[0]` is a `thinking` block on every real call, not `text`.
   `_extract_text_from_content_blocks()` now finds the actual `text`-type
   block(s) explicitly instead of assuming a fixed index, and
   `_default_llm_call()` also checks `stop_reason == "end_turn"` before
   trusting the response at all. (Note: `classify_all_batches.py` has the
   same `content[0]["text"]` assumption this was copied from — it wasn't
   fixed as part of this PR, since it's a separate, out-of-scope script,
   but it likely has the same latent bug.)
2. **Silent recall loss**: if the model's output for a batch omitted,
   duplicated, or hallucinated an `evidence_id` (most commonly from
   truncation), that was previously indistinguishable from "this record
   legitimately has zero claims" — a real pipeline should never
   silently under-count seeds that way. `extract_seeds_from_records()`
   now verifies every input record's `evidence_id` appears in the output
   exactly once and raises `IncompleteBatchError` otherwise, rejecting
   the whole batch rather than partially processing it.
3. **Claim provenance**: a valid `evidence_id` alone didn't stop the
   model from hallucinating a claim, or attaching one record's real
   content to a different (also valid) `evidence_id` in the same batch.
   Each claim must now include a `supporting_quote`, verified as an
   actual (whitespace-normalized, case-insensitive) substring of that
   specific record's `evidence_text` before being accepted — a
   misattributed or invented quote simply won't be found in the record
   it's attached to.

Also hardened against prompt injection: the fixed instructions now live
in the API's `system` parameter, separate from the untrusted,
externally-scraped `evidence_text`/`title` fields, and the system prompt
explicitly tells the model to treat that content as data, never as
instructions.

**v0.2 review round 2 (before merge) — closed two remaining gaps in the
round-1 fixes**, both confirmed by directly reproducing them against the
code before fixing:

1. **`supporting_quote` verified the wrong thing**: the check only
   confirmed a quote was real text from the *correct record* — it never
   checked the quote actually *supported* the specific target/indication
   claimed. A real, verbatim, correctly-attributed quote like "No new
   safety signals were observed" could still be attached to a fabricated
   `target`/`indication` pair and pass. `_quote_supports_claim()` now
   additionally requires the quote to contain the claimed target's and
   indication's own text, not just be real and correctly attributed.
2. **A structurally malformed claim (wrong JSON type, missing field)
   inside an otherwise-complete record was silently dropped**, which is
   the same silent-recall-loss failure mode `IncompleteBatchError`
   already exists to prevent at the record level, just one level down:
   a record whose `evidence_id` coverage looked complete could still
   have lost a real claim to corruption. Structurally malformed claims
   now raise `IncompleteBatchError` for the whole batch, same as missing/
   duplicate/hallucinated evidence_ids. (A syntactically valid claim that
   merely fails quote verification is kept as a softer per-claim drop —
   that reflects the model's own judgment being wrong about one specific
   claim, not evidence the batch itself is corrupted.)

Also added: input-level duplicate `evidence_id` rejection (`ValueError`,
raised before any LLM call) — two input records sharing an ID would make
any output line for that ID structurally ambiguous even if it
technically "covers" the ID once.

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
- No retry/backoff on transient network or API errors (429/5xx) — a
  batch failure currently propagates immediately rather than retrying.
  `classify_all_batches.py` doesn't have this either; flagged as a real
  gap, not fixed here.
- No token-budget-aware batch splitting — `batch_size` limits record
  *count* per call, not input token size, so a batch of unusually long
  `evidence_text` records could still risk truncation despite the fixed
  count limit.
- `pipeline.py`'s `process_records()` is atomic: if seed extraction
  raises (missing API key, network error, `IncompleteBatchError`), no
  events are returned either, even though event extraction is fully
  local/deterministic and would have succeeded on its own. This is a
  deliberate v0.1 scope decision (see `process_records()`'s docstring),
  not an oversight — independent partial results would need a different
  return contract.
- `supporting_quote` verification trades recall for precision, more so
  after round 2's fix: a real claim is dropped not just if the model
  paraphrases instead of quoting verbatim, but also if the target and
  indication text don't both literally appear within the same quote
  (e.g. the source spells a target differently in the sentence that
  states the indication than in the sentence that names the target).
  This mirrors the same precision-over-recall bias already documented
  for `pubmed.py`'s asset-mention extraction (see DESIGN.md's PR #2
  section) rather than introducing a new tradeoff philosophy. A
  `target_text`/`target_normalized` field split (raw quote wording kept
  separate from a canonicalized name) would recover some of this lost
  recall while keeping verification exact-substring-strict; noted as a
  reasonable follow-up, not implemented here.
- A single malformed claim anywhere in a batch's output now fails the
  entire batch (`IncompleteBatchError`), even if the other 49 records in
  a 50-record batch were extracted cleanly. This is deliberately strict
  (see "review round 2" above for why), but means one bad claim has an
  outsized blast radius under the current batch-size default; if this
  proves too disruptive in practice, a smaller `batch_size` reduces the
  amount of good data any single failure discards.

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
