#!/usr/bin/env python3
"""Layer 2: same-record PubMed DOI-exact linkage.

Measures whether each AACR/ASCO conference-abstract RECORD is itself
indexed in PubMed under its own DOI (`<doi>[doi]`). This is a corpus
indexing-coverage statistic, NOT a later-publication-linkage statistic --
a conference abstract's DOI is essentially never the DOI of a later full
paper on the same construct, so a miss here says nothing about whether
the underlying seed was ever published elsewhere. See REPORT_AACR_ASCO.md
for why these two questions are kept separate; conflating them was the
core flaw in this PR's first draft.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

import requests  # noqa: E402

from sources.pubmed import ESEARCH_ENDPOINT  # noqa: E402

CORPUS_PATH = Path(__file__).parent / "full_corpus.jsonl"
OUT_PATH = Path(__file__).parent / "layer2_doi_linkage_results.jsonl"
SLEEP = 0.34  # stay under NCBI's 3 req/sec unauthenticated limit


def doi_to_pmid(doi: str, timeout: int = 30, retries: int = 4) -> list[str]:
    params = {"db": "pubmed", "term": f"{doi}[doi]", "retmode": "json"}
    last_exc = None
    for attempt in range(retries):
        try:
            response = requests.get(ESEARCH_ENDPOINT, params=params, timeout=timeout)
            response.raise_for_status()
            result = response.json().get("esearchresult", {})
            return result.get("idlist", [])
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"doi_to_pmid failed after {retries} attempts for {doi!r}") from last_exc


def _load_already_done() -> set[tuple[str, str]]:
    """Resume support: (source, record_id) pairs already written to OUT_PATH."""
    if not OUT_PATH.exists():
        return set()
    done = set()
    for line in OUT_PATH.open(encoding="utf-8"):
        r = json.loads(line)
        done.add((r["source"], r["record_id"]))
    return done


def main() -> None:
    records = [json.loads(line) for line in CORPUS_PATH.open(encoding="utf-8")]
    already_done = _load_already_done()
    if already_done:
        print(f"Resuming: {len(already_done)} records already done", file=sys.stderr)

    no_doi = matched = no_match = 0
    processed = 0

    with OUT_PATH.open("a", encoding="utf-8") as f:
        for i, r in enumerate(records):
            if (r["source"], r["record_id"]) in already_done:
                continue

            doi = r.get("doi")
            if not doi:
                out = {**r, "doi_linkage_status": "DOI_LINKAGE_UNAVAILABLE", "matched_pmids": []}
                no_doi += 1
            else:
                pmids = doi_to_pmid(doi)
                if pmids:
                    out = {**r, "doi_linkage_status": "SAME_RECORD_PUBMED_MATCH", "matched_pmids": pmids}
                    matched += 1
                else:
                    out = {**r, "doi_linkage_status": "NO_EXACT_DOI_PMID_MATCH", "matched_pmids": []}
                    no_match += 1
                time.sleep(SLEEP)

            f.write(json.dumps(out, ensure_ascii=False) + "\n")
            f.flush()
            processed += 1
            if processed % 100 == 0:
                print(f"...{processed} newly processed (record {i + 1}/{len(records)})", file=sys.stderr)

    print(f"Newly processed this run: {processed}")
    print(f"  DOI_LINKAGE_UNAVAILABLE (no doi): {no_doi}")
    print(f"  SAME_RECORD_PUBMED_MATCH: {matched}")
    print(f"  NO_EXACT_DOI_PMID_MATCH: {no_match}")
    print(f"Wrote/appended to {OUT_PATH}")


if __name__ == "__main__":
    main()
