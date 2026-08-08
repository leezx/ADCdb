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

CONFIDENCE_TIERS = ("HIGH", "MEDIUM", "LOW", "UNCLASSIFIED")


def _tier_stats(rows: list[dict]) -> dict:
    confirmed = sum(len(r["lineage_confirmed_pmids"]) for r in rows)
    matches = sum(len(r["adc_query_term_matches"]) for r in rows)
    return {
        "seeds": len(rows),
        "confirmed_pmids": confirmed,
        "matches": matches,
        "recall_pct": round(100 * matches / confirmed, 1) if confirmed else None,
    }


def build_summary(results: list[dict]) -> dict:
    """Pure aggregation function, no file I/O -- separated from main() so
    the aggregation logic (tier breakdown, recall math, the seed-level
    recall invariant check) can be unit tested directly on constructed
    fixtures instead of only against the live results file."""
    status_counts = Counter(r["status"] for r in results)
    linked = [r for r in results if r["status"] == "LINKED_AND_TESTED"]

    # Always recomputed from query_identifier, never trusted from a stored
    # identifier_confidence value (even if one is present in the result
    # file) -- a stored value could be stale relative to whatever version
    # of classify_identifier_confidence() produced it, and silently
    # trusting it would let a classifier bug fix here go unnoticed by
    # anyone rerunning summarize_layer34.py against an old results file.
    # "UNCLASSIFIED" (not None) is used as the bucket key here so it's a
    # regular tier like the other three -- every linked row falls into
    # exactly one of HIGH/MEDIUM/LOW/UNCLASSIFIED, with no row silently
    # missing from every bucket at once (see the reconciliation check
    # below, which would fail loudly if that ever stopped holding).
    for r in linked:
        r["identifier_confidence"] = classify_identifier_confidence(r["query_identifier"]) or "UNCLASSIFIED"

    tiers = {tier: [r for r in linked if r["identifier_confidence"] == tier] for tier in CONFIDENCE_TIERS}
    recall_by_confidence = {tier: _tier_stats(rows) for tier, rows in tiers.items()}

    reconciled_seed_count = sum(t["seeds"] for t in recall_by_confidence.values())
    if reconciled_seed_count != len(linked):
        raise AssertionError(
            f"Confidence tiers ({reconciled_seed_count} seeds) don't reconcile with "
            f"len(linked) ({len(linked)}) -- every LINKED_AND_TESTED row must fall into "
            "exactly one of HIGH/MEDIUM/LOW/UNCLASSIFIED."
        )

    # Primary benchmark number per PR #8 review: only HIGH/MEDIUM-confidence
    # lineage identifiers (proprietary asset codes or non-approved
    # construct names) count toward the headline recall figure. LOW
    # confidence (an already-FDA-approved ADC's generic name, e.g.
    # "trastuzumab deruxtecan") and UNCLASSIFIED identifiers are both
    # excluded -- LOW because a match there is close to tautological (the
    # identifier itself overlaps ADC_QUERY_TERM's own vocabulary, and
    # would otherwise dominate a number meant to measure discovery of
    # genuinely new constructs -- one such seed alone contributes 19/32
    # raw confirmed PMIDs), UNCLASSIFIED because an unrecognized string is
    # not evidence of anything and must not be silently folded into a
    # tier that counts toward the benchmark.
    high_medium_rows = tiers["HIGH"] + tiers["MEDIUM"]
    high_medium = _tier_stats(high_medium_rows)

    # Deliberately restricted to the same HIGH+MEDIUM+LOW+UNCLASSIFIED
    # partition as recall_by_confidence, so this number and the tier
    # breakdown always describe the same population. PR #8 round-2 review
    # found an earlier version computed this over ALL linked rows
    # (silently including UNCLASSIFIED ones) while the accompanying note
    # claimed unclassified rows were "excluded from every recall number
    # above" -- that was false; this field's totals now equal
    # sum(recall_by_confidence[t] for t in tiers) by construction, not by
    # a claim in prose.
    all_tiers_confirmed = sum(recall_by_confidence[t]["confirmed_pmids"] for t in CONFIDENCE_TIERS)
    all_tiers_matches = sum(recall_by_confidence[t]["matches"] for t in CONFIDENCE_TIERS)

    seeds_with_confirmed_paper = sum(1 for r in high_medium_rows if r["lineage_confirmed_pmids"])

    summary = {
        "total_seeds": len(results),
        "status_breakdown": dict(status_counts),
        "seeds_with_identifier": sum(1 for r in results if r["query_identifier"]),
        "seeds_unlinkable": status_counts.get("UNLINKABLE_NO_IDENTIFIER", 0),
        "seeds_no_candidates": status_counts.get("NO_CANDIDATES_FOUND", 0),
        "seeds_linked_and_tested": len(linked),
        "total_confirmed_pmids_all_tiers": all_tiers_confirmed,
        "total_matches_all_tiers": all_tiers_matches,
        "recall_pct_all_tiers": round(100 * all_tiers_matches / all_tiers_confirmed, 1) if all_tiers_confirmed else None,
        "recall_by_confidence": recall_by_confidence,
        "benchmark_recall_high_medium_only": high_medium,
        "seed_level_recall_note": (
            f"{seeds_with_confirmed_paper}/{high_medium['seeds']} HIGH/MEDIUM-confidence linked "
            "seeds have a non-empty lineage_confirmed_pmids list (this is actually counted, not "
            "assumed -- the LINKED_AND_TESTED status is supposed to guarantee it, but this checks "
            "the invariant rather than hardcoding seeds/seeds). This says nothing about the "
            "population of seeds that could NOT be linked "
            f"({status_counts.get('UNLINKABLE_NO_IDENTIFIER', 0)} UNLINKABLE + "
            f"{status_counts.get('NO_CANDIDATES_FOUND', 0)} NO_CANDIDATES_FOUND are excluded from "
            f"this denominator, not counted as recall failures) or the "
            f"{recall_by_confidence['UNCLASSIFIED']['seeds']} LINKED_AND_TESTED seed(s) whose "
            "identifier didn't fit any confidence tier (see recall_by_confidence.UNCLASSIFIED -- "
            "included in recall_pct_all_tiers above but excluded from benchmark_recall_high_medium_only)."
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
    return summary


def main() -> None:
    results = [json.loads(line) for line in RESULTS_FILE.open(encoding="utf-8")]
    summary = build_summary(results)

    with SUMMARY_FILE.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote summary to {SUMMARY_FILE}")


if __name__ == "__main__":
    main()
