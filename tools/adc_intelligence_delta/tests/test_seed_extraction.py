import json

import pytest

from contracts import EvidenceRecord
from seed_extraction import (
    IncompleteBatchError,
    LLMResponseError,
    MissingAPIKeyError,
    _extract_text_from_content_blocks,
    dedup_seeds,
    extract_seeds_from_records,
    normalize_seed_slug,
)

TEXT_1 = "An anti-CDCP1 antibody-drug conjugate showed tumor regression in colorectal cancer PDX models."
TEXT_2 = "Background: HER2 and TROP2 are both validated ADC targets. This study evaluates a novel anti-TROP2 ADC in gastric cancer."


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


def _line(evidence_id: str, claims: list[dict]) -> str:
    return json.dumps({"evidence_id": evidence_id, "claims": claims})


def _claim(target: str, indication: str, quote: str) -> dict:
    return {"target": target, "indication": indication, "supporting_quote": quote}


def test_normalize_seed_slug_format():
    assert normalize_seed_slug("CDCP1", "colorectal cancer", "ADC") == "CDCP1|COLORECTAL_CANCER|ADC"


def test_extracts_one_seed_per_verified_claim():
    records = [_record("e1", evidence_text=TEXT_1)]
    claim = _claim("CDCP1", "colorectal cancer", "showed tumor regression in colorectal cancer PDX models")
    fake = lambda p: _line("e1", [claim])

    seeds = extract_seeds_from_records(records, llm_call=fake)

    assert len(seeds) == 1
    assert seeds[0].target == "CDCP1"
    assert seeds[0].indication == "colorectal cancer"
    assert seeds[0].seed_id == "CDCP1|COLORECTAL_CANCER|ADC"
    assert seeds[0].supporting_evidence_ids == ["e1"]


def test_empty_claims_is_the_common_case_not_an_error():
    records = [_record("e1", evidence_text="Trial status updated to recruiting.")]
    fake = lambda p: _line("e1", [])

    seeds = extract_seeds_from_records(records, llm_call=fake)
    assert seeds == []


def test_does_not_invent_pairings_across_multiple_targets_and_indications():
    records = [_record("e1", evidence_text=TEXT_2)]
    claim = _claim("TROP2", "gastric cancer", "evaluates a novel anti-TROP2 ADC in gastric cancer")
    fake = lambda p: _line("e1", [claim])

    seeds = extract_seeds_from_records(records, llm_call=fake)

    assert len(seeds) == 1
    assert seeds[0].target == "TROP2"
    assert not any(s.target == "HER2" for s in seeds)


def test_multiple_claims_in_one_record_all_extracted():
    text = "A GPC3 ADC was tested in hepatocellular carcinoma models and separately in lung cancer models."
    records = [_record("e1", evidence_text=text)]
    fake = lambda p: _line(
        "e1",
        [
            _claim("GPC3", "hepatocellular carcinoma", "tested in hepatocellular carcinoma models"),
            _claim("GPC3", "lung cancer", "separately in lung cancer models"),
        ],
    )

    seeds = extract_seeds_from_records(records, llm_call=fake)
    assert len(seeds) == 2
    assert {s.indication for s in seeds} == {"hepatocellular carcinoma", "lung cancer"}


# --- Batch integrity: missing/duplicate/unexpected evidence_id ---


def test_missing_evidence_id_raises_incomplete_batch_error():
    # Regression guard: PR #9 review found that a truncated/incomplete
    # response is otherwise indistinguishable from "this record has zero
    # claims" -- silently under-counting seeds. Missing coverage must
    # fail loudly instead.
    records = [_record("e1", evidence_text=TEXT_1), _record("e2", evidence_text=TEXT_1)]
    fake = lambda p: _line("e1", [])  # e2 never appears

    with pytest.raises(IncompleteBatchError):
        extract_seeds_from_records(records, llm_call=fake)


def test_duplicate_evidence_id_raises_incomplete_batch_error():
    records = [_record("e1", evidence_text=TEXT_1)]
    fake = lambda p: _line("e1", []) + "\n" + _line("e1", [])

    with pytest.raises(IncompleteBatchError):
        extract_seeds_from_records(records, llm_call=fake)


def test_hallucinated_evidence_id_raises_incomplete_batch_error():
    # A prior version silently dropped an out-of-batch evidence_id and
    # continued as if nothing were wrong. An ID the model invented that
    # wasn't in the input is itself a sign something went wrong with this
    # batch (truncation, corruption, confusion) -- treat it the same as
    # missing/duplicate coverage: fail the batch, don't silently drop it.
    records = [_record("e1", evidence_text=TEXT_1)]
    fake = lambda p: _line("e1", []) + "\n" + _line("e999-not-in-batch", [])

    with pytest.raises(IncompleteBatchError):
        extract_seeds_from_records(records, llm_call=fake)


def test_complete_coverage_with_extra_malformed_lines_still_succeeds():
    records = [_record("e1", evidence_text=TEXT_1), _record("e2", evidence_text="unrelated text")]
    fake = lambda p: "not valid json\n" + _line("e1", []) + "\n" + _line("e2", [])

    seeds = extract_seeds_from_records(records, llm_call=fake)
    assert seeds == []  # no crash, no IncompleteBatchError -- coverage is complete


# --- supporting_quote verification ---


def test_claim_with_unverifiable_quote_is_dropped_not_the_batch():
    records = [_record("e1", evidence_text=TEXT_1)]
    claim = _claim("CDCP1", "colorectal cancer", "this exact phrase does not appear anywhere in the text")
    fake = lambda p: _line("e1", [claim])

    seeds = extract_seeds_from_records(records, llm_call=fake)
    assert seeds == []  # claim dropped, but batch coverage was complete so no error


def test_claim_quote_hallucinated_from_a_different_record_is_dropped():
    # The concrete misattribution scenario PR #9 review raised: a claim
    # attached to a valid evidence_id, but whose quote actually belongs
    # to a different record in the same batch.
    records = [
        _record("e1", evidence_text=TEXT_1),
        _record("e2", evidence_text=TEXT_2),
    ]
    # Claim on e1 using a quote that only exists in e2's text.
    misattributed = _claim("TROP2", "gastric cancer", "evaluates a novel anti-TROP2 ADC in gastric cancer")
    fake = lambda p: _line("e1", [misattributed]) + "\n" + _line("e2", [])

    seeds = extract_seeds_from_records(records, llm_call=fake)
    assert seeds == []


def test_quote_verification_tolerates_whitespace_and_case_differences():
    records = [_record("e1", evidence_text=TEXT_1)]
    # Quote has different casing and collapsed whitespace vs. the source.
    claim = _claim("CDCP1", "colorectal cancer", "TUMOR REGRESSION   in colorectal cancer PDX models")
    fake = lambda p: _line("e1", [claim])

    seeds = extract_seeds_from_records(records, llm_call=fake)
    assert len(seeds) == 1


def test_too_short_quote_is_rejected():
    records = [_record("e1", evidence_text=TEXT_1)]
    claim = _claim("CDCP1", "colorectal cancer", "in")  # real substring, but trivially short
    fake = lambda p: _line("e1", [claim])

    seeds = extract_seeds_from_records(records, llm_call=fake)
    assert seeds == []


# --- Malformed LLM output shapes (still tolerated per-line/per-claim) ---


@pytest.mark.parametrize(
    "claims_json",
    [
        "null",
        '"oops"',
        '["oops"]',
    ],
)
def test_malformed_claims_field_shapes_do_not_crash(claims_json):
    records = [_record("e1", evidence_text=TEXT_1)]
    line = f'{{"evidence_id": "e1", "claims": {claims_json}}}'
    fake = lambda p: line

    seeds = extract_seeds_from_records(records, llm_call=fake)
    assert seeds == []


def test_top_level_non_dict_json_line_is_treated_as_missing_not_a_crash():
    # A bare JSON list/bool/number as the whole line isn't a Python
    # AttributeError crash (the earlier malformed-shape bug this class of
    # test originally guarded), but it also can't supply an evidence_id,
    # so it correctly surfaces as missing batch coverage rather than
    # silently vanishing.
    records = [_record("e1", evidence_text=TEXT_1)]
    fake = lambda p: "[1,2,3]"

    with pytest.raises(IncompleteBatchError):
        extract_seeds_from_records(records, llm_call=fake)


def test_claim_target_as_non_string_is_dropped():
    records = [_record("e1", evidence_text=TEXT_1)]
    line = '{"evidence_id": "e1", "claims": [{"target": 42, "indication": "colorectal cancer", "supporting_quote": "tumor regression in colorectal cancer PDX models"}]}'
    fake = lambda p: line

    seeds = extract_seeds_from_records(records, llm_call=fake)
    assert seeds == []


def test_claim_normalizing_to_empty_slug_is_dropped():
    records = [_record("e1", evidence_text=TEXT_1)]
    claim = _claim("---", "...", "tumor regression in colorectal cancer PDX models")
    fake = lambda p: _line("e1", [claim])

    seeds = extract_seeds_from_records(records, llm_call=fake)
    assert seeds == []


# --- Batching ---


def test_batches_are_chunked_and_each_chunk_only_sees_its_own_records():
    calls = []

    def fake_llm(user_content):
        calls.append(user_content)
        if len(calls) == 1:
            return "\n".join(_line(f"e{n}", []) for n in range(1, 4))
        return "\n".join(_line(f"e{n}", []) for n in range(4, 6))

    records = [_record(f"e{n}") for n in range(1, 6)]
    seeds = extract_seeds_from_records(records, llm_call=fake_llm, batch_size=3)

    assert len(calls) == 2  # 5 records at batch_size=3 -> two chunks


def test_batch_size_zero_raises_value_error():
    with pytest.raises(ValueError):
        extract_seeds_from_records([_record("e1")], llm_call=lambda p: "", batch_size=0)


def test_batch_size_negative_raises_value_error():
    # A prior design would have silently returned [] with no LLM call and
    # no error for a negative batch_size -- a silent false negative.
    with pytest.raises(ValueError):
        extract_seeds_from_records([_record("e1")], llm_call=lambda p: "", batch_size=-5)


def test_empty_records_list_makes_no_llm_call():
    calls = []
    extract_seeds_from_records([], llm_call=lambda p: calls.append(p) or "")
    assert calls == []


# --- dedup ---


def test_dedup_seeds_merges_same_hypothesis_across_records():
    r1 = _record("e1", evidence_text=TEXT_1)
    r2 = _record("e2", evidence_text=TEXT_1)
    claim = _claim("CDCP1", "colorectal cancer", "tumor regression in colorectal cancer PDX models")

    seeds = extract_seeds_from_records([r1], llm_call=lambda p: _line("e1", [claim]))
    seeds += extract_seeds_from_records([r2], llm_call=lambda p: _line("e2", [claim]))

    deduped = dedup_seeds(seeds)
    assert len(deduped) == 1
    merged = next(iter(deduped.values()))
    assert set(merged.supporting_evidence_ids) == {"e1", "e2"}


# --- API key / network guard ---


def test_missing_api_key_raises_without_network_call(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def _unexpected_post(*args, **kwargs):
        raise AssertionError("requests.post() must not be called when ANTHROPIC_API_KEY is unset")

    import seed_extraction

    monkeypatch.setattr(seed_extraction.requests, "post", _unexpected_post)

    with pytest.raises(MissingAPIKeyError):
        extract_seeds_from_records([_record("e1")])


# --- _extract_text_from_content_blocks: the Opus 5 thinking-block fix ---


def test_extract_text_finds_text_block_after_leading_thinking_block():
    # The confirmed production bug: Claude Opus 5 has thinking on by
    # default, so content[0] is a thinking block, not text. Verified
    # against https://platform.claude.com/docs/en/build-with-claude/thinking
    # ("On Claude Opus 5 ... thinking is already on: no configuration
    # needed") before fixing this.
    content = [
        {"type": "thinking", "thinking": ""},
        {"type": "text", "text": '{"evidence_id": "e1", "claims": []}'},
    ]
    assert _extract_text_from_content_blocks(content) == '{"evidence_id": "e1", "claims": []}'


def test_extract_text_concatenates_multiple_text_blocks():
    content = [
        {"type": "thinking", "thinking": ""},
        {"type": "text", "text": "part one "},
        {"type": "text", "text": "part two"},
    ]
    assert _extract_text_from_content_blocks(content) == "part one part two"


def test_extract_text_raises_when_no_text_block_present():
    content = [{"type": "thinking", "thinking": ""}]
    with pytest.raises(LLMResponseError):
        _extract_text_from_content_blocks(content)


def test_extract_text_raises_on_non_dict_blocks():
    content = ["not a block"]
    with pytest.raises(LLMResponseError):
        _extract_text_from_content_blocks(content)
