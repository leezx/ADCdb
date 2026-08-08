from contracts import EvidenceRecord
from event_extraction import extract_events_from_record, infer_event_type


def _ct_record(overall_status: str) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id="fake-evidence-id",
        source_type="clinicaltrials",
        source_name="ClinicalTrials.gov",
        source_url="https://clinicaltrials.gov/study/NCT00000000",
        source_record_id="NCT00000000",
        publication_date="2026-01-01",
        retrieved_at="2026-01-01T00:00:00Z",
        title="A fake ADC trial",
        evidence_text="Study summary text that never mentions status words directly.",
        evidence_class="CLINICAL_TRIAL_RECORD",
        provenance={"nct_id": "NCT00000000", "overall_status": overall_status},
    )


def test_completed_and_terminated_map_to_distinct_event_types():
    # A prior version of infer_event_type() text-searched evidence_text and
    # merged COMPLETED and TERMINATED into the same TRIAL_COMPLETED type --
    # a trial that finished as planned and one stopped early are different
    # facts and must not collapse into one event type.
    assert infer_event_type(_ct_record("COMPLETED")) == "TRIAL_COMPLETED"
    assert infer_event_type(_ct_record("TERMINATED")) == "TRIAL_TERMINATED"


def test_ct_status_mapping_is_deterministic_on_structured_field():
    assert infer_event_type(_ct_record("RECRUITING")) == "TRIAL_RECRUITING"
    assert infer_event_type(_ct_record("NOT_YET_RECRUITING")) == "TRIAL_NOT_YET_RECRUITING"
    assert infer_event_type(_ct_record("ACTIVE_NOT_RECRUITING")) == "TRIAL_ACTIVE_NOT_RECRUITING"
    assert infer_event_type(_ct_record("WITHDRAWN")) == "TRIAL_WITHDRAWN"


def test_ct_status_ignores_evidence_text_content():
    # Regression guard: evidence_text says "recruiting" but the structured
    # status says COMPLETED -- the structured field must win, not a text
    # search, since evidence_text is a free-text summary, not the status.
    record = _ct_record("COMPLETED")
    record.evidence_text = "This trial is actively recruiting new patients."
    assert infer_event_type(record) == "TRIAL_COMPLETED"


def test_ct_unknown_status_falls_back_to_trial_other_not_untyped():
    assert infer_event_type(_ct_record("UNKNOWN")) == "TRIAL_OTHER"
    assert infer_event_type(_ct_record("")) == "TRIAL_OTHER"


def test_extract_events_from_record_attaches_ct_event_type():
    events = extract_events_from_record(_ct_record("TERMINATED"), asset_id="fake-asset")
    assert len(events) == 1
    assert events[0].event_type == "TRIAL_TERMINATED"
