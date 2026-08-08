import json

from contracts import EvidenceRecord
from pipeline import process_records


def _ct_record(evidence_id: str, overall_status: str) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        source_type="clinicaltrials",
        source_name="ClinicalTrials.gov",
        source_url=f"https://clinicaltrials.gov/study/{evidence_id}",
        source_record_id=evidence_id,
        publication_date="2026-01-01",
        retrieved_at="2026-01-01T00:00:00Z",
        title="A fake ADC trial",
        evidence_text="An anti-HER2 antibody-drug conjugate is being evaluated in breast cancer patients.",
        evidence_class="CLINICAL_TRIAL_RECORD",
        provenance={"nct_id": evidence_id, "overall_status": overall_status},
    )


def _fake_llm(record):
    # Quote must be a real substring of evidence_text AND contain both
    # the target and indication text -- the full sentence satisfies both.
    claim = {"target": "HER2", "indication": "breast cancer", "supporting_quote": record.evidence_text}
    return lambda user_content: json.dumps({"evidence_id": record.evidence_id, "claims": [claim]})


def test_process_records_llm_call_passthrough_reaches_seed_extraction():
    # PR #9 review: process_records() hardcoded the real Anthropic API
    # call, so pipeline-level tests needed ANTHROPIC_API_KEY or monkeypatching
    # requests -- this verifies the injected llm_call actually reaches
    # seed_extraction.py through process_records()'s new passthrough.
    record = _ct_record("NCT001", "RECRUITING")

    seeds, events = process_records([record], llm_call=_fake_llm(record))

    assert len(seeds) == 1
    seed = next(iter(seeds.values()))
    assert seed.target == "HER2"
    # Events are empty here not because of a bug in this change, but
    # because pipeline.py still passes asset_id=None, seed_id=None
    # unconditionally (entity resolution is separately-tracked later-PR
    # work -- see the TODO comments in pipeline.py); extract_events_from_record()
    # returns [] whenever both are None, regardless of a record's content.
    assert events == []


def test_process_records_seed_failure_raises_before_returning_events():
    # Documents the atomic-failure contract stated in process_records()'s
    # docstring: even though events are computed first (and would have
    # succeeded on their own), a seed-extraction failure raises and the
    # whole call returns nothing -- not a partial (events, no seeds) result.
    import pytest

    record = _ct_record("NCT003", "COMPLETED")

    def failing_llm(user_content):
        raise RuntimeError("simulated LLM failure")

    with pytest.raises(RuntimeError, match="simulated LLM failure"):
        process_records([record], llm_call=failing_llm)
