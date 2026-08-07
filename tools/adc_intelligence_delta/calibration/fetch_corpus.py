#!/usr/bin/env python3
"""Fetch-once tool for the PubMed Radar Calibration v0.1 experiments.
Dumps the current 45-day retrieval corpus to JSONL so classification and
recall checks work against a fixed, reproducible snapshot instead of a
live-changing PubMed index.

Usage:
    python3 fetch_corpus.py --since 2026-06-23 --out corpus_2026-06-23_2026-08-07.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from sources import pubmed  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="ISO date")
    parser.add_argument("--until", default=None, help="ISO date, default today")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    since = date.fromisoformat(args.since)
    until = date.fromisoformat(args.until) if args.until else None

    out_path = Path(args.out)
    count = 0
    with out_path.open("w", encoding="utf-8") as handle:
        for article in pubmed.fetch_articles(since, until):
            evidence = pubmed.to_evidence(article)
            record = {
                "pmid": article["pmid"],
                "title": article["title"],
                "abstract": article["abstract"],
                "publication_date": evidence.publication_date,
                "mentioned_assets": evidence.mentioned_assets,
                "url": evidence.source_url,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    print(f"Wrote {count} articles to {out_path}")


if __name__ == "__main__":
    main()
