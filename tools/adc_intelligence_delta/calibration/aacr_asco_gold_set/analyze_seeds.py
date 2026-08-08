#!/usr/bin/env python3
"""
Analyze PRECLINICAL_ADC_SEED records after classification to understand
what Layer 3 (independent candidate retrieval) will need to query.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

OUT_DIR = Path(__file__).parent
SEEDS_FILE = OUT_DIR / "unique_adc_seeds.jsonl"


def extract_antibody_names(title: str, abstract: str) -> set[str]:
    """Extract potential antibody names (common suffixes: -zumab, -umab, -mab)."""
    text = (title or "") + " " + (abstract or "")
    # Looser pattern for antibody names
    pattern = r"\b[a-z0-9]+(?:zumab|umab|mab|vedotin|deruxtecan|govitecan|imab|ximab)\b"
    matches = re.findall(pattern, text, re.IGNORECASE)
    return set(m.lower() for m in matches)


def extract_targets(title: str, abstract: str) -> set[str]:
    """Extract potential target mentions (common patterns: CD19, HER2, EGFR, TROP-2, etc.)."""
    text = (title or "") + " " + (abstract or "")
    # Common cancer targets as regex patterns
    target_patterns = [
        r"\bCD\d+\b",  # CD19, CD20, etc.
        r"\b[A-Z]+[0-9]+\b",  # EGFR, HER2, etc.
        r"\b(?:TROP|DLL|MSLN|nectin|integrin|IB6|ROR|DLK|B7|MICA|ULBP)\S*\b",
        r"\b[A-Z]+[0-9]+-[A-Z0-9]+\b",  # CLDN18.2, etc.
    ]
    targets = set()
    for pattern in target_patterns:
        matches = re.findall(pattern, text)
        targets.update(m for m in matches if len(m) <= 20)  # Filter out very long matches
    return targets


def main() -> None:
    if not SEEDS_FILE.exists():
        print(f"Seeds file not found: {SEEDS_FILE}")
        print("Run task54_merge_and_filter.py first to generate unique_adc_seeds.jsonl")
        return

    seeds = [json.loads(line) for line in SEEDS_FILE.open(encoding="utf-8")]
    print(f"Total PRECLINICAL_ADC_SEED records: {len(seeds)}")

    all_antibodies = Counter()
    all_targets = Counter()
    by_year = Counter()
    by_source = Counter()

    for seed in seeds:
        title = seed.get("title", "")
        abstract = seed.get("abstract", "")
        year = seed.get("year")
        source = seed.get("source")

        by_year[year] += 1
        by_source[source] += 1

        antibodies = extract_antibody_names(title, abstract)
        targets = extract_targets(title, abstract)

        all_antibodies.update(antibodies)
        all_targets.update(targets)

    print(f"\nBy year: {dict(sorted(by_year.items()))}")
    print(f"By source: {dict(by_source)}")

    print(f"\nTop 20 antibody names mentioned:")
    for name, count in all_antibodies.most_common(20):
        print(f"  {name}: {count}")

    print(f"\nTop 20 target genes/proteins mentioned:")
    for target, count in all_targets.most_common(20):
        print(f"  {target}: {count}")

    print("\nLayer 3 Strategy:")
    print("- For each PRECLINICAL_ADC_SEED, extract (antibody name, target, year)")
    print("- Query PubMed using independent search (not DOI-based)")
    print("- Measure: of later-published PMIDs retrieved, how many does ADC_QUERY_TERM catch?")


if __name__ == "__main__":
    main()
