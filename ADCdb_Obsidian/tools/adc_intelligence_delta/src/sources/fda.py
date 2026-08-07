"""Captures REGULATORY events — approvals, label changes, application status
changes — via the free, no-key openFDA API
(https://api.fda.gov/drug/drugsfda.json).

openFDA's drugsfda endpoint has no dedicated "ADC" facet, so this fetches all
submissions with a status-date in the window and filters locally by a
payload/suffix keyword list (the naming convention ADC drug INNs follow:
*-vedotin, *-deruxtecan, *-govitecan, *-mafodotin, *-tesirine, *-emtansine,
*-ozogamicin, *-tecan, or the literal word "conjugate"). Coarse by design —
false positives get dropped in human review, false negatives (an ADC whose
INN doesn't follow the suffix convention yet) are the real risk.
"""

from __future__ import annotations

from datetime import date
from typing import Iterator

import requests

FDA_ENDPOINT = "https://api.fda.gov/drug/drugsfda.json"

ADC_NAME_SIGNALS = (
    "vedotin",
    "deruxtecan",
    "govitecan",
    "mafodotin",
    "tesirine",
    "emtansine",
    "ozogamicin",
    "tirumotecan",
    "conjugate",
    "-tecan",
)


def _looks_adc(text: str) -> bool:
    lowered = text.lower()
    return any(signal in lowered for signal in ADC_NAME_SIGNALS)


def fetch_submissions(since: date, limit: int = 100, timeout: int = 30) -> Iterator[dict]:
    """Yield openFDA drugsfda records with a submission_status_date >= since,
    already filtered to ones that look ADC-related by name."""
    until = date.today()
    # Real spaces here, not literal "+" — requests percent-encodes this
    # string when building the query, so embedding "+" ourselves double-
    # encodes it into "%2B" and openFDA 500s on the malformed range query.
    search = f"submissions.submission_status_date:[{since.strftime('%Y%m%d')} TO {until.strftime('%Y%m%d')}]"
    skip = 0
    while True:
        params = {"search": search, "limit": str(limit), "skip": str(skip)}
        response = requests.get(FDA_ENDPOINT, params=params, timeout=timeout)
        if response.status_code == 404:
            # openFDA returns 404 (not an error payload) once results run out
            break
        response.raise_for_status()
        data = response.json()
        results = data.get("results", []) or []
        if not results:
            break
        for record in results:
            openfda = record.get("openfda", {}) or {}
            names = " ".join(
                openfda.get("generic_name", [])
                + openfda.get("brand_name", [])
                + openfda.get("substance_name", [])
            )
            products = record.get("products", []) or []
            product_names = " ".join(p.get("brand_name", "") for p in products)
            if _looks_adc(names) or _looks_adc(product_names):
                yield record
        skip += limit
        if skip >= (data.get("meta", {}).get("results", {}) or {}).get("total", 0):
            break


def normalize(record: dict, since: date) -> list[dict]:
    """One drugsfda record can contain multiple submissions; only return the
    ones dated within the requested window, as separate REGULATORY
    candidates (a single application can have several relevant status
    changes in the same month, e.g. a supplement approval for a new
    indication)."""
    openfda = record.get("openfda", {}) or {}
    generic_names = openfda.get("generic_name", []) or []
    brand_names = openfda.get("brand_name", []) or []
    application_number = record.get("application_number", "")
    sponsor = record.get("sponsor_name", "")

    events = []
    for submission in record.get("submissions", []) or []:
        status_date_raw = submission.get("submission_status_date", "")
        if not status_date_raw or len(status_date_raw) != 8:
            continue
        status_date = f"{status_date_raw[0:4]}-{status_date_raw[4:6]}-{status_date_raw[6:8]}"
        if status_date < since.isoformat():
            continue
        events.append(
            {
                "source": "fda",
                "application_number": application_number,
                "sponsor_name": sponsor,
                "generic_names": generic_names,
                "brand_names": brand_names,
                "submission_type": submission.get("submission_type", ""),
                "submission_status": submission.get("submission_status", ""),
                "submission_status_date": status_date,
                "review_priority": submission.get("review_priority", ""),
                "url": f"https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo={application_number}"
                if application_number
                else "",
            }
        )
    return events
