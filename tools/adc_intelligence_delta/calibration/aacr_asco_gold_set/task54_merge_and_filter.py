#!/usr/bin/env python3
"""
Task #54: Merge all classified batches, filter to PRECLINICAL_ADC_SEED, dedupe to unique seeds.

Output: unique_adc_seeds.jsonl (one seed per unique (source, year, record_id, doi, title))
"""

from __future__ import annotations

import json
from pathlib import Path

OUT_DIR = Path(__file__).parent
IN_BATCHES = sorted(OUT_DIR.glob("labeled_batch_*.jsonl"))
SEEDS_OUT = OUT_DIR / "unique_adc_seeds.jsonl"
MERGE_OUT = OUT_DIR / "merged_classification.jsonl"


def main() -> None:
    all_records = []
    for batch_file in IN_BATCHES:
        if not batch_file.exists():
            continue
        batch_num = batch_file.stem.split("_")[2]
        for line in batch_file.open(encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    r = json.loads(line)
                    all_records.append(r)
                except json.JSONDecodeError:
                    print(f"Warning: skipped invalid JSON in {batch_file}: {line[:80]}")

    print(f"Total records from all batches: {len(all_records)}")

    # Count by category
    by_category = {}
    for r in all_records:
        cat = r.get("category", "UNKNOWN")
        by_category[cat] = by_category.get(cat, 0) + 1

    print("Category breakdown:")
    for cat in sorted(by_category.keys()):
        print(f"  {cat}: {by_category[cat]}")

    # Write merged
    with MERGE_OUT.open("w", encoding="utf-8") as f:
        for r in all_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"Wrote {len(all_records)} to {MERGE_OUT}")

    # Filter to PRECLINICAL_ADC_SEED
    seeds = [r for r in all_records if r.get("category") == "PRECLINICAL_ADC_SEED"]
    print(f"PRECLINICAL_ADC_SEED records: {len(seeds)}")

    # Dedupe: unique by (source, year, record_id, doi, title)
    seen = set()
    unique_seeds = []
    for r in seeds:
        key = (r.get("source"), r.get("year"), r.get("record_id"), r.get("doi"), r.get("title"))
        if key not in seen:
            seen.add(key)
            unique_seeds.append(r)

    print(f"Unique seeds after deduplication: {len(unique_seeds)}")

    # Write unique seeds
    with SEEDS_OUT.open("w", encoding="utf-8") as f:
        for r in unique_seeds:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"Wrote {len(unique_seeds)} unique seeds to {SEEDS_OUT}")


if __name__ == "__main__":
    main()
