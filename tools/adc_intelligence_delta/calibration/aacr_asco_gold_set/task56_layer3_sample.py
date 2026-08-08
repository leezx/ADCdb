#!/usr/bin/env python3
"""
Task #56: Layer 3 - Sample independent PubMed queries for top antibodies.

Instead of querying every seed (which would be 51 independent searches),
query only the top N antibodies that appear in multiple seeds.
This gives a statistically meaningful estimate of later-publication coverage.

Output: layer3_sample_queries.jsonl with candidate PMIDs per antibody.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests  # noqa: E402

OUT_DIR = Path(__file__).parent
SEEDS_FILE = OUT_DIR / "unique_adc_seeds.jsonl"
OUT_PATH = OUT_DIR / "layer3_sample_queries.jsonl"

# Top antibodies from analyze_seeds output
TOP_ANTIBODIES = [
    ("sacituzumab", 6),  # (name, count in seeds)
    ("datopotamab", 3),
    ("trastuzumab", 3),
]

ESEARCH_ENDPOINT = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
SLEEP = 0.34  # respect NCBI rate limit


def query_pubmed(query_term: str, retmax: int = 10) -> list[str]:
    """Query PubMed for a term, return up to retmax PMIDs."""
    params = {"db": "pubmed", "term": query_term, "retmax": retmax, "retmode": "json"}
    for attempt in range(3):
        try:
            response = requests.get(ESEARCH_ENDPOINT, params=params, timeout=30)
            response.raise_for_status()
            result = response.json().get("esearchresult", {})
            return result.get("idlist", [])
        except requests.exceptions.RequestException:
            time.sleep(1.5 * (attempt + 1))
    return []


def extract_years_from_seeds() -> dict[str, set[int]]:
    """Map antibody names to set of years they appear in seeds."""
    if not SEEDS_FILE.exists():
        return {}

    antibody_years = {}
    for line in SEEDS_FILE.open(encoding="utf-8"):
        seed = json.loads(line)
        title = seed.get("title", "").lower()
        abstract = seed.get("abstract", "").lower()
        year = seed.get("year")

        for ab_name, _ in TOP_ANTIBODIES:
            if ab_name.lower() in title or ab_name.lower() in abstract:
                if ab_name not in antibody_years:
                    antibody_years[ab_name] = set()
                antibody_years[ab_name].add(year)

    return antibody_years


def main() -> None:
    print(f"Layer 3 Sample: querying top antibodies for later-publication coverage", file=sys.stderr)

    antibody_years = extract_years_from_seeds()

    results = []
    for ab_name, seed_count in TOP_ANTIBODIES:
        years_in_seeds = sorted(antibody_years.get(ab_name, set()))
        min_year = min(years_in_seeds) if years_in_seeds else 2020

        # Query: antibody name + ADC-related terms, with year filter for "later" publications
        # "Later" = published after the seed year
        query = f'({ab_name} AND (antibody-drug OR ADC)) AND ({min_year+1}[pdat]:{(min_year+3)}[pdat])'

        print(f"Querying: {query}", file=sys.stderr)
        pmids = query_pubmed(query, retmax=20)
        time.sleep(SLEEP)

        result = {
            "antibody": ab_name,
            "seed_count": seed_count,
            "seed_years": years_in_seeds,
            "query": query,
            "pmids_found": pmids,
            "pmid_count": len(pmids),
        }
        results.append(result)
        print(f"  {ab_name}: {len(pmids)} PMIDs found", file=sys.stderr)

    # Write output
    with OUT_PATH.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\nLayer 3 Sample Complete:")
    print(f"Wrote {len(results)} query results to {OUT_PATH}")
    print(f"\nNote: This is a SAMPLE, not exhaustive. To fully implement Layer 3:")
    print(f"  1. For each of the 51 seeds, construct a unique PubMed query")
    print(f"  2. For PMIDs found, verify they are 'later-publications' (after conference year)")
    print(f"  3. Apply ADC_QUERY_TERM to those PMIDs and compute recall")
    print(f"  4. Report: N seeds → N later-published PMIDs → X recall (ADC_QUERY_TERM hits / total)")


if __name__ == "__main__":
    main()
