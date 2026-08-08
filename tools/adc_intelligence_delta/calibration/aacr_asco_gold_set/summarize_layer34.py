#!/usr/bin/env python3
"""Summarize exhaustive Layer 3/4 results into a JSON summary for the report."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

OUT_DIR = Path(__file__).parent
RESULTS_FILE = OUT_DIR / "layer34_exhaustive_results.jsonl"
SUMMARY_FILE = OUT_DIR / "layer34_summary.json"


def main() -> None:
    results = [json.loads(line) for line in RESULTS_FILE.open(encoding="utf-8")]
    status_counts = Counter(r["status"] for r in results)

    linked = [r for r in results if r["status"] == "LINKED_AND_TESTED"]
    total_confirmed = sum(len(r["lineage_confirmed_pmids"]) for r in linked)
    total_matches = sum(len(r["adc_query_term_matches"]) for r in linked)
    total_misses = sum(len(r["adc_query_term_misses"]) for r in linked)

    # Separate the one already-approved-drug seed (trastuzumab deruxtecan)
    # from novel/emerging construct seeds -- ADC_QUERY_TERM matching a
    # "deruxtecan" paper is close to tautological (deruxtecan is literally
    # one of the query's own terms), so it should not be allowed to
    # dominate the headline recall number for what's meant to measure
    # discovery of NOVEL seeds.
    novel_linked = [r for r in linked if r["query_identifier"] != "trastuzumab deruxtecan"]
    novel_confirmed = sum(len(r["lineage_confirmed_pmids"]) for r in novel_linked)
    novel_matches = sum(len(r["adc_query_term_matches"]) for r in novel_linked)

    summary = {
        "total_seeds": len(results),
        "status_breakdown": dict(status_counts),
        "seeds_with_identifier": sum(1 for r in results if r["query_identifier"]),
        "seeds_unlinkable": status_counts.get("UNLINKABLE_NO_IDENTIFIER", 0),
        "seeds_no_candidates": status_counts.get("NO_CANDIDATES_FOUND", 0),
        "seeds_linked_and_tested": len(linked),
        "total_confirmed_pmids": total_confirmed,
        "total_matches": total_matches,
        "total_misses": total_misses,
        "overall_recall_pct": round(100 * total_matches / total_confirmed, 1) if total_confirmed else None,
        "novel_seeds_only": {
            "seeds": len(novel_linked),
            "confirmed_pmids": novel_confirmed,
            "matches": novel_matches,
            "recall_pct": round(100 * novel_matches / novel_confirmed, 1) if novel_confirmed else None,
        },
        "linked_seeds_detail": [
            {
                "source": r["source"],
                "record_id": r["record_id"],
                "identifier": r["query_identifier"],
                "confirmed_pmids": len(r["lineage_confirmed_pmids"]),
                "matches": len(r["adc_query_term_matches"]),
                "misses": len(r["adc_query_term_misses"]),
            }
            for r in linked
        ],
    }

    with SUMMARY_FILE.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote summary to {SUMMARY_FILE}")


if __name__ == "__main__":
    main()
