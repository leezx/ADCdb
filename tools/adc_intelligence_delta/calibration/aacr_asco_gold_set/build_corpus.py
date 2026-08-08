#!/usr/bin/env python3
"""Load the existing AACR/ASCO ADC abstract corpus from Zhixins-KB (already
built via Crossref, not re-scraped -- see the repo's memory note on this)
and normalize both source schemas into one flat JSONL. Read-only against
Zhixins-KB; writes only into this repo.
"""

from __future__ import annotations

import json
from pathlib import Path

ZHIXINS_KB_ADC_EXPERT = Path(
    "/Volumes/Stelligen_SSD/Stelligen/Zhixins-KB/2.Biotech/5.ADC_Expert"
)
YEARS = range(2016, 2027)
OUT_PATH = Path(__file__).parent / "full_corpus.jsonl"


def load_aacr_year(year: int) -> list[dict]:
    path = ZHIXINS_KB_ADC_EXPERT / "AACR_Abstracts" / f"AACR_{year}_ADC" / "adc_abstracts.json"
    if not path.exists():
        return []
    records = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for r in records:
        out.append(
            {
                "source": "AACR",
                "year": year,
                "record_id": r.get("abstract_number"),
                "doi": r.get("doi"),
                "title": r.get("title"),
                "abstract": r.get("abstract_text"),
                "authors": r.get("authors") or [],
                "publication_date": r.get("published_online") or r.get("published_print"),
            }
        )
    return out


def load_asco_year(year: int) -> list[dict]:
    path = ZHIXINS_KB_ADC_EXPERT / "ASCO_Abstracts" / f"ASCO_{year}_ADC" / "adc_abstracts.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for r in data.get("records", []):
        out.append(
            {
                "source": "ASCO",
                "year": year,
                "record_id": r.get("absId"),
                "doi": r.get("doi"),
                "title": r.get("title"),
                "abstract": r.get("abstract"),
                "authors": r.get("authors") or [],
                "publication_date": r.get("publication_date"),
            }
        )
    return out


def main() -> None:
    all_records = []
    for year in YEARS:
        all_records.extend(load_aacr_year(year))
        all_records.extend(load_asco_year(year))

    missing_doi = [r for r in all_records if not r["doi"]]
    missing_abstract = [r for r in all_records if not r["abstract"]]

    with OUT_PATH.open("w", encoding="utf-8") as f:
        for r in all_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"Total records: {len(all_records)}")
    print(f"Missing doi: {len(missing_doi)}")
    print(f"Missing abstract text: {len(missing_abstract)}")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
