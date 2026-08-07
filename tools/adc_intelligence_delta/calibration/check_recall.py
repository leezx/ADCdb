#!/usr/bin/env python3
"""Experiment B (recall): for each paper in the independently-built gold
set, check whether the PRODUCTION ADC_QUERY_TERM would have matched it
(via `<query> AND <pmid>[uid]`, an authoritative round-trip through the
real PubMed API rather than a local text-matching simulation). Does not
modify the production query -- this is measurement only.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import requests  # noqa: E402

from sources.pubmed import ADC_QUERY_TERM, ESEARCH_ENDPOINT  # noqa: E402


def query_matches_pmid(pmid: str) -> bool:
    params = {
        "db": "pubmed",
        "term": f"({ADC_QUERY_TERM}) AND {pmid}[uid]",
        "retmode": "json",
    }
    response = requests.get(ESEARCH_ENDPOINT, params=params, timeout=30)
    response.raise_for_status()
    count = int(response.json()["esearchresult"]["count"])
    return count > 0


def main() -> None:
    gold_set_path = Path(__file__).parent / "gold_set.jsonl"
    gold = [json.loads(line) for line in gold_set_path.open(encoding="utf-8")]

    hits, misses = [], []
    for record in gold:
        matched = query_matches_pmid(record["pmid"])
        (hits if matched else misses).append(record)
        time.sleep(0.34)

    print(f"Gold set size: {len(gold)}")
    print(f"Production query recall: {len(hits)}/{len(gold)} ({100*len(hits)/len(gold):.1f}%)")
    print(f"Misses: {len(misses)}")

    out_dir = Path(__file__).parent
    with (out_dir / "recall_hits.jsonl").open("w", encoding="utf-8") as handle:
        for r in hits:
            handle.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (out_dir / "recall_misses.jsonl").open("w", encoding="utf-8") as handle:
        for r in misses:
            handle.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("Wrote recall_hits.jsonl and recall_misses.jsonl")


if __name__ == "__main__":
    main()
