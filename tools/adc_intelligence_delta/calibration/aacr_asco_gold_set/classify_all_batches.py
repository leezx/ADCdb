#!/usr/bin/env python3
"""
Classify all AACR/ASCO batches with checkpoint support.
Resume from any batch/record if interrupted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[3] / "src"
sys.path.insert(0, str(SRC_DIR))

import os
import requests  # noqa: E402

BATCHES_DIR = Path(__file__).parent / "batches"
OUT_DIR = Path(__file__).parent
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
API_URL = "https://api.anthropic.com/v1/messages"

if not API_KEY:
    print("Error: ANTHROPIC_API_KEY not set", file=sys.stderr)
    sys.exit(1)

TAXONOMY = """
1. PRECLINICAL_ADC_SEED -- reports NEW preclinical/early-discovery experimental evidence (in vitro binding/internalization/cytotoxicity, in vivo PDX/xenograft efficacy, PK, novel target x indication characterization) for an antibody-based construct covalently linked to a cytotoxic small-molecule payload, intended for target-mediated delivery, NOT yet FDA-approved (brand-new candidate, novel platform, or genuinely new target/indication for existing payload class).

2. CLINICAL_ADC -- any clinical trial data (phase 1-4), safety/case report, real-world data, or combination-therapy result for an ADC, INCLUDING already-approved ADCs and first-in-human dose-escalation trials. Primary content is clinical trial data rather than preclinical characterization.

3. ADC_REVIEW_OR_METHOD -- reviews, methodology-only papers, no new experimental data of its own.

4. ADC_RELATED_BUT_NOT_ASSET_SEED -- adjacent but not an ADC seed: target/biomarker profiling without specific novel ADC tested; general target biology without antibody-payload construct; non-ADC conjugates (PROTAC/degrader, radio/photo, antibody-antibiotic, aptamer/DARPin-drug, immunotoxins without small-molecule).

5. IRRELEVANT -- not meaningfully about ADCs.
"""

PROMPT_TEMPLATE = """You are classifying AACR/ASCO conference abstracts about antibody-drug conjugates (ADCs).

Taxonomy:
{taxonomy}

For each record below, output one JSON object per line with fields:
source, year, record_id, doi, title, abstract, publication_date, category, is_true_adc, confidence, one_sentence_reason

Where:
- category: one of the 5 above (exact string match)
- is_true_adc: true/false
- confidence: HIGH/MEDIUM/LOW
- one_sentence_reason: specific to this record, not a template

COPY through unchanged: source, year, record_id, doi, title, abstract, publication_date
ADD these four new fields to each output line.

Important: base classification ONLY on domain/modality content. Do not consider whether external search queries could find it.

Records:
{records_json}

Output: one JSON object per line, complete output only (no intro/outro text).
"""


def load_batch(batch_num: int) -> list[dict]:
    """Load a single batch file."""
    path = BATCHES_DIR / f"batch_{batch_num:02d}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.open(encoding="utf-8")]


def load_already_done(batch_num: int) -> set[str]:
    """Load record_ids already classified in this batch."""
    out_path = OUT_DIR / f"labeled_batch_{batch_num:02d}.jsonl"
    if not out_path.exists():
        return set()
    done = set()
    for line in out_path.open(encoding="utf-8"):
        r = json.loads(line)
        done.add(r["record_id"])
    return done


def classify_batch(batch_num: int) -> int:
    """Classify one batch, resuming from checkpoint. Returns count of newly processed."""
    records = load_batch(batch_num)
    if not records:
        print(f"batch_{batch_num:02d}: no input file", file=sys.stderr)
        return 0

    already_done = load_already_done(batch_num)
    todo = [r for r in records if r["record_id"] not in already_done]

    if not todo:
        print(f"batch_{batch_num:02d}: already complete ({len(already_done)}/{len(records)})", file=sys.stderr)
        return 0

    print(f"batch_{batch_num:02d}: resuming {len(todo)}/{len(records)} (already done: {len(already_done)})", file=sys.stderr)

    out_path = OUT_DIR / f"labeled_batch_{batch_num:02d}.jsonl"

    # Classify in sub-chunks of 50 to avoid token explosion
    processed = 0
    for chunk_start in range(0, len(todo), 50):
        chunk_end = min(chunk_start + 50, len(todo))
        chunk = todo[chunk_start:chunk_end]

        records_json = "\n".join(json.dumps(r, ensure_ascii=False) for r in chunk)
        prompt = PROMPT_TEMPLATE.format(taxonomy=TAXONOMY, records_json=records_json)

        headers = {
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": "claude-opus-5",
            "max_tokens": 8000,
            "messages": [{"role": "user", "content": prompt}],
        }

        resp = requests.post(API_URL, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        output_text = data["content"][0]["text"].strip()

        # Parse output and write incrementally
        with out_path.open("a", encoding="utf-8") as f:
            for line in output_text.split("\n"):
                line = line.strip()
                if not line or line.startswith("Note:") or line.startswith("Output:"):
                    continue
                try:
                    # Validate JSON
                    obj = json.loads(line)
                    f.write(line + "\n")
                    f.flush()
                    processed += 1
                except json.JSONDecodeError:
                    print(f"Warning: skipped invalid JSON in batch_{batch_num:02d}: {line[:80]}", file=sys.stderr)

        print(f"  batch_{batch_num:02d} chunk {chunk_start//50 + 1}: {processed} newly processed", file=sys.stderr)

    return processed


def main() -> None:
    total_newly_processed = 0
    for batch_num in range(1, 13):
        count = classify_batch(batch_num)
        total_newly_processed += count

    print(f"Total newly processed: {total_newly_processed}")
    print(f"Wrote to {OUT_DIR}/labeled_batch_*.jsonl")


if __name__ == "__main__":
    main()
