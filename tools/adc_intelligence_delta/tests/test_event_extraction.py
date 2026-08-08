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
    assert infer_event_type(_ct_record("ENROLLING_BY_INVITATION")) == "TRIAL_ENROLLING_BY_INVITATION"
    assert infer_event_type(_ct_record("SUSPENDED")) == "TRIAL_SUSPENDED"


def test_ct_expanded_access_statuses_get_their_own_family_not_trial_star():
    # Expanded-access statuses describe drug availability outside a trial,
    # not a trial phase -- they must not collapse into the TRIAL_* types
    # or into the catch-all TRIAL_OTHER.
    assert infer_event_type(_ct_record("AVAILABLE")) == "EXPANDED_ACCESS_AVAILABLE"
    assert infer_event_type(_ct_record("NO_LONGER_AVAILABLE")) == "EXPANDED_ACCESS_NO_LONGER_AVAILABLE"
    assert infer_event_type(_ct_record("TEMPORARILY_NOT_AVAILABLE")) == "EXPANDED_ACCESS_TEMPORARILY_NOT_AVAILABLE"
    assert infer_event_type(_ct_record("APPROVED_FOR_MARKETING")) == "EXPANDED_ACCESS_APPROVED_FOR_MARKETING"
    assert infer_event_type(_ct_record("WITHHELD")) == "EXPANDED_ACCESS_WITHHELD"


def test_ct_status_ignores_evidence_text_content():
    # Regression guard: evidence_text says "recruiting" but the structured
    # status says COMPLETED -- the structured field must win, not a text
    # search, since evidence_text is a free-text summary, not the status.
    record = _ct_record("COMPLETED")
    record.evidence_text = "This trial is actively recruiting new patients."
    assert infer_event_type(record) == "TRIAL_COMPLETED"


def test_ct_unknown_is_a_real_status_not_the_trial_other_fallback():
    # UNKNOWN is itself a defined CT.gov OverallStatus enum value ("status
    # temporarily unknown pending verification"), not an absent/invalid
    # one -- it must map to its own type, not the TRIAL_OTHER bucket
    # reserved for genuinely unrecognized status strings.
    assert infer_event_type(_ct_record("UNKNOWN")) == "TRIAL_STATUS_UNKNOWN"


def test_ct_genuinely_unrecognized_status_falls_back_to_trial_other():
    assert infer_event_type(_ct_record("SOME_FUTURE_STATUS_NOT_YET_DEFINED")) == "TRIAL_OTHER"
    assert infer_event_type(_ct_record("")) == "TRIAL_OTHER"


def test_ct_status_handles_none_and_whitespace():
    # provenance.get("overall_status", "") returns None (not the default)
    # if the key is present with value None -- a naive .upper() call on
    # that would crash. Also checks that incidental whitespace around a
    # valid status doesn't cause it to miss the mapping.
    record = _ct_record("RECRUITING")
    record.provenance["overall_status"] = None
    assert infer_event_type(record) == "TRIAL_OTHER"

    record.provenance["overall_status"] = "  RECRUITING  "
    assert infer_event_type(record) == "TRIAL_RECRUITING"


def test_extract_events_from_record_attaches_ct_event_type():
    events = extract_events_from_record(_ct_record("TERMINATED"), asset_id="fake-asset")
    assert len(events) == 1
    assert events[0].event_type == "TRIAL_TERMINATED"
