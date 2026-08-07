#!/usr/bin/env python3
"""Fetch candidate papers for the Experiment B (recall) gold set using a
DIFFERENT retrieval methodology than the production query — the PubMed
MeSH controlled-vocabulary heading "Immunoconjugates" combined with
preclinical-signal keywords, rather than free-text phrase/suffix matching.
This independence is what makes checking the production query's recall
against this set meaningful rather than circular.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import requests  # noqa: E402

from sources.pubmed import EFETCH_ENDPOINT, ESEARCH_ENDPOINT, _parse_article  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402

MESH_QUERY = (
    'Immunoconjugates[Mesh] AND '
    '(internalization OR "cell-derived xenograft" OR PDX OR "patient-derived xenograft" '
    'OR "target expression" OR "novel target")'
)


def main() -> None:
    params = {
        "db": "pubmed",
        "term": MESH_QUERY,
        "datetype": "edat",
        "mindate": "2025/01/01",
        "maxdate": "2026/08/07",
        "retmax": "300",
        "retmode": "json",
    }
    response = requests.get(ESEARCH_ENDPOINT, params=params, timeout=30)
    response.raise_for_status()
    result = response.json()["esearchresult"]
    pmids = result["idlist"]
    print(f"MeSH-based query matched {result['count']} total; fetching {len(pmids)}")

    out_path = Path(__file__).parent / "gold_candidates_raw.jsonl"
    with out_path.open("w", encoding="utf-8") as handle:
        for start in range(0, len(pmids), 200):
            batch = pmids[start : start + 200]
            efetch_params = {"db": "pubmed", "id": ",".join(batch), "rettype": "abstract", "retmode": "xml"}
            efetch_response = requests.get(EFETCH_ENDPOINT, params=efetch_params, timeout=30)
            efetch_response.raise_for_status()
            root = ET.fromstring(efetch_response.content)
            for article_el in root.findall(".//PubmedArticle"):
                article = _parse_article(article_el)
                handle.write(json.dumps(article, ensure_ascii=False) + "\n")
            time.sleep(0.34)
    print(f"Wrote {len(pmids)} candidate articles to {out_path}")


if __name__ == "__main__":
    main()
