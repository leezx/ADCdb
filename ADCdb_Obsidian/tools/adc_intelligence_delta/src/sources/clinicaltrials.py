"""Fetches ClinicalTrials.gov studies whose record was created or updated
since a given date and whose text looks ADC-related, and normalizes each
into an ADC_EVENT candidate. Event-type classification happens downstream
in pipeline.py, not here.

No API key required — free, official v2 API
(https://clinicaltrials.gov/api/v2/studies).
"""

from __future__ import annotations

import time
from datetime import date
from typing import Iterator

import requests

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


def normalize(study: dict) -> dict:
    """Turn a raw CT.gov study record into a source-agnostic candidate dict.
    pipeline.py does entity resolution and event-type classification; this
    function only extracts fields."""
    protocol = study.get("protocolSection", {})
    identification = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})
    arms = protocol.get("armsInterventionsModule", {})
    conditions = protocol.get("conditionsModule", {})
    description = protocol.get("descriptionModule", {})
    sponsor = protocol.get("sponsorCollaboratorsModule", {})

    intervention_names: list[str] = []
    for intervention in arms.get("interventions", []) or []:
        if intervention.get("name"):
            intervention_names.append(intervention["name"])
        intervention_names.extend(intervention.get("otherNames", []) or [])

    return {
        "source": "clinicaltrials.gov",
        "nct_id": identification.get("nctId", ""),
        "brief_title": identification.get("briefTitle", ""),
        "official_title": identification.get("officialTitle", ""),
        "overall_status": status.get("overallStatus", ""),
        "last_update_post_date": (status.get("lastUpdatePostDateStruct", {}) or {}).get("date", ""),
        "start_date": (status.get("startDateStruct", {}) or {}).get("date", ""),
        "phases": design.get("phases", []) or [],
        "intervention_names": intervention_names,
        "conditions": conditions.get("conditions", []) or [],
        "brief_summary": description.get("briefSummary", ""),
        "lead_sponsor": (sponsor.get("leadSponsor", {}) or {}).get("name", ""),
        "url": f"https://clinicaltrials.gov/study/{identification.get('nctId', '')}",
    }
