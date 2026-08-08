"""ADCSeed extraction from EvidenceRecords via LLM claim extraction.

Extracts therapeutic hypotheses (target x indication pairs) from evidence
records, generating stable seed identities independent of any drug name.
This allows evidence from academic papers, company posters, and later-named
assets to accumulate against the same seed.

Design rationale (see DESIGN.md #5): seed identity is keyed on
(target, indication, modality), not on any asset/drug name. This enables
the system to represent early-stage hypotheses that may not yet have a named
pharmaceutical product, and to merge evidence streams from different temporal
and organizational contexts (e.g., university research -> company naming ->
FDA approval) against the same underlying hypothesis.

PR #9 history: the original v0.1 implementation took a record's
`mentioned_targets` and `mentioned_indications` -- two independently
extracted free-text mention lists -- and emitted a seed for every
Cartesian-product pair, which is wrong whenever a record mentions more
than one target or indication (it invents pairings the source text never
claims). PR #9 review additionally found that this bug was moot in
practice: no source adapter (clinicaltrials.py, fda.py, pubmed.py,
company_pr.py) ever populated `mentioned_targets` at all, so the old
function produced zero seeds in production, always. This version replaces
that approach entirely: it reads `evidence_text` directly and asks an LLM
to extract the actual `target -- supported_in -> indication` claims the
text supports, rather than inferring a relation from two independent
mention lists after the fact.
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable

import requests

from contracts import ADCSeed, EvidenceRecord

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
# Same model and calling convention as
# calibration/aacr_asco_gold_set/classify_all_batches.py -- one LLM-calling
# pattern for this repo rather than a second, divergent one.
ANTHROPIC_MODEL = "claude-opus-5"
# Matches classify_all_batches.py's chunk size, chosen there to avoid
# response token explosion.
DEFAULT_BATCH_SIZE = 50


class MissingAPIKeyError(RuntimeError):
    pass


PROMPT_TEMPLATE = """You are extracting antibody-drug conjugate (ADC) therapeutic hypotheses from evidence records for a drug-intelligence pipeline.

For each record below, identify every EXPLICIT claim that a specific ADC construct (an antibody or antibody-like binder covalently linked to a cytotoxic payload, targeting a specific protein/antigen) is being developed, tested, or evaluated against a specific disease/indication.

A claim requires the source text to actually link a target to an indication -- e.g. "an anti-CDCP1 antibody-drug conjugate showed tumor regression in colorectal cancer PDX models" is one claim: target=CDCP1, indication=colorectal cancer.

Do NOT invent claims by pairing every target mentioned with every indication mentioned in a record. If a record discusses two targets as background but only tests one of them in a given indication, only the tested combination is a claim. If a record names a target but the text never states which indication it's being evaluated in (or vice versa), that is NOT a claim -- omit it rather than guess. Most records (routine trial status updates, safety reports, review articles, records about targets/indications not evaluated together) will have ZERO claims; an empty list is the expected, common output, not a failure.

target: the protein/antigen the ADC binds (gene symbol or receptor name, e.g. "HER2", "TROP2", "CDCP1"), never a drug/company code name.
indication: the disease/cancer type being evaluated (e.g. "colorectal cancer", "triple-negative breast cancer"), not a cell line or assay name.

For each record, output exactly one JSON object per line with fields:
evidence_id (copy through unchanged from the input), claims (a list of {{"target": ..., "indication": ...}} objects, possibly empty)

Records:
{records_json}

Output: one JSON object per line, complete output only (no intro/outro text, no markdown code fences)."""


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


def _default_llm_call(prompt: str) -> str:
    """Calls the real Anthropic API. Not unit tested directly -- callers
    that need determinism pass `llm_call` to extract_seeds_from_records()
    instead, which is what the test suite does."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise MissingAPIKeyError(
            "ANTHROPIC_API_KEY is not set. Claim-level seed extraction calls "
            "the Anthropic API to read evidence_text and extract target x "
            "indication claims -- set ANTHROPIC_API_KEY before calling "
            "extract_seeds_from_records()."
        )
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 8000,
        "messages": [{"role": "user", "content": prompt}],
    }
    resp = requests.post(ANTHROPIC_API_URL, json=payload, headers=headers, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    return data["content"][0]["text"].strip()


def _build_prompt(records: list[EvidenceRecord]) -> str:
    records_json = "\n".join(
        json.dumps(
            {
                "evidence_id": r.evidence_id,
                "title": r.title,
                "evidence_text": r.evidence_text,
            },
            ensure_ascii=False,
        )
        for r in records
    )
    return PROMPT_TEMPLATE.format(records_json=records_json)


def _parse_llm_output(output_text: str, valid_evidence_ids: set[str]) -> list[ADCSeed]:
    """Pure parsing/validation, no network calls -- this is what the test
    suite exercises directly against constructed LLM output strings,
    without needing to mock requests or hit the real API."""
    seeds: list[ADCSeed] = []

    for line in output_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("```"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            # Same tolerance as classify_all_batches.py: skip malformed
            # lines rather than fail the whole batch over one bad line.
            continue

        # obj can be any JSON value (a bare list, string, number, ...),
        # not necessarily a dict -- unlike a deterministic API response,
        # LLM output isn't schema-guaranteed. Every field below is
        # similarly untrusted: "claims" being present but null (not
        # missing) is the same dict.get()-with-None pitfall found in
        # event_extraction.py during PR #8, and a claim entry can be any
        # JSON value too. Validate shape explicitly at each step rather
        # than letting one malformed line crash the whole batch's results.
        if not isinstance(obj, dict):
            continue

        evidence_id = obj.get("evidence_id")
        if not isinstance(evidence_id, str) or evidence_id not in valid_evidence_ids:
            # Guards against the LLM hallucinating an evidence_id that
            # wasn't in this batch's input -- never attach a seed to a
            # record we didn't actually send it.
            continue

        claims = obj.get("claims")
        if not isinstance(claims, list):
            continue

        for claim in claims:
            if not isinstance(claim, dict):
                continue
            target = claim.get("target")
            indication = claim.get("indication")
            if not isinstance(target, str) or not isinstance(indication, str):
                continue
            target = target.strip()
            indication = indication.strip()
            if not target or not indication:
                continue
            seed_id = normalize_seed_slug(target, indication, modality="ADC")
            seeds.append(
                ADCSeed(
                    seed_id=seed_id,
                    target=target,
                    indication=indication,
                    modality="ADC",
                    supporting_evidence_ids=[evidence_id],
                )
            )

    return seeds


def extract_seeds_from_records(
    records: list[EvidenceRecord],
    *,
    llm_call: Callable[[str], str] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[ADCSeed]:
    """Extract ADCSeeds from a batch of EvidenceRecords via LLM claim
    extraction, reading evidence_text directly instead of cross-multiplying
    independent mention lists (see module docstring for why).

    Args:
        records: EvidenceRecords to extract seeds from.
        llm_call: injectable (prompt: str) -> response_text function.
            Defaults to the real Anthropic API call. Tests pass a fake here
            so the suite never makes a network call.
        batch_size: records per LLM call (default matches
            classify_all_batches.py's convention).

    Returns:
        One ADCSeed per extracted claim (not yet deduplicated -- pass
        through dedup_seeds() to merge same-hypothesis seeds across
        records).
    """
    llm_call = llm_call or _default_llm_call
    all_seeds: list[ADCSeed] = []

    for start in range(0, len(records), batch_size):
        chunk = records[start : start + batch_size]
        if not chunk:
            continue
        prompt = _build_prompt(chunk)
        output_text = llm_call(prompt)
        valid_ids = {r.evidence_id for r in chunk}
        all_seeds.extend(_parse_llm_output(output_text, valid_ids))

    return all_seeds


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
