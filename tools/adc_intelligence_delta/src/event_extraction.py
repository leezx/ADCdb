"""ADCEvent extraction from EvidenceRecords.

Interprets source-agnostic EvidenceRecords into typed, dated events
(TRIAL_START, REGULATORY, CLINICAL_READOUT, etc.). This PR implements
basic inference rules; fine-grained event typing via LLM classification
is flagged as later-PR work.

An event attaches to an asset_id, a seed_id, or both. Multiple
EvidenceRecords can support a single event (e.g., a trial start might be
confirmed by both a ClinicalTrials.gov record AND a company press release).
"""

from __future__ import annotations

import re
from datetime import datetime
from contracts import ADCEvent, EvidenceRecord


def infer_event_type(record: EvidenceRecord) -> str:
    """Infer basic event type from source_type and evidence_class.

    Rules (v0.1, coarse):
    - clinicaltrials + UNCLASSIFIED → TRIAL_ENROLLMENT
    - fda + approval-related text → FDA_APPROVAL / FDA_DESIGNATION
    - pubmed/aacr/asco + preclinical → PRECLINICAL_READOUT
    - Others → UNTYPED (requires fine-grained LLM classification in a later PR)

    These rules are heuristic and incomplete. Full typing requires LLM
    classification on the evidence_text; this function only handles the
    lowest-hanging fruit to validate the pipeline.
    """
    source = record.source_type.lower()
    evidence = record.evidence_text.lower()
    evidence_class = record.evidence_class.lower()

    # ClinicalTrials.gov records → TRIAL events
    if source == "clinicaltrials":
        if "not yet recruiting" in evidence or "recruiting" in evidence:
            return "TRIAL_ENROLLMENT"
        if "enrolling by invitation" in evidence:
            return "TRIAL_ENROLLMENT"
        if "active, not recruiting" in evidence:
            return "TRIAL_ACTIVE"
        if "completed" in evidence or "terminated" in evidence:
            return "TRIAL_COMPLETED"
        return "TRIAL_ENROLLMENT"  # Default for CT records

    # FDA records → REGULATORY events
    if source == "fda":
        if re.search(r"approval|approved", evidence):
            return "FDA_APPROVAL"
        if re.search(r"breakthrough|priority|fast track", evidence):
            return "FDA_DESIGNATION"
        if re.search(r"label|labeling", evidence):
            return "FDA_LABEL_CHANGE"
        return "REGULATORY"

    # PubMed/AACR/ASCO records → RESEARCH events
    if source in ("pubmed", "aacr", "asco", "esmo"):
        if "preclinical" in evidence_class or "clinical" not in evidence_class:
            return "PRECLINICAL_READOUT"
        if "clinical" in evidence_class or "trial" in evidence:
            return "CLINICAL_READOUT"
        return "RESEARCH_PUBLICATION"

    # Fallback
    return "UNTYPED"


def extract_event_date(record: EvidenceRecord) -> str:
    """Extract or infer event date.

    Prefers:
    1. publication_date from the record (if set)
    2. submission_status_date from FDA provenance (source-specific)
    3. retrieved_at as a fallback (event date = when we learned about it)

    Returns an ISO date string (YYYY-MM-DD).
    """
    # Prefer source-provided publication date
    if record.publication_date:
        return record.publication_date

    # FDA-specific: submission_status_date
    if record.source_type == "fda":
        prov = record.provenance or {}
        status_date = None
        for sub in prov.get("submissions", []):
            status_date = sub.get("submission_status_date")
            if status_date:
                break
        if status_date:
            # FDA dates are in YYYYMMDD format; convert to ISO
            try:
                dt = datetime.strptime(status_date, "%Y%m%d")
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

    # Fallback: when we retrieved the record
    if record.retrieved_at:
        # Assume retrieved_at is ISO datetime; extract just the date
        return record.retrieved_at.split("T")[0] if "T" in record.retrieved_at else record.retrieved_at

    # Last resort
    return "1970-01-01"


def extract_event_id(record: EvidenceRecord, asset_id: str | None, seed_id: str | None) -> str:
    """Generate a stable event_id from record and asset/seed context.

    Format: source_record_id:asset_id:seed_id (with None rendered as "none")
    Hash to keep reasonable length.
    """
    import hashlib

    parts = [
        record.source_record_id,
        asset_id or "none",
        seed_id or "none",
    ]
    composite = ":".join(parts)
    # Use first 12 chars of SHA256 hash for brevity while maintaining stability
    return hashlib.sha256(composite.encode()).hexdigest()[:12]


def extract_events_from_record(
    record: EvidenceRecord,
    asset_id: str | None = None,
    seed_id: str | None = None,
) -> list[ADCEvent]:
    """Extract one or more events from a single EvidenceRecord.

    An event needs at least one of (asset_id, seed_id). If both are None,
    returns an empty list (no way to attach the event).

    Args:
        record: The evidence record to interpret
        asset_id: If known, the asset this evidence is about (may be None)
        seed_id: If known, the seed hypothesis this evidence supports (may be None)

    Returns:
        List of ADCEvent objects (typically 0–1 per record in this version)
    """
    if not asset_id and not seed_id:
        # Can't attach event to anything; skip
        return []

    event_type = infer_event_type(record)
    event_date = extract_event_date(record)
    event_id = extract_event_id(record, asset_id, seed_id)

    event = ADCEvent(
        event_id=event_id,
        asset_id=asset_id,
        seed_id=seed_id,
        event_type=event_type,
        event_date=event_date,
        evidence_ids=[record.evidence_id],
    )

    return [event]
