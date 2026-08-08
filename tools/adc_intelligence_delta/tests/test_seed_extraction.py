import json

import pytest

from contracts import EvidenceRecord
from seed_extraction import (
    MissingAPIKeyError,
    dedup_seeds,
    extract_seeds_from_records,
    normalize_seed_slug,
)


def _record(evidence_id: str, title: str = "", evidence_text: str = "") -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        source_type="pubmed",
        source_name="PubMed",
        source_url=f"https://pubmed.ncbi.nlm.nih.gov/{evidence_id}",
        source_record_id=evidence_id,
        publication_date="2026-01-01",
        retrieved_at="2026-01-01T00:00:00Z",
        title=title,
        evidence_text=evidence_text,
    )


def _fake_llm(response_lines: list[dict]):
    # Returns an llm_call callable that ignores the prompt and returns a
    # fixed JSON-lines response -- the whole point of dependency injection
    # here is that no test needs to construct or inspect the prompt text.
    text = "\n".join(json.dumps(line) for line in response_lines)
    return lambda prompt: text


def test_normalize_seed_slug_format():
    assert normalize_seed_slug("CDCP1", "colorectal cancer", "ADC") == "CDCP1|COLORECTAL_CANCER|ADC"


def test_extracts_one_seed_per_claim():
    records = [_record("e1", evidence_text="An anti-CDCP1 ADC showed regression in colorectal cancer PDX models.")]
    fake = _fake_llm([{"evidence_id": "e1", "claims": [{"target": "CDCP1", "indication": "colorectal cancer"}]}])

    seeds = extract_seeds_from_records(records, llm_call=fake)

    assert len(seeds) == 1
    assert seeds[0].target == "CDCP1"
    assert seeds[0].indication == "colorectal cancer"
    assert seeds[0].seed_id == "CDCP1|COLORECTAL_CANCER|ADC"
    assert seeds[0].supporting_evidence_ids == ["e1"]


def test_empty_claims_is_the_common_case_not_an_error():
    # Regression guard for the old Cartesian-product design: a record that
    # merely mentions a target and an indication without linking them must
    # NOT produce a seed. This is what most records should look like.
    records = [_record("e1", evidence_text="Trial status updated to recruiting.")]
    fake = _fake_llm([{"evidence_id": "e1", "claims": []}])

    seeds = extract_seeds_from_records(records, llm_call=fake)
    assert seeds == []


def test_does_not_invent_pairings_across_multiple_targets_and_indications():
    # The exact failure mode the old Cartesian-product code had: a record
    # discussing two targets and evaluated in one indication must yield
    # only the claim the LLM actually reports, not all combinations.
    records = [
        _record(
            "e1",
            evidence_text=(
                "Background: HER2 and TROP2 are both validated ADC targets. "
                "This study evaluates a novel anti-TROP2 ADC in gastric cancer."
            ),
        )
    ]
    fake = _fake_llm([{"evidence_id": "e1", "claims": [{"target": "TROP2", "indication": "gastric cancer"}]}])

    seeds = extract_seeds_from_records(records, llm_call=fake)

    assert len(seeds) == 1
    assert seeds[0].target == "TROP2"
    # HER2 x gastric cancer must never appear -- there was no such claim.
    assert not any(s.target == "HER2" for s in seeds)


def test_multiple_claims_in_one_record_all_extracted():
    records = [_record("e1")]
    fake = _fake_llm(
        [
            {
                "evidence_id": "e1",
                "claims": [
                    {"target": "GPC3", "indication": "hepatocellular carcinoma"},
                    {"target": "GPC3", "indication": "lung cancer"},
                ],
            }
        ]
    )

    seeds = extract_seeds_from_records(records, llm_call=fake)
    assert len(seeds) == 2
    assert {s.indication for s in seeds} == {"hepatocellular carcinoma", "lung cancer"}


def test_hallucinated_evidence_id_is_dropped():
    # If the LLM returns an evidence_id that wasn't in this batch's input,
    # the claim must not be attached to anything -- silently trusting it
    # would let a seed appear with the wrong provenance.
    records = [_record("e1")]
    fake = _fake_llm([{"evidence_id": "e999-not-in-batch", "claims": [{"target": "HER2", "indication": "breast cancer"}]}])

    seeds = extract_seeds_from_records(records, llm_call=fake)
    assert seeds == []


def test_malformed_json_line_is_skipped_not_fatal():
    def fake_llm(prompt):
        return 'not valid json\n{"evidence_id": "e1", "claims": [{"target": "MSLN", "indication": "mesothelioma"}]}'

    records = [_record("e1")]
    seeds = extract_seeds_from_records(records, llm_call=fake_llm)
    assert len(seeds) == 1
    assert seeds[0].target == "MSLN"


def test_claim_missing_target_or_indication_is_dropped():
    records = [_record("e1")]
    fake = _fake_llm(
        [{"evidence_id": "e1", "claims": [{"target": "", "indication": "breast cancer"}, {"target": "HER2", "indication": ""}]}]
    )
    seeds = extract_seeds_from_records(records, llm_call=fake)
    assert seeds == []


def test_batches_are_chunked_and_each_chunk_only_sees_its_own_records():
    calls = []

    def fake_llm(prompt):
        calls.append(prompt)
        # Echo back a claim for whichever chunk this call represents,
        # proving each call only received its own records.
        return "\n".join(
            json.dumps({"evidence_id": f"e{n}", "claims": [{"target": "HER2", "indication": "breast cancer"}]})
            for n in range(1, 4)
        ) if len(calls) == 1 else "\n".join(
            json.dumps({"evidence_id": f"e{n}", "claims": [{"target": "HER2", "indication": "breast cancer"}]})
            for n in range(4, 6)
        )

    records = [_record(f"e{n}") for n in range(1, 6)]
    seeds = extract_seeds_from_records(records, llm_call=fake_llm, batch_size=3)

    assert len(calls) == 2  # 5 records at batch_size=3 -> two chunks
    assert len(seeds) == 5


def test_dedup_seeds_merges_same_hypothesis_across_records():
    records = [_record("e1"), _record("e2")]
    fake = _fake_llm(
        [
            {"evidence_id": "e1", "claims": [{"target": "HER2", "indication": "breast cancer"}]},
        ]
    )
    # Two separate calls (one per record via batch_size=1) both claiming
    # the same hypothesis -- dedup must merge them and union evidence_ids.
    seeds = extract_seeds_from_records([records[0]], llm_call=fake)
    seeds += extract_seeds_from_records(
        [records[1]],
        llm_call=_fake_llm([{"evidence_id": "e2", "claims": [{"target": "HER2", "indication": "breast cancer"}]}]),
    )

    deduped = dedup_seeds(seeds)
    assert len(deduped) == 1
    merged = next(iter(deduped.values()))
    assert set(merged.supporting_evidence_ids) == {"e1", "e2"}


def test_missing_api_key_raises_without_network_call(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def _unexpected_post(*args, **kwargs):
        raise AssertionError("requests.post() must not be called when ANTHROPIC_API_KEY is unset")

    import seed_extraction

    monkeypatch.setattr(seed_extraction.requests, "post", _unexpected_post)

    with pytest.raises(MissingAPIKeyError):
        extract_seeds_from_records([_record("e1")])


def test_empty_records_list_makes_no_llm_call():
    calls = []
    extract_seeds_from_records([], llm_call=lambda p: calls.append(p) or "")
    assert calls == []
