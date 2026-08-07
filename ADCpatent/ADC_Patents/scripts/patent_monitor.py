#!/usr/bin/env python3
"""ADC patent collection and monitoring workflow.

This module intentionally uses only the Python standard library so the monitor can
run before project-specific dependencies are installed.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import os
import random
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
INDEX = ROOT / "index"
DOWNLOADS = ROOT / "downloads"
LOGS = ROOT / "logs"
REPORTS = ROOT / "reports"

MASTER_COLUMNS = [
    "record_id",
    "category",
    "sub_category",
    "publication_number",
    "application_number",
    "family_id",
    "family_representative",
    "title",
    "abstract",
    "assignee",
    "inventors",
    "priority_date",
    "filing_date",
    "publication_date",
    "jurisdiction",
    "legal_status",
    "expected_expiry_date",
    "target_antigen",
    "antibody_name",
    "epitope_claimed",
    "sequence_claimed",
    "linker_type",
    "payload_type",
    "conjugation_method",
    "DAR_claimed",
    "indication",
    "combination_claimed",
    "claim_scope_summary",
    "independent_claim_1_short",
    "source_database",
    "google_patents_url",
    "lens_url",
    "espacenet_url",
    "wipo_url",
    "pdf_url",
    "local_pdf_path",
    "local_json_path",
    "download_status",
    "manual_review_required",
    "last_checked_date",
    "new_or_updated",
    "notes",
]

RAW_COLUMNS = MASTER_COLUMNS + ["query", "keywords_matched", "downloaded_at"]

TARGET_TERMS = [
    "HER2", "TROP2", "Nectin-4", "B7-H3", "CEACAM5", "EGFR", "HER3",
    "FRa", "FRα", "CD30", "CD33", "CD22", "CD79b", "Tissue factor",
    "CLDN18.2", "MUC1", "MUC16", "MET", "LIV-1", "ROR1", "TWEAKR",
    "Fn14", "TNFRSF12A", "B7H4", "ERBB2", "ERBB3", "FOLR1", "NaPi2b",
    "SLC34A2", "PSMA", "CD19", "CD20", "CD25", "CD37", "CD38", "CD56",
    "CD70", "CD71", "CD74", "CD123", "CD138", "DLL3", "PTK7", "AXL",
    "c-Met", "mesothelin", "MSLN", "GPC3", "5T4", "SLC39A6",
]

PAYLOAD_TERMS = [
    "MMAE", "MMAF", "auristatin", "maytansinoid", "DM1", "DM4", "DXd",
    "exatecan", "camptothecin", "topoisomerase", "PBD", "duocarmycin",
    "amanitin", "calicheamicin", "deruxtecan", "SN-38", "SN38", "belotecan",
    "anthracycline", "pyrrolobenzodiazepine", "tubulysin", "eribulin",
    "doxorubicin", "maytansine", "amatoxin",
]

LINKER_TERMS = [
    "linker", "Val-Cit", "cleavable", "non-cleavable", "hydrazone",
    "disulfide", "glucuronide", "cathepsin", "self-immolative", "PEG",
    "hydrophilic",
]

CONJUGATION_TERMS = [
    "DAR", "drug antibody ratio", "site-specific", "site specific",
    "conjugation", "engineered cysteine", "transglutaminase", "glycan",
    "sortase", "click chemistry", "manufacturing", "purification",
    "purifying", "formulation", "formulations", "stability",
]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def ensure_dirs() -> None:
    for path in [
        CONFIG,
        INDEX,
        DOWNLOADS / "linker",
        DOWNLOADS / "payload",
        DOWNLOADS / "conjugation_dar_process",
        DOWNLOADS / "whole_adc_combination",
        LOGS,
        REPORTS,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def log_tsv(path: Path, row: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    fields = list(row.keys())
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def read_simple_yaml(path: Path) -> dict[str, list[str]]:
    data: dict[str, list[str]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not raw.startswith(" ") and line.endswith(":"):
            current = line[:-1]
            data[current] = []
            continue
        if current and line.startswith("- "):
            value = line[2:].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            data[current].append(value)
    return data


def load_queries() -> list[tuple[str, str]]:
    config = read_simple_yaml(CONFIG / "search_queries.yaml")
    queries: list[tuple[str, str]] = []
    for group in [
        "broad_adc",
        "linker",
        "payload",
        "conjugation_dar_process",
        "whole_adc_combination",
    ]:
        queries.extend((group, q) for q in config.get(group, []))
    for target in config.get("targets", []):
        queries.append(("target", f'"{target}" "antibody drug conjugate"'))
        queries.append(("target", f'"{target}" ADC'))
    for company in config.get("companies", []):
        queries.append(("company", f'"{company}" "antibody drug conjugate"'))
    for payload in config.get("payload_terms", []):
        queries.append(("payload", f'"{payload}" "antibody drug conjugate"'))
    return queries


def fetch_url(url: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 ADC-patent-monitor/0.1",
            "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_json_with_retries(url: str, retries: int, backoff: float) -> dict:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            body = fetch_url(url).decode("utf-8", errors="replace")
            return json.loads(body)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= retries:
                break
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
        sleep_for = backoff * (2 ** attempt) + random.uniform(0, backoff)
        time.sleep(sleep_for)
    raise last_error or RuntimeError("unknown fetch error")


def search_google_patents(
    query: str,
    max_results: int,
    pages_per_query: int,
    retries: int,
    backoff: float,
    page_delay: float,
) -> tuple[list[dict[str, str]], int]:
    inner_query = urllib.parse.quote(query)
    results: list[dict[str, str]] = []
    seen_pubs = set()
    error_pages = 0
    for page in range(max(1, pages_per_query)):
        url_parts = [f"q=({inner_query})", "dups=language"]
        if page:
            url_parts.append(f"page={page}")
        url_param = urllib.parse.quote("&".join(url_parts), safe="")
        url = f"https://patents.google.com/xhr/query?url={url_param}&exp="
        try:
            payload = fetch_json_with_retries(url, retries, backoff)
            total = str(payload.get("results", {}).get("total_num_results", ""))
            log_tsv(LOGS / "crawl_log.tsv", {
                "timestamp": now_iso(),
                "source": "google_patents",
                "query": query,
                "page": str(page),
                "url": url,
                "total_num_results": total,
                "status": "ok",
            })
        except Exception as exc:
            error_pages += 1
            log_tsv(LOGS / "error_log.tsv", {
                "timestamp": now_iso(),
                "stage": "search",
                "record": f"{query} page={page}",
                "error": repr(exc),
            })
            continue
        time.sleep(page_delay)
        page_count = 0
        for cluster in payload.get("results", {}).get("cluster", []):
            for item in cluster.get("result", []):
                patent = item.get("patent", {})
                pub = patent.get("publication_number", "")
                if not pub or pub in seen_pubs:
                    continue
                seen_pubs.add(pub)
                page_count += 1
                results.append({
                    "publication_number": pub,
                    "title": clean_text(patent.get("title", "")),
                    "abstract": clean_text(patent.get("snippet", "")),
                    "assignee": clean_text(patent.get("assignee", "")),
                    "inventors": clean_text(patent.get("inventor", "")),
                    "priority_date": patent.get("priority_date", ""),
                    "filing_date": patent.get("filing_date", ""),
                    "publication_date": patent.get("publication_date", ""),
                    "pdf_url": (
                        f"https://patentimages.storage.googleapis.com/{patent.get('pdf')}"
                        if patent.get("pdf")
                        else f"https://patents.google.com/patent/{pub}/pdf"
                    ),
                    "legal_status": summarize_country_status(patent),
                })
                if len(results) >= max_results:
                    return results, error_pages
        if page_count == 0:
            break
    return results, error_pages


def summarize_country_status(patent: dict) -> str:
    statuses = []
    aggregated = patent.get("family_metadata", {}).get("aggregated", {})
    for status in aggregated.get("country_status", [])[:8]:
        country = status.get("country_code", "")
        state = status.get("best_patent_stage", {}).get("state", "")
        if country and state:
            statuses.append(f"{country}:{state}")
    return "; ".join(statuses)


def meta_content(body: str, name: str) -> str:
    patterns = [
        rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']*)',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+name=["\']{re.escape(name)}["\']',
        rf'<meta[^>]+property=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']*)',
    ]
    for pattern in patterns:
        match = re.search(pattern, body, flags=re.I)
        if match:
            return html.unescape(match.group(1)).strip()
    return ""


def itemprop(body: str, prop: str) -> str:
    match = re.search(
        rf'<[^>]+itemprop=["\']{re.escape(prop)}["\'][^>]*>(.*?)</[^>]+>',
        body,
        flags=re.I | re.S,
    )
    if match:
        return clean_text(match.group(1))
    match = re.search(
        rf'<meta[^>]+itemprop=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']*)',
        body,
        flags=re.I,
    )
    return html.unescape(match.group(1)).strip() if match else ""


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def parse_patent(
    publication_number: str,
    query: str,
    seed: dict[str, str] | None = None,
    skip_detail_fetch: bool = False,
) -> dict[str, str]:
    seed = seed or {}
    url = f"https://patents.google.com/patent/{publication_number}/en"
    record = blank_record()
    record.update({
        "publication_number": publication_number,
        "record_id": publication_number,
        "source_database": "Google Patents",
        "google_patents_url": url,
        "lens_url": lens_url(publication_number),
        "espacenet_url": espacenet_url(publication_number),
        "wipo_url": wipo_url(publication_number),
        "pdf_url": seed.get("pdf_url") or f"https://patents.google.com/patent/{publication_number}/pdf",
        "query": query,
        "last_checked_date": now_iso(),
        "download_status": "metadata_only",
    })
    for field in [
        "title",
        "abstract",
        "assignee",
        "inventors",
        "priority_date",
        "filing_date",
        "publication_date",
        "legal_status",
    ]:
        record[field] = seed.get(field, "")
    if skip_detail_fetch:
        record["jurisdiction"] = publication_number[:2]
        record["category"] = classify(record)
        record["sub_category"] = infer_subcategory(record)
        record["keywords_matched"] = ", ".join(find_keywords(record))
        record["target_antigen"] = ", ".join(find_terms(record, TARGET_TERMS))
        record["payload_type"] = ", ".join(find_terms(record, PAYLOAD_TERMS))
        record["linker_type"] = ", ".join(find_terms(record, LINKER_TERMS))
        record["conjugation_method"] = ", ".join(find_terms(record, CONJUGATION_TERMS))
        record["manual_review_required"] = "TRUE" if record["category"] == "antibody_link_only" else "FALSE"
        record["new_or_updated"] = "new"
        return record
    try:
        body = fetch_url(url).decode("utf-8", errors="replace")
    except Exception as exc:
        record["notes"] = f"metadata fetch failed: {exc!r}"
        log_tsv(LOGS / "error_log.tsv", {
            "timestamp": now_iso(),
            "stage": "metadata",
            "record": publication_number,
            "error": repr(exc),
        })
        record["jurisdiction"] = publication_number[:2]
        record["category"] = classify(record)
        record["sub_category"] = infer_subcategory(record)
        record["keywords_matched"] = ", ".join(find_keywords(record))
        record["target_antigen"] = ", ".join(find_terms(record, TARGET_TERMS))
        record["payload_type"] = ", ".join(find_terms(record, PAYLOAD_TERMS))
        record["linker_type"] = ", ".join(find_terms(record, LINKER_TERMS))
        record["conjugation_method"] = ", ".join(find_terms(record, CONJUGATION_TERMS))
        record["manual_review_required"] = "TRUE"
        record["new_or_updated"] = "new"
        return record

    record["title"] = meta_content(body, "DC.title") or meta_content(body, "citation_title") or itemprop(body, "title") or record["title"]
    record["abstract"] = meta_content(body, "DC.description") or itemprop(body, "abstract") or record["abstract"]
    if not record["assignee"]:
        assignee = "; ".join(unique(re.findall(r'itemprop=["\']assigneeOriginal["\'][^>]*>(.*?)</', body, re.I | re.S)))
        record["assignee"] = clean_text(assignee) or meta_content(body, "DC.contributor")
    if not record["inventors"]:
        inventors = "; ".join(clean_text(x) for x in unique(re.findall(r'itemprop=["\']inventor["\'][^>]*>(.*?)</', body, re.I | re.S)))
        record["inventors"] = inventors
    record["publication_date"] = itemprop(body, "publicationDate") or record["publication_date"]
    record["filing_date"] = itemprop(body, "filingDate") or record["filing_date"]
    record["priority_date"] = itemprop(body, "priorityDate") or record["priority_date"]
    record["application_number"] = itemprop(body, "applicationNumber") or record["application_number"]
    record["family_id"] = itemprop(body, "familyID") or record["family_id"]
    record["jurisdiction"] = publication_number[:2]
    record["legal_status"] = itemprop(body, "status") or record["legal_status"]
    record["category"] = classify(record)
    record["sub_category"] = infer_subcategory(record)
    record["keywords_matched"] = ", ".join(find_keywords(record))
    record["target_antigen"] = ", ".join(find_terms(record, TARGET_TERMS))
    record["payload_type"] = ", ".join(find_terms(record, PAYLOAD_TERMS))
    record["linker_type"] = ", ".join(find_terms(record, LINKER_TERMS))
    record["conjugation_method"] = ", ".join(find_terms(record, CONJUGATION_TERMS))
    record["manual_review_required"] = "TRUE" if not record["title"] or record["category"] == "antibody_link_only" else "FALSE"
    record["new_or_updated"] = "new"
    return record


def lens_url(publication_number: str) -> str:
    return f"https://www.lens.org/lens/search/patent/list?q={urllib.parse.quote(publication_number)}"


def espacenet_url(publication_number: str) -> str:
    return f"https://worldwide.espacenet.com/patent/search?q={urllib.parse.quote(publication_number)}"


def wipo_url(publication_number: str) -> str:
    if publication_number.startswith("WO"):
        return f"https://patentscope.wipo.int/search/en/detail.jsf?docId={urllib.parse.quote(publication_number)}"
    return f"https://patentscope.wipo.int/search/en/result.jsf?queryString={urllib.parse.quote(publication_number)}"


def merge_source_names(existing: str, incoming: str) -> str:
    names = []
    for value in [existing, incoming]:
        for name in re.split(r"[;,]", value or ""):
            cleaned = name.strip()
            if cleaned and cleaned not in names:
                names.append(cleaned)
    return "; ".join(names)


def blank_record() -> dict[str, str]:
    return {column: "" for column in RAW_COLUMNS}


def unique(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        cleaned = clean_text(value)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


def searchable(record: dict[str, str]) -> str:
    return " ".join([record.get("title", ""), record.get("abstract", ""), record.get("query", "")]).lower()


def find_terms(record: dict[str, str], terms: list[str]) -> list[str]:
    text = searchable(record)
    found = []
    for term in terms:
        escaped = re.escape(term.lower())
        if re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text):
            found.append(term)
    return found


def find_keywords(record: dict[str, str]) -> list[str]:
    return find_terms(record, TARGET_TERMS + PAYLOAD_TERMS + LINKER_TERMS + CONJUGATION_TERMS)


def classify(record: dict[str, str]) -> str:
    text = searchable(record)
    has_adc = any(term in text for term in ["antibody drug conjugate", "antibody-drug conjugate", " adc ", "drug conjugate"])
    has_payload = bool(find_terms(record, PAYLOAD_TERMS))
    has_linker = bool(find_terms(record, LINKER_TERMS))
    has_conj = bool(find_terms(record, CONJUGATION_TERMS))
    has_target = bool(find_terms(record, TARGET_TERMS))
    has_combo = any(term in text for term in ["combination", "dosing", "biomarker", "bispecific", "dual payload", "masked", "probody"])
    if has_adc and (has_target or (has_payload and has_linker) or has_combo):
        return "whole_adc_combination"
    if has_adc and has_conj:
        return "conjugation_dar_process"
    if has_linker:
        return "linker"
    if has_payload:
        return "payload"
    if has_conj:
        return "conjugation_dar_process"
    if has_adc:
        return "whole_adc_combination"
    return "antibody_link_only"


def infer_subcategory(record: dict[str, str]) -> str:
    category = record.get("category", "")
    if category == "whole_adc_combination":
        return "product_or_composition"
    if category == "antibody_link_only":
        return "antibody_only_or_uncertain"
    return category


def clean_filename(value: str, limit: int = 90) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return (value[:limit].strip("_") or "untitled")


def download_record(record: dict[str, str], skip_downloads: bool) -> dict[str, str]:
    category = record.get("category", "")
    if category == "antibody_link_only":
        record["download_status"] = "link_only"
        return write_sidecar(record)
    if skip_downloads:
        record["download_status"] = "skipped"
        return write_sidecar(record)
    target_dir = DOWNLOADS / category
    target_dir.mkdir(parents=True, exist_ok=True)
    year = (record.get("priority_date", "")[:4] or "unknown")
    assignee = clean_filename(record.get("assignee", "unknown"), 32)
    title = clean_filename(record.get("title", "untitled"))
    base = f"{category}__{record['publication_number']}__{assignee}__{year}__{title}"
    pdf_path = target_dir / f"{base}.pdf"
    try:
        pdf = fetch_url(record["pdf_url"])
        if pdf.startswith(b"%PDF"):
            pdf_path.write_bytes(pdf)
            record["local_pdf_path"] = str(pdf_path)
            record["download_status"] = "downloaded"
            log_tsv(LOGS / "download_log.tsv", {
                "timestamp": now_iso(),
                "publication_number": record["publication_number"],
                "category": category,
                "status": "downloaded",
                "local_pdf_path": str(pdf_path),
            })
        else:
            record["download_status"] = "metadata_only"
            record["notes"] = append_note(record["notes"], "PDF URL did not return a PDF")
    except Exception as exc:
        record["download_status"] = "failed_source_blocked"
        record["notes"] = append_note(record["notes"], f"PDF download failed: {exc!r}")
        log_tsv(LOGS / "error_log.tsv", {
            "timestamp": now_iso(),
            "stage": "download",
            "record": record["publication_number"],
            "error": repr(exc),
        })
    return write_sidecar(record)


def write_sidecar(record: dict[str, str]) -> dict[str, str]:
    category = record.get("category", "metadata")
    target_dir = DOWNLOADS / category if category != "antibody_link_only" else INDEX
    target_dir.mkdir(parents=True, exist_ok=True)
    sidecar = target_dir / f"{record['publication_number']}.json"
    sidecar.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    record["local_json_path"] = str(sidecar)
    record["downloaded_at"] = now_iso()
    return record


def append_note(existing: str, note: str) -> str:
    return f"{existing}; {note}" if existing else note


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(path: Path, rows: list[dict[str, str]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(path.suffix + f".bak.{dt.datetime.now().strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(path, backup)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def merge_records(existing: list[dict[str, str]], incoming: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    by_pub = {row.get("publication_number", ""): row for row in existing if row.get("publication_number")}
    new_rows = []
    for row in incoming:
        pub = row.get("publication_number", "")
        if not pub:
            continue
        if pub in by_pub:
            old = by_pub[pub]
            merged = {**old, **row}
            merged["source_database"] = merge_source_names(old.get("source_database", ""), row.get("source_database", ""))
            merged["new_or_updated"] = "updated"
            by_pub[pub] = merged
        else:
            by_pub[pub] = row
            new_rows.append(row)
    return list(by_pub.values()), new_rows


def mark_family_representatives(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        key = row.get("family_id") or "|".join([row.get("title", ""), row.get("assignee", ""), row.get("priority_date", "")])
        groups.setdefault(key, []).append(row)
        row["family_representative"] = "FALSE"
    pref = {"WO": 0, "US": 1, "EP": 2, "CN": 3}
    for members in groups.values():
        representative = sorted(members, key=lambda r: pref.get((r.get("publication_number") or "")[:2], 99))[0]
        representative["family_representative"] = "TRUE"
    return rows


def write_indexes(rows: list[dict[str, str]]) -> None:
    rows = mark_family_representatives(rows)
    write_tsv(INDEX / "raw_patent_records.tsv", rows, RAW_COLUMNS)
    write_tsv(INDEX / "master_adc_patent_index.tsv", rows, MASTER_COLUMNS)
    (INDEX / "master_adc_patent_index.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    for category, filename in [
        ("antibody_link_only", "antibody_patent_link_index.tsv"),
        ("linker", "linker_patent_index.tsv"),
        ("payload", "payload_patent_index.tsv"),
        ("conjugation_dar_process", "conjugation_dar_process_patent_index.tsv"),
        ("whole_adc_combination", "whole_adc_combination_patent_index.tsv"),
    ]:
        write_tsv(INDEX / filename, [r for r in rows if r.get("category") == category], MASTER_COLUMNS)
    family_rows = []
    for row in rows:
        if row.get("family_representative") == "TRUE":
            family_rows.append({
                "family_id": row.get("family_id") or row.get("record_id"),
                "representative_publication": row.get("publication_number"),
                "category": row.get("category"),
                "title": row.get("title"),
                "assignee": row.get("assignee"),
                "priority_date": row.get("priority_date"),
            })
    write_tsv(INDEX / "patent_family_map.tsv", family_rows, [
        "family_id", "representative_publication", "category", "title", "assignee", "priority_date"
    ])


def generate_report(rows: list[dict[str, str]], new_rows: list[dict[str, str]], errors: int) -> None:
    counts = {}
    for row in rows:
        counts[row.get("category", "")] = counts.get(row.get("category", ""), 0) + 1
    new_counts = {}
    for row in new_rows:
        new_counts[row.get("category", "")] = new_counts.get(row.get("category", ""), 0) + 1
    report = [
        "# ADC Patent Monitoring Update",
        "",
        f"- date_of_scan: {now_iso()}",
        f"- number_of_total_records: {len(rows)}",
        f"- number_of_new_records: {len(new_rows)}",
        f"- number_of_new_families: {sum(1 for r in new_rows if r.get('family_representative') == 'TRUE')}",
        f"- new_whole_ADC_patents: {new_counts.get('whole_adc_combination', 0)}",
        f"- new_linker_patents: {new_counts.get('linker', 0)}",
        f"- new_payload_patents: {new_counts.get('payload', 0)}",
        f"- new_conjugation_or_DAR_patents: {new_counts.get('conjugation_dar_process', 0)}",
        f"- new_antibody_link_only_records: {new_counts.get('antibody_link_only', 0)}",
        f"- errors_or_gaps: {errors} errors logged during this run",
        "",
        "## Category Counts",
        "",
    ]
    for category in sorted(counts):
        report.append(f"- {category}: {counts[category]}")
    report.extend(["", "## Recommended Manual Review", ""])
    for row in new_rows[:25]:
        report.append(f"- {row.get('publication_number')}: {row.get('title')} ({row.get('category')})")
    text = "\n".join(report) + "\n"
    dated = REPORTS / f"biweekly_update_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    dated.write_text(text, encoding="utf-8")
    (REPORTS / "biweekly_update_latest.md").write_text(text, encoding="utf-8")
    if not (REPORTS / "initial_landscape_summary.md").exists():
        (REPORTS / "initial_landscape_summary.md").write_text(text, encoding="utf-8")
    write_tsv(REPORTS / "new_patents_detected.tsv", new_rows, MASTER_COLUMNS)
    write_tsv(REPORTS / "changed_status_patents.tsv", [], MASTER_COLUMNS)


def run_monitor(args: argparse.Namespace) -> int:
    ensure_dirs()
    if args.reclassify_existing:
        rows = read_tsv(INDEX / "raw_patent_records.tsv")
        for row in rows:
            pub = row.get("publication_number", "")
            row["record_id"] = pub
            row["google_patents_url"] = row.get("google_patents_url") or f"https://patents.google.com/patent/{pub}/en"
            row["lens_url"] = row.get("lens_url") or lens_url(pub)
            row["espacenet_url"] = row.get("espacenet_url") or espacenet_url(pub)
            row["wipo_url"] = row.get("wipo_url") or wipo_url(pub)
            row["category"] = classify(row)
            row["sub_category"] = infer_subcategory(row)
            row["keywords_matched"] = ", ".join(find_keywords(row))
            row["target_antigen"] = ", ".join(find_terms(row, TARGET_TERMS))
            row["payload_type"] = ", ".join(find_terms(row, PAYLOAD_TERMS))
            row["linker_type"] = ", ".join(find_terms(row, LINKER_TERMS))
            row["conjugation_method"] = ", ".join(find_terms(row, CONJUGATION_TERMS))
            row["manual_review_required"] = "TRUE" if not row.get("title") or row["category"] == "antibody_link_only" else "FALSE"
        write_indexes(rows)
        generate_report(rows, [], 0)
        return 0
    queries = load_queries()
    if args.query_start:
        queries = queries[args.query_start :]
    if args.query_end:
        queries = queries[: args.query_end - args.query_start if args.query_start else args.query_end]
    if args.query_limit:
        queries = queries[: args.query_limit]
    incoming: list[dict[str, str]] = []
    errors_before = len(read_tsv(LOGS / "error_log.tsv"))
    seen = set()
    consecutive_failed_queries = 0
    for group, query in queries:
        results, error_pages = search_google_patents(
            query,
            args.max_results_per_query,
            args.pages_per_query,
            args.retries,
            args.retry_backoff,
            args.page_delay,
        )
        if not results and error_pages:
            consecutive_failed_queries += 1
            if consecutive_failed_queries >= args.max_failed_queries:
                log_tsv(LOGS / "monitoring_log.tsv", {
                    "timestamp": now_iso(),
                    "lookback_days": str(args.lookback_days),
                    "queries_run": "partial",
                    "records_seen": str(len(incoming)),
                    "new_records": "unknown",
                    "skip_downloads": str(args.skip_downloads),
                    "status": f"stopped_after_{consecutive_failed_queries}_failed_queries",
                })
                break
            continue
        consecutive_failed_queries = 0
        for seed in results:
            pub = seed.get("publication_number", "")
            if pub in seen:
                continue
            seen.add(pub)
            record = parse_patent(pub, query, seed, args.skip_detail_fetch)
            record["sub_category"] = record["sub_category"] or group
            record = download_record(record, args.skip_downloads)
            incoming.append(record)
            time.sleep(args.delay)
    existing = read_tsv(INDEX / "raw_patent_records.tsv")
    merged, new_rows = merge_records(existing, incoming)
    write_indexes(merged)
    errors = max(0, len(read_tsv(LOGS / "error_log.tsv")) - errors_before)
    generate_report(merged, new_rows, errors)
    log_tsv(LOGS / "monitoring_log.tsv", {
        "timestamp": now_iso(),
        "lookback_days": str(args.lookback_days),
        "queries_run": str(len(queries)),
        "records_seen": str(len(incoming)),
        "new_records": str(len(new_rows)),
        "skip_downloads": str(args.skip_downloads),
    })
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ADC patent biweekly monitor")
    parser.add_argument("--lookback-days", type=int, default=21)
    parser.add_argument("--max-results-per-query", type=int, default=50)
    parser.add_argument("--pages-per-query", type=int, default=5)
    parser.add_argument("--query-limit", type=int, default=0, help="Limit queries for smoke tests")
    parser.add_argument("--query-start", type=int, default=0, help="Start query offset for batched runs")
    parser.add_argument("--query-end", type=int, default=0, help="End query offset for batched runs")
    parser.add_argument("--skip-detail-fetch", action="store_true", help="Use Google search-result metadata only")
    parser.add_argument("--skip-downloads", action="store_true")
    parser.add_argument("--reclassify-existing", action="store_true", help="Recompute indexes from existing raw records without network access")
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--page-delay", type=float, default=1.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-backoff", type=float, default=2.0)
    parser.add_argument("--max-failed-queries", type=int, default=3)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_monitor(args)


if __name__ == "__main__":
    raise SystemExit(main())
