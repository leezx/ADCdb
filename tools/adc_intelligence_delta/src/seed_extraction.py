"""ADCSeed extraction from EvidenceRecords.

Extracts therapeutic hypotheses (target × indication pairs) from evidence
records, generating stable seed identities independent of any drug name.
This allows evidence from academic papers, company posters, and later-named
assets to accumulate against the same seed.

Design rationale (see DESIGN.md #5): seed identity is keyed on
(target, indication, modality), not on any asset/drug name. This enables
the system to represent early-stage hypotheses that may not yet have a named
pharmaceutical product, and to merge evidence streams from different temporal
and organizational contexts (e.g., university research → company naming →
FDA approval) against the same underlying hypothesis.
"""

from __future__ import annotations

import re
from contracts import ADCSeed, EvidenceRecord


def normalize_seed_slug(target: str, indication: str, modality: str = "ADC") -> str:
    """Generate a stable seed_id from (target, indication, modality).

    Slug format: target|indication|modality
    - All uppercase target/indication (if gene names), lowercase modality
    - Whitespace normalized to underscores
    - Non-alphanumeric chars (except | and _) stripped
    """
    def clean(s: str) -> str:
        # Remove punctuation, collapse whitespace to underscores
        s = re.sub(r'[^\w\s\-]', '', s)
        s = re.sub(r'\s+', '_', s.strip())
        return s.upper()

    target_clean = clean(target)
    indication_clean = clean(indication)
    return f"{target_clean}|{indication_clean}|{modality}"


def extract_seeds_from_record(record: EvidenceRecord) -> list[ADCSeed]:
    """Extract zero or more seeds from a single EvidenceRecord.

    For each (target, indication) pair found in the record, yields one seed.
    If the record has mentioned_targets but no mentioned_indications,
    skips it (we need both target and indication to form a seed).
    """
    seeds = []

    targets = record.mentioned_targets or []
    indications = record.mentioned_indications or []

    # Only generate seeds if we have both targets and indications
    if not targets or not indications:
        return seeds

    # Cartesian product: every target × indication pair
    for target in targets:
        for indication in indications:
            seed_id = normalize_seed_slug(target, indication, modality="ADC")
            seed = ADCSeed(
                seed_id=seed_id,
                target=target,
                indication=indication,
                modality="ADC",
                supporting_evidence_ids=[record.evidence_id],
            )
            seeds.append(seed)

    return seeds


def dedup_seeds(seeds: list[ADCSeed]) -> dict[str, ADCSeed]:
    """Merge seeds with identical seed_id, accumulating evidence_ids.

    Returns a dict keyed by seed_id containing the merged seed for each
    unique hypothesis.
    """
    dedup_map: dict[str, ADCSeed] = {}

    for seed in seeds:
        if seed.seed_id in dedup_map:
            # Merge: append evidence IDs (deduplicate by converting to set)
            existing = dedup_map[seed.seed_id]
            all_ids = list(set(existing.supporting_evidence_ids + seed.supporting_evidence_ids))
            existing.supporting_evidence_ids = all_ids
        else:
            dedup_map[seed.seed_id] = seed

    return dedup_map
