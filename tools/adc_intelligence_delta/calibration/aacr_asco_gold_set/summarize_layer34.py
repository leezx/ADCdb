#!/usr/bin/env python3
"""Summarize exhaustive Layer 3/4 results into a JSON summary for the report."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from task57_exhaustive_layer34 import classify_identifier_confidence

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

    # Recomputed rather than trusted from the stored row: older result
    # files predate the identifier_confidence field, so fall back to
    # deriving it from query_identifier for backward compatibility.
    for r in linked:
        r.setdefault("identifier_confidence", None)
        if r["identifier_confidence"] is None:
            r["identifier_confidence"] = classify_identifier_confidence(r["query_identifier"])

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
        "seed_level_recall_note": (
            f"{high_medium['seeds']}/{high_medium['seeds']} HIGH/MEDIUM-confidence linked seeds had "
            "≥1 lineage-confirmed later-published paper, by construction of the LINKED_AND_TESTED "
            "status -- this is not evidence about the population of seeds that could NOT be linked "
            f"({status_counts.get('UNLINKABLE_NO_IDENTIFIER', 0)} UNLINKABLE + "
            f"{status_counts.get('NO_CANDIDATES_FOUND', 0)} NO_CANDIDATES_FOUND are excluded from this "
            "denominator, not counted as recall failures)."
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
