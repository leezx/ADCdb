#!/usr/bin/env python3
"""
Compute preliminary recall on Layer 3 sample PMIDs.
For each of the 3 sample antibodies, test 3-5 PMIDs against ADC_QUERY_TERM.
"""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

import requests

LAYER3_FILE = Path(__file__).parent / "layer3_sample_queries.jsonl"
ESEARCH_ENDPOINT = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

# ADC_QUERY_TERM from src/sources/pubmed.py
QUERY_TERMS = (
    "antibody-drug conjugate",
    "antibody drug conjugate",
    "antibody-drug conjugates",
    "vedotin",
    "deruxtecan",
    "govitecan",
    "mafodotin",
    "tesirine",
    "emtansine",
    "ozogamicin",
    "tirumotecan",
)
ADC_QUERY_TERM = " OR ".join(f'"{term}"[tiab]' for term in QUERY_TERMS)

SLEEP = 0.34  # NCBI rate limit


def test_pmid_against_query(pmid: str) -> bool:
    """Test if a PMID matches ADC_QUERY_TERM."""
    params = {
        "db": "pubmed",
        "term": f"({ADC_QUERY_TERM}) AND {pmid}[uid]",
        "retmode": "json",
    }
    try:
        response = requests.get(ESEARCH_ENDPOINT, params=params, timeout=30)
        response.raise_for_status()
        count = int(response.json()["esearchresult"]["count"])
        return count > 0
    except Exception as e:
        print(f"Error querying PMID {pmid}: {e}", file=sys.stderr)
        return False


def main() -> None:
    samples = [json.loads(line) for line in LAYER3_FILE.open(encoding="utf-8")]

    results = []
    total_tested = 0
    total_matched = 0

    for sq in samples:
        ab_name = sq["antibody"]
        pmids = sq["pmids_found"]

        # Test up to 5 PMIDs per antibody
        test_pmids = pmids[:5] if len(pmids) >= 5 else pmids

        print(f"\n{ab_name}: testing {len(test_pmids)} PMIDs", file=sys.stderr)

        ab_matched = 0
        for pmid in test_pmids:
            matched = test_pmid_against_query(pmid)
            total_tested += 1
            if matched:
                ab_matched += 1
                total_matched += 1
            status = "✓" if matched else "✗"
            print(f"  {status} PMID {pmid}", file=sys.stderr)
            time.sleep(SLEEP)

        recall_pct = 100 * ab_matched / len(test_pmids) if test_pmids else 0
        results.append({
            "antibody": ab_name,
            "pmids_tested": len(test_pmids),
            "matched": ab_matched,
            "recall_pct": recall_pct,
        })

    print("\n" + "=" * 60)
    print("SAMPLE RECALL RESULTS (Layer 4 preliminary)")
    print("=" * 60)
    for r in results:
        print(f"{r['antibody']:15} {r['matched']}/{r['pmids_tested']} matched ({r['recall_pct']:.0f}%)")

    overall_recall = 100 * total_matched / total_tested if total_tested > 0 else 0
    print(f"\nOverall sample recall: {total_matched}/{total_tested} = {overall_recall:.1f}%")
    print(f"Sample: top 3 antibodies, {total_tested} PMIDs total")

    # Write results for report
    summary = {
        "layer": 4,
        "stage": "sample_preliminary",
        "seeds_represented": 12,
        "seeds_total": 51,
        "pmids_tested": total_tested,
        "pmids_matched": total_matched,
        "recall_pct": overall_recall,
        "by_antibody": results,
    }

    out_file = Path(__file__).parent / "layer4_sample_recall.json"
    with out_file.open("w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nResults saved to {out_file}")


if __name__ == "__main__":
    main()
