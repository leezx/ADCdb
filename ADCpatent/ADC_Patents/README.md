# ADC Patent Intelligence Dataset

This directory is generated from `ADC_Patent_Download_Monitor.skill.md`.

The workflow collects ADC patent metadata, keeps antibody-only records as link-only entries, downloads full PDFs for ADC linker/payload/conjugation/process/whole-product records when available, deduplicates patent families, and supports biweekly monitoring.

Run the monitoring workflow from this directory:

```bash
python scripts/09_biweekly_monitor.py --lookback-days 21
```

For a smaller smoke test:

```bash
python scripts/09_biweekly_monitor.py --lookback-days 21 --max-results-per-query 5 --skip-downloads
```

For the broad index-first build, do not download PDFs. Run in slow batches to avoid Google Patents throttling:

```bash
python scripts/09_biweekly_monitor.py --query-start 0 --query-end 20 --max-results-per-query 100 --pages-per-query 10 --skip-detail-fetch --skip-downloads --page-delay 8 --retry-backoff 20 --retries 2
python scripts/09_biweekly_monitor.py --query-start 20 --query-end 40 --max-results-per-query 100 --pages-per-query 10 --skip-detail-fetch --skip-downloads --page-delay 8 --retry-backoff 20 --retries 2
python scripts/09_biweekly_monitor.py --query-start 40 --query-end 60 --max-results-per-query 100 --pages-per-query 10 --skip-detail-fetch --skip-downloads --page-delay 8 --retry-backoff 20 --retries 2
python scripts/09_biweekly_monitor.py --query-start 60 --query-end 80 --max-results-per-query 100 --pages-per-query 10 --skip-detail-fetch --skip-downloads --page-delay 8 --retry-backoff 20 --retries 2
python scripts/09_biweekly_monitor.py --query-start 80 --query-end 120 --max-results-per-query 100 --pages-per-query 10 --skip-detail-fetch --skip-downloads --page-delay 8 --retry-backoff 20 --retries 2
python scripts/09_biweekly_monitor.py --query-start 120 --query-end 160 --max-results-per-query 100 --pages-per-query 10 --skip-detail-fetch --skip-downloads --page-delay 8 --retry-backoff 20 --retries 2
python scripts/09_biweekly_monitor.py --query-start 160 --query-end 216 --max-results-per-query 100 --pages-per-query 10 --skip-detail-fetch --skip-downloads --page-delay 8 --retry-backoff 20 --retries 2
```

Use publication number as the unique index key. The master index includes Google Patents plus generated Lens, Espacenet, and WIPO links. Automated Lens, EPO OPS, WIPO, USPTO, and CNIPA enrichment requires credentials or source-specific access; see `reports/source_status.md`.
