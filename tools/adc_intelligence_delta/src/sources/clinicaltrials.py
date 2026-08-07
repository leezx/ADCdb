"""Fetches ClinicalTrials.gov studies whose record was created or updated
since a given date and whose text looks ADC-related, and normalizes each
into a source-independent EvidenceRecord (see contracts.py). This adapter
only extracts and reshapes fields — no entity resolution, no event-type
interpretation happens here.

No API key required — free, official v2 API
(https://clinicaltrials.gov/api/v2/studies).
"""

from __future__ import annotations

import hashlib
import time
from datetime import date, datetime, timezone
from typing import Iterator

import requests

from contracts import EvidenceRecord

CTG_ENDPOINT = "https://clinicaltrials.gov/api/v2/studies"

# Multi-word ADC-specific phrases and known payload-name suffixes only.
# An earlier attempt with bare "ADC"/"conjugated"/"payload" pulled ~600
# hits/45-days with heavy false-positive noise (pneumococcal "conjugate"
# vaccines, generic "payload" imaging studies, "ADC" as an unrelated
# acronym) — precision-first substitute until a classification step exists.
ADC_QUERY_TERM = (
    '"antibody-drug conjugate" OR "antibody drug conjugate" OR '
    '"antibody-drug conjugates" OR MMAE OR MMAF OR DXd OR '
    'vedotin OR deruxtecan OR govitecan OR mafodotin OR tesirine OR '
    'emtansine OR ozogamicin OR tirumotecan'
)

FIELDS = ",".join(
    [
        "protocolSection.identificationModule",
        "protocolSection.statusModule",
        "protocolSection.designModule",
        "protocolSection.armsInterventionsModule",
        "protocolSection.descriptionModule",
        "protocolSection.sponsorCollaboratorsModule",
        "protocolSection.conditionsModule",
    ]
)


def fetch_studies(since: date, page_size: int = 100, timeout: int = 30, sleep: float = 0.2) -> Iterator[dict]:
    """Yield raw CT.gov study records with LastUpdatePostDate >= since."""
    filter_advanced = f"AREA[LastUpdatePostDate]RANGE[{since.isoformat()},MAX]"
    params = {
        "format": "json",
        "query.term": ADC_QUERY_TERM,
        "filter.advanced": filter_advanced,
        "fields": FIELDS,
        "pageSize": str(page_size),
    }
    page_token = None
    while True:
        request_params = dict(params)
        if page_token:
            request_params["pageToken"] = page_token
        response = requests.get(CTG_ENDPOINT, params=request_params, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        for study in data.get("studies", []) or []:
            yield study
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(sleep)


def to_evidence(study: dict) -> EvidenceRecord:
    """Turn one raw CT.gov study record into an EvidenceRecord. This is the
    only function downstream code (entity resolution, future seed/event
    logic) should ever call — nothing else in this module produces
    source-agnostic output."""
    protocol = study.get("protocolSection", {})
    identification = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})
    arms = protocol.get("armsInterventionsModule", {})
    conditions = protocol.get("conditionsModule", {})
    description = protocol.get("descriptionModule", {})
    sponsor = protocol.get("sponsorCollaboratorsModule", {})

    nct_id = identification.get("nctId", "")
    brief_title = identification.get("briefTitle", "")
    brief_summary = description.get("briefSummary", "")

    intervention_names: list[str] = []
    for intervention in arms.get("interventions", []) or []:
        if intervention.get("name"):
            intervention_names.append(intervention["name"])
        intervention_names.extend(intervention.get("otherNames", []) or [])

    last_update = (status.get("lastUpdatePostDateStruct", {}) or {}).get("date", "")

    return EvidenceRecord(
        evidence_id=_evidence_id("clinicaltrials", nct_id, last_update),
        source_type="clinicaltrials",
        source_name="ClinicalTrials.gov",
        source_url=f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else "",
        source_record_id=nct_id,
        publication_date=last_update or None,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        title=brief_title,
        evidence_text=brief_summary,
        mentioned_assets=intervention_names,
        mentioned_targets=[],
        mentioned_indications=conditions.get("conditions", []) or [],
        evidence_class="CLINICAL_TRIAL_RECORD",
        confidence="raw",
        provenance={
            "nct_id": nct_id,
            "official_title": identification.get("officialTitle", ""),
            "overall_status": status.get("overallStatus", ""),
            "phases": design.get("phases", []) or [],
            "lead_sponsor": (sponsor.get("leadSponsor", {}) or {}).get("name", ""),
            "start_date": (status.get("startDateStruct", {}) or {}).get("date", ""),
        },
    )


def _evidence_id(source_type: str, record_id: str, version_marker: str) -> str:
    digest = hashlib.sha1(f"{source_type}|{record_id}|{version_marker}".encode("utf-8"))
    return digest.hexdigest()[:16]
