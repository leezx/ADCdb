---
name: adc-patent-download-monitor
description: Build and maintain an antibody-drug conjugate patent acquisition and monitoring workflow. Use when collecting, indexing, downloading, deduplicating, or biweekly monitoring ADC patents across antibody, linker, payload, conjugation/DAR/process, and whole-ADC categories. This is for patent intelligence data acquisition, not legal advice.
---

# ADC Patent Download and Monitoring

## Mission

Build a reproducible ADC patent intelligence dataset. Collect patent metadata, preserve source links, download full documents for ADC component and product categories, deduplicate patent families, and monitor for newly published records every two weeks.

Do not provide legal opinions. Use language such as "appears to claim", "may cover", and "manual legal review required". Do not say a patent blocks, clears, guarantees FTO, or is invalid.

## Category Policy

Classify each record into one primary category and optional secondary categories.

Primary category priority:

1. `whole_adc_combination`
2. `linker`
3. `payload`
4. `conjugation_dar_process`
5. `antibody_link_only`

Download full patent files for:

- `whole_adc_combination`: complete ADCs, target-specific ADCs, linker-payload-antibody combinations, dosing, biomarkers, indications, combination therapy, bispecific ADCs, dual-payload ADCs, masked/probody/conditional ADCs.
- `linker`: cleavable, non-cleavable, Val-Cit, peptide, hydrazone, disulfide, glucuronide, cathepsin-cleavable, acid-labile, redox-sensitive, self-immolative, PEG/hydrophilic, and linker-payload intermediate patents.
- `payload`: auristatin/MMAE/MMAF, maytansinoid/DM1/DM4, calicheamicin, PBD, duocarmycin, camptothecin/exatecan/DXd/topoisomerase I, amanitin, tubulin inhibitor, DNA-damaging, immunostimulatory, TLR/STING agonist, and ADC-like radionuclide payload patents.
- `conjugation_dar_process`: site-specific, cysteine, lysine, engineered cysteine, THIOMAB-like, enzymatic, glycan remodeling, transglutaminase, sortase, click, oxime/hydrazone conjugation, DAR control, homogeneous ADCs, analytics, purification, formulation, stability, manufacturing, aggregate/hydrophobicity reduction.

Do not mass-download antibody-only patents. For `antibody_link_only`, save metadata and source links only, including target, antibody name, epitope, CDR/VH/VL/sequence-region claims when available. If an antibody patent clearly claims an ADC product or composition, classify it as `whole_adc_combination` and download it.

## Sources

Use multiple sources because no database is complete:

- Google Patents as the default discovery source for readable records and family grouping.
- WIPO Patentscope for PCT publications.
- Espacenet or Lens to cross-check family members, priority data, legal status, and citations.
- USPTO, CNIPA, J-PlatPat, and KIPRIS when relevant and accessible.

Every record must keep at least one working source link. If a source blocks download, keep metadata and URL, log the failure, and continue.

## Search Strategy

Use layered searches. Expand terms in config rather than hard-coding them in scripts.

- Broad ADC terms: `"antibody drug conjugate"`, `"antibody-drug conjugate"`, `"ADC" "payload"`, `"ADC" "linker"`, `"ADC" "drug antibody ratio"`, `"ADC" "site-specific conjugation"`.
- Component terms: combine ADC terms with linker, payload, conjugation, DAR, process, formulation, purification, and manufacturing terms.
- Target terms: combine `{target}` with `"antibody drug conjugate"`, `"ADC"`, `"drug conjugate"`, and `"linker payload antibody"`.
- Company terms: search major ADC companies and common legacy names, including Seagen/Seattle Genetics, ImmunoGen, Daiichi Sankyo, AstraZeneca, Gilead, Mersana, Sutro, ProfoundBio, DualityBio, Kelun, LegoChem, ADC Therapeutics, Ambrx, Synaffix, Byondis, Tubulis, BioNTech, RemeGen, Hansoh, and BeiGene.
- Payload terms: search common payload families and names, including MMAE, MMAF, auristatin, maytansinoid, DM1, DM4, DXd, exatecan, camptothecin, topoisomerase I inhibitor, PBD, duocarmycin, amanitin, and calicheamicin.

Start target-specific searches with major ADC targets such as HER2, TROP2, Nectin-4, B7-H3, CEACAM5, EGFR, HER3, FRa, CD30, CD33, CD22, CD79b, Tissue factor, CLDN18.2, MUC1, MUC16, MET, LIV-1, ROR1, TWEAKR, Fn14, and TNFRSF12A.

## Output Layout

Create or maintain:

```text
ADC_Patents/
  README.md
  config/
    search_queries.yaml
    monitored_keywords.yaml
    source_urls.yaml
  index/
    raw_patent_records.tsv
    master_adc_patent_index.tsv
    master_adc_patent_index.json
    antibody_patent_link_index.tsv
    linker_patent_index.tsv
    payload_patent_index.tsv
    conjugation_dar_process_patent_index.tsv
    whole_adc_combination_patent_index.tsv
    patent_family_map.tsv
  downloads/
    linker/
    payload/
    conjugation_dar_process/
    whole_adc_combination/
  logs/
    crawl_log.tsv
    download_log.tsv
    error_log.tsv
    deduplication_log.tsv
    monitoring_log.tsv
  reports/
    initial_landscape_summary.md
    biweekly_update_latest.md
    new_patents_detected.tsv
    changed_status_patents.tsv
```

Name downloaded files:

```text
{category}__{publication_number}__{assignee_short}__{priority_year}__{clean_title}.pdf
{same_basename}.json
```

Metadata sidecars must include publication/application numbers, family ID if available, title, abstract, claims/full text/PDF URLs, assignee, inventors, dates, jurisdiction, legal status, category, matched keywords, source database, download timestamp, local path, and notes.

## Index Schema

The master index should support filtering by target, payload, linker, assignee, status, and priority/publication dates.

Required columns:

```text
record_id
category
sub_category
publication_number
application_number
family_id
family_representative
title
abstract
assignee
inventors
priority_date
filing_date
publication_date
jurisdiction
legal_status
expected_expiry_date
target_antigen
antibody_name
epitope_claimed
sequence_claimed
linker_type
payload_type
conjugation_method
DAR_claimed
indication
combination_claimed
claim_scope_summary
independent_claim_1_short
source_database
google_patents_url
lens_url
espacenet_url
wipo_url
pdf_url
local_pdf_path
local_json_path
download_status
manual_review_required
last_checked_date
new_or_updated
notes
```

Keep raw publication-level records. Create a family-level index separately.

## Deduplication

Deduplicate into families by:

1. explicit family ID
2. shared priority application
3. same title, assignee, and priority date
4. Google Patents family grouping

Never delete family members. Mark one representative per family using this preference order: WO, US, EP, CN, then other jurisdictions.

## Claim Extraction

For downloaded patents, extract and summarize:

- independent claim 1
- other independent claims
- claims mentioning antibody, linker, payload, DAR, conjugation, target, indication, dosing, biomarker, or combination therapy

Keep summaries factual and non-legal. Mark uncertain records with `manual_review_required = TRUE`.

## Monitoring

Provide a runnable biweekly command:

```bash
python scripts/09_biweekly_monitor.py --lookback-days 21
```

Each monitoring run must:

1. rerun monitored queries
2. search newly published documents from the lookback window
3. compare against existing raw and family indexes
4. detect new publications, new family members, legal-status changes where available, new assignees, targets, payloads, or linker chemistry
5. update indexes without duplicating records
6. download only categories `linker`, `payload`, `conjugation_dar_process`, and `whole_adc_combination`
7. keep antibody-only records link-only
8. generate a dated report and update `reports/biweekly_update_latest.md`

Record scan timestamps in ISO format. Back up existing indexes before overwriting them.

The biweekly report should include scan date, new record count, new family count, counts by category, notable assignees/targets/payloads/linkers, recommended manual-review downloads, and errors or gaps.

## Suggested Implementation

Use Python with a config-driven design:

```text
scripts/
  01_search_patents.py
  02_parse_results.py
  03_classify_patents.py
  04_download_patents.py
  05_extract_claims.py
  06_deduplicate_families.py
  07_generate_indexes.py
  08_generate_report.py
  09_biweekly_monitor.py
```

Prefer APIs, stable export links, and static HTML parsing before browser automation. Useful libraries include `requests`, `beautifulsoup4`, `pandas`, `pyyaml`, `python-dateutil`, `rapidfuzz`, and SQLite or DuckDB. Use Selenium or Playwright only when static access fails.

## Acceptance Criteria

- Every record has at least one working source link.
- Downloaded records have PDF and JSON metadata whenever available.
- Antibody-only patents are indexed but not mass-downloaded.
- Whole ADC, linker, payload, conjugation, DAR, and process patents are downloaded.
- Raw records and family-level records are both preserved.
- The master index is filterable by target, payload, linker, assignee, and dates.
- Monitoring can detect new publications and rerun without duplicating records.
- Every scan is reproducible and has logs.
