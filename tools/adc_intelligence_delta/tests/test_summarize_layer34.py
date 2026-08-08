import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "calibration" / "aacr_asco_gold_set"))

from summarize_layer34 import build_summary


def _linked_row(source, record_id, identifier, confirmed_pmids, matches, misses=None, stored_confidence=None):
    return {
        "source": source,
        "record_id": record_id,
        "status": "LINKED_AND_TESTED",
        "query_identifier": identifier,
        "identifier_confidence": stored_confidence,
        "lineage_confirmed_pmids": confirmed_pmids,
        "adc_query_term_matches": matches,
        "adc_query_term_misses": misses or [],
    }


def _unlinked_row(status):
    return {
        "source": "AACR",
        "record_id": "x",
        "status": status,
        "query_identifier": None,
        "lineage_confirmed_pmids": [],
        "adc_query_term_matches": [],
        "adc_query_term_misses": [],
    }


def test_one_row_per_tier_lands_in_the_right_bucket():
    results = [
        _linked_row("AACR", "1", "OBI-992", ["p1"], ["p1"]),  # HIGH
        _linked_row("AACR", "2", "faricimab", ["p2"], ["p2"]),  # MEDIUM
        _linked_row("AACR", "3", "trastuzumab deruxtecan", ["p3", "p4"], ["p3", "p4"]),  # LOW
        _linked_row("AACR", "4", "some random unclassifiable text", ["p5"], ["p5"]),  # UNCLASSIFIED
    ]
    summary = build_summary(results)

    assert summary["recall_by_confidence"]["HIGH"]["seeds"] == 1
    assert summary["recall_by_confidence"]["MEDIUM"]["seeds"] == 1
    assert summary["recall_by_confidence"]["LOW"]["seeds"] == 1
    assert summary["recall_by_confidence"]["UNCLASSIFIED"]["seeds"] == 1
    # Every linked row must be accounted for in exactly one tier.
    assert sum(t["seeds"] for t in summary["recall_by_confidence"].values()) == len(results)


def test_stale_stored_confidence_is_ignored_and_recomputed():
    # A row claiming a stored (wrong) HIGH confidence for an identifier
    # that actually classifies as LOW must be recomputed, not trusted --
    # this is the exact bug PR #8 review found in an earlier version.
    results = [_linked_row("AACR", "1", "trastuzumab deruxtecan", ["p1"], ["p1"], stored_confidence="HIGH")]
    summary = build_summary(results)
    assert summary["recall_by_confidence"]["LOW"]["seeds"] == 1
    assert summary["recall_by_confidence"]["HIGH"]["seeds"] == 0


def test_unclassified_seeds_are_excluded_from_benchmark_but_included_in_all_tiers():
    results = [
        _linked_row("AACR", "1", "OBI-992", ["p1"], ["p1"]),
        _linked_row("AACR", "2", "some random unclassifiable text", ["p2", "p3"], []),
    ]
    summary = build_summary(results)

    # Primary benchmark: only the HIGH-confidence seed counts.
    assert summary["benchmark_recall_high_medium_only"]["seeds"] == 1
    assert summary["benchmark_recall_high_medium_only"]["confirmed_pmids"] == 1

    # recall_pct_all_tiers must include the unclassified row's PMIDs too --
    # PR #8 round-2 review found an earlier version computed this over ALL
    # linked rows while the accompanying note falsely claimed unclassified
    # rows were excluded from every recall number. Now the two must agree
    # by construction: this total equals the sum across all 4 tiers.
    assert summary["total_confirmed_pmids_all_tiers"] == 3
    tier_sum = sum(t["confirmed_pmids"] for t in summary["recall_by_confidence"].values())
    assert summary["total_confirmed_pmids_all_tiers"] == tier_sum


def test_seed_level_recall_note_actually_counts_not_hardcodes():
    # A LINKED_AND_TESTED row with an empty lineage_confirmed_pmids list
    # should never occur in real data (process_seed() only sets that
    # status when confirmed is non-empty), but the note's numerator must
    # be computed, not assumed -- construct the "shouldn't happen" case
    # directly to prove the note doesn't just hardcode seeds/seeds.
    results = [_linked_row("AACR", "1", "OBI-992", [], [])]
    summary = build_summary(results)
    assert summary["seed_level_recall_note"].startswith("0/1 ")


def test_unlinkable_and_no_candidates_excluded_from_every_denominator():
    results = [
        _linked_row("AACR", "1", "OBI-992", ["p1"], ["p1"]),
        _unlinked_row("UNLINKABLE_NO_IDENTIFIER"),
        _unlinked_row("NO_CANDIDATES_FOUND"),
    ]
    summary = build_summary(results)
    assert summary["seeds_linked_and_tested"] == 1
    assert summary["total_seeds"] == 3
    assert summary["seeds_unlinkable"] == 1
    assert summary["seeds_no_candidates"] == 1


def test_empty_results_do_not_crash_on_division():
    summary = build_summary([])
    assert summary["recall_by_confidence"]["HIGH"]["recall_pct"] is None
    assert summary["benchmark_recall_high_medium_only"]["recall_pct"] is None


def test_matches_misses_and_recall_pct_all_tiers_are_correct():
    # PR #8 round-3 review: prior tests only asserted on confirmed_pmids
    # totals, not matches/misses/recall_pct -- this asserts on all of
    # them, and specifically on a case with a real miss, which is the
    # scenario total_misses exists to surface (an earlier version of
    # build_summary() silently dropped this field entirely).
    results = [
        _linked_row("AACR", "1", "OBI-992", ["p1", "p2"], ["p1"], misses=["p2"]),
        _linked_row("AACR", "2", "faricimab", ["p3"], ["p3"]),
    ]
    summary = build_summary(results)

    assert summary["total_confirmed_pmids_all_tiers"] == 3
    assert summary["total_matches_all_tiers"] == 2
    assert summary["total_misses_all_tiers"] == 1
    assert summary["recall_pct_all_tiers"] == round(100 * 2 / 3, 1)

    high = summary["recall_by_confidence"]["HIGH"]
    assert (high["confirmed_pmids"], high["matches"], high["misses"]) == (2, 1, 1)
    medium = summary["recall_by_confidence"]["MEDIUM"]
    assert (medium["confirmed_pmids"], medium["matches"], medium["misses"]) == (1, 1, 0)


def test_build_summary_does_not_mutate_caller_owned_rows():
    # PR #8 round-3 review: build_summary() wrote identifier_confidence
    # directly onto the input row dicts despite being documented as a
    # pure aggregation function -- a caller reusing the same row objects
    # across multiple calls (or a test fixture shared across assertions)
    # could see confidence values silently overwritten out from under it.
    row = _linked_row("AACR", "1", "OBI-992", ["p1"], ["p1"])
    original = dict(row)
    build_summary([row])
    assert row == original
