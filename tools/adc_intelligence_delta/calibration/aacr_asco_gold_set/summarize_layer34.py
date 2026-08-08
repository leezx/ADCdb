#!/usr/bin/env python3
"""Summarize exhaustive Layer 3/4 results into a JSON summary for the report."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from identifier_confidence import classify_identifier_confidence

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

    # Always recomputed from query_identifier, never trusted from a stored
    # identifier_confidence value (even if one is present in the result
    # file) -- a stored value could be stale relative to whatever version
    # of classify_identifier_confidence() produced it, and silently
    # trusting it would let a classifier bug fix here go unnoticed by
    # anyone rerunning summarize_layer34.py against an old results file.
    for r in linked:
        r["identifier_confidence"] = classify_identifier_confidence(r["query_identifier"])

    unclassified = [r for r in linked if r["identifier_confidence"] is None]

    def _tier_stats(tier_filter) -> dict:
        subset = [r for r in linked if tier_filter(r["identifier_confidence"])]
        confirmed = sum(len(r["lineage_confirmed_pmids"]) for r in subset)
        matches = sum(len(r["adc_query_term_matches"]) for r in subset)
        return {
            "seeds": len(subset),
            "confirmed_pmids": confirmed,
            "matches": matches,
            "recall_pct": round(100 * matches / confirmed, 1) if confirmed else None,
        }

    recall_by_confidence = {
        tier: _tier_stats(lambda c, tier=tier: c == tier)
        for tier in ("HIGH", "MEDIUM", "LOW")
    }

    # Primary benchmark number per PR #8 review: only HIGH/MEDIUM-confidence
    # lineage identifiers (proprietary asset codes or non-approved
    # construct names) count toward the headline recall figure. LOW
    # confidence (an already-FDA-approved ADC's generic name, e.g.
    # "trastuzumab deruxtecan") is excluded because a match there is close
    # to tautological -- the identifier itself overlaps with
    # ADC_QUERY_TERM's own vocabulary -- and would otherwise dominate a
    # number meant to measure discovery of genuinely new constructs (one
    # such seed alone contributes 19/32 raw confirmed PMIDs).
    high_medium = _tier_stats(lambda c: c in ("HIGH", "MEDIUM"))

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
        "overall_recall_pct_all_confidence_tiers": round(100 * total_matches / total_confirmed, 1) if total_confirmed else None,
        "recall_by_confidence": recall_by_confidence,
        "benchmark_recall_high_medium_only": high_medium,
        "seeds_unclassified_confidence": len(unclassified),
        "seed_level_recall_note": (
            f"{sum(1 for r in linked if r['identifier_confidence'] in ('HIGH', 'MEDIUM') and r['lineage_confirmed_pmids'])}"
            f"/{high_medium['seeds']} HIGH/MEDIUM-confidence linked seeds have a non-empty "
            "lineage_confirmed_pmids list (this is actually counted, not assumed -- the "
            "LINKED_AND_TESTED status is supposed to guarantee it, but this checks the "
            "invariant rather than hardcoding seeds/seeds). This says nothing about the "
            "population of seeds that could NOT be linked "
            f"({status_counts.get('UNLINKABLE_NO_IDENTIFIER', 0)} UNLINKABLE + "
            f"{status_counts.get('NO_CANDIDATES_FOUND', 0)} NO_CANDIDATES_FOUND are excluded from this "
            f"denominator, not counted as recall failures) or the "
            f"{len(unclassified)} LINKED_AND_TESTED seed(s) whose identifier didn't fit any "
            "confidence tier (excluded from every recall number above, not silently folded "
            "into MEDIUM)."
        ),
        "linked_seeds_detail": [
            {
                "source": r["source"],
                "record_id": r["record_id"],
                "identifier": r["query_identifier"],
                "identifier_confidence": r["identifier_confidence"],
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
