"""Captures company press releases / material disclosures via SEC EDGAR's
free full-text search API (https://efts.sec.gov/LATEST/search-index),
normalized into source-independent EvidenceRecords (see contracts.py).

No API key required — official, free SEC EDGAR full-text search endpoint.
This is the fourth source (after ClinicalTrials.gov, FDA, PubMed/AACR/ASCO)
and closes the v0.1 source coverage named in the original design.

Why SEC EDGAR and not company IR pages directly: company press-release
pages have no shared API or feed format — every company runs its own site,
and scraping ~400+ individual biotech IR pages is exactly the kind of
per-source scraper sprawl this pipeline's source-adapter pattern exists to
avoid (see DESIGN.md). SEC EDGAR full-text search instead gives one
official, free, no-key API across every US-listed company's 8-K filings —
material events (which for a clinical-stage biotech almost always includes
clinical readouts, regulatory actions, and pipeline updates) get filed as
exhibits (typically EX-99.1) attached to Form 8-K within 4 business days.
This only covers US-listed/SEC-reporting companies — private biotechs and
non-US-listed companies (common for early-stage academic spinouts and
some ex-US pharma) are a structural gap, not a bug; see README.

The search is a simple full-text keyword match on the whole 8-K filing
text, not a title/abstract-only match like PubMed's [tiab] qualifier —
SEC EDGAR full-text search doesn't expose that granularity. This trades
precision for the fact that 8-K exhibits are already narrowly-scoped
press releases (unlike PubMed's much larger corpus), so keyword-in-body
false-positive risk is lower than it would be against, say, full journal
articles.
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import date, datetime, timezone
from typing import Iterator

import requests

from contracts import EvidenceRecord

EDGAR_SEARCH_ENDPOINT = "https://efts.sec.gov/LATEST/search-index"
EDGAR_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

# Same precision-first pattern as clinicaltrials.py / pubmed.py: multi-word
# phrases and payload-name suffixes only, no bare "ADC" (SEC filings are
# financial/legal documents where "ADC" collides with unrelated acronyms
# — e.g. "Analog-to-Digital Converter" appears in semiconductor 10-Ks).
ADC_QUERY_TERMS = (
    '"antibody-drug conjugate"',
    '"antibody drug conjugate"',
    "vedotin",
    "deruxtecan",
    "govitecan",
    "mafodotin",
    "tesirine",
    "emtansine",
    "ozogamicin",
    "tirumotecan",
)

# SEC EDGAR full-text search requires an identifying User-Agent per their
# fair-access policy (https://www.sec.gov/os/webmaster-faq#developers) —
# unlike CT.gov/openFDA/PubMed this is enforced, not optional. SEC expects
# a real contact address here (they will rate-limit or block a generic/
# fake one under sustained use), so this reads from an env var rather than
# hardcoding a placeholder that would silently stay wrong in production.
#
# This must fail loudly rather than send any default: an earlier version
# of this constant used an em-dash ("—") in the placeholder text, which
# `http.client.putheader()` encodes as Latin-1 before sending -- any
# non-Latin-1 character in a header value raises UnicodeEncodeError deep
# inside `requests.get()`, well past the point a caller could catch it
# meaningfully. Sending a fake-but-valid placeholder would be just as
# wrong per SEC's fair-access policy (it wants a real contact, not any
# non-empty string), so raising here instead of falling back is the
# correct fix, not just the safe one.
class MissingUserAgentError(RuntimeError):
    pass


def _user_agent() -> str:
    # Read at call time, not import time, so a caller that sets the env
    # var programmatically after `import company_pr` (e.g. a script that
    # loads a .env file, or a test) still gets picked up.
    value = os.environ.get("ADCDB_EDGAR_USER_AGENT", "").strip()
    if not value:
        raise MissingUserAgentError(
            "ADCDB_EDGAR_USER_AGENT is not set. SEC EDGAR's fair-access policy "
            "requires a real, identifying User-Agent (organization + contact "
            "email) on every request -- see "
            "https://www.sec.gov/os/webmaster-faq#developers. Set "
            "ADCDB_EDGAR_USER_AGENT to something like "
            "'ADCdb Intelligence Delta (your-real-email@example.org)' before "
            "calling fetch_filings()."
        )
    try:
        value.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise MissingUserAgentError(
            f"ADCDB_EDGAR_USER_AGENT contains a character HTTP headers can't "
            f"carry (must be Latin-1 encodable): {exc}"
        ) from exc
    # C0 control characters (CR, LF, NUL, ...) pass the Latin-1 check
    # above (they're all in range 0-255) but are invalid inside an HTTP
    # header value -- requests would eventually reject them with its own
    # InvalidHeader error, but only after this function had already
    # claimed the value was fine, defeating the point of validating at
    # the configuration boundary. Tab is allowed (valid in header values).
    if any(ord(ch) < 0x20 and ch != "\t" for ch in value):
        raise MissingUserAgentError(
            "ADCDB_EDGAR_USER_AGENT contains a control character, which is "
            "not valid inside an HTTP header value."
        )
    return value


def fetch_filings(
    since: date,
    limit: int = 100,
    timeout: int = 30,
    sleep: float = 0.3,
) -> Iterator[dict]:
    """Yield raw SEC EDGAR full-text search hits for 8-K filings mentioning
    any ADC term, filed since the given date. Deduplicates across the
    per-term queries (the API doesn't support OR across quoted phrases in
    one request), keyed on the hit's _id."""
    until = date.today()
    seen_ids: set[str] = set()

    for term in ADC_QUERY_TERMS:
        start = 0
        while True:
            params = {
                "q": term,
                "forms": "8-K",
                "dateRange": "custom",
                "startdt": since.isoformat(),
                "enddt": until.isoformat(),
                "from": str(start),
            }
            headers = {"User-Agent": _user_agent()}
            response = requests.get(EDGAR_SEARCH_ENDPOINT, params=params, headers=headers, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            hits = (data.get("hits", {}) or {}).get("hits", []) or []
            if not hits:
                break

            for hit in hits:
                hit_id = hit.get("_id", "")
                if hit_id in seen_ids:
                    continue
                seen_ids.add(hit_id)
                yield hit

            start += limit
            total = (data.get("hits", {}) or {}).get("total", {}).get("value", 0)
            if start >= total:
                break
            time.sleep(sleep)


def _filing_url(hit: dict) -> str:
    """Reconstruct the direct exhibit URL from an EDGAR full-text search hit.

    hit['_id'] is "{accession-with-dashes}:{file_name}"; the archive path
    needs the accession number's dashes stripped for the directory name.
    """
    hit_id = hit.get("_id", "")
    if ":" not in hit_id:
        return ""
    accession, file_name = hit_id.split(":", 1)
    accession_no_dashes = accession.replace("-", "")
    source = hit.get("_source", {}) or {}
    ciks = source.get("ciks", []) or []
    if not ciks:
        return ""
    cik = ciks[0].lstrip("0") or "0"
    return f"{EDGAR_ARCHIVES_BASE}/{cik}/{accession_no_dashes}/{file_name}"


def to_evidence(hit: dict) -> EvidenceRecord:
    """Turn one raw EDGAR full-text search hit into an EvidenceRecord. This
    is the only function downstream code should call — nothing else in this
    module produces source-agnostic output.

    Note on evidence_text: EDGAR full-text search returns metadata about
    the filing, not the exhibit's actual press-release body — fetching and
    parsing every exhibit's HTML is out of scope for v0.1 (see README).
    evidence_text is therefore a deterministic serialization of the
    available metadata (company, filing items, form type), same pattern as
    fda.py's evidence_text for openFDA's structured-only records. The
    filing_url in provenance is the citable source for the actual text.
    """
    source = hit.get("_source", {}) or {}
    display_names = source.get("display_names", []) or []
    company_name = display_names[0] if display_names else "Unknown"
    file_date = source.get("file_date", "")
    items = source.get("items", []) or []
    adsh = source.get("adsh", "")
    filing_url = _filing_url(hit)

    evidence_text = (
        f"SEC Form 8-K filed by {company_name} on {file_date}. "
        f"Item(s): {', '.join(items) if items else 'unspecified'}. "
        f"Exhibit: {source.get('file_description', source.get('file_type', ''))}."
    )

    return EvidenceRecord(
        evidence_id=_evidence_id("company_pr", adsh, hit.get("_id", "")),
        source_type="company_pr",
        source_name="SEC EDGAR (8-K full-text search)",
        source_url=filing_url,
        source_record_id=adsh,
        publication_date=file_date or None,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        title=f"{company_name} — Form 8-K ({file_date})",
        evidence_text=evidence_text,
        mentioned_assets=[],
        mentioned_targets=[],
        mentioned_indications=[],
        evidence_class="COMPANY_DISCLOSURE",
        confidence="raw",
        provenance={
            "cik": (source.get("ciks", []) or [None])[0],
            "company_display_name": company_name,
            "accession_number": adsh,
            "form": source.get("form", ""),
            "items": items,
            "file_type": source.get("file_type", ""),
            "file_description": source.get("file_description", ""),
            "filing_url": filing_url,
        },
    )


def _evidence_id(source_type: str, record_id: str, version_marker: str) -> str:
    digest = hashlib.sha1(f"{source_type}|{record_id}|{version_marker}".encode("utf-8"))
    return digest.hexdigest()[:16]
