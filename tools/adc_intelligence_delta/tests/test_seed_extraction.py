import json

import pytest

from contracts import EvidenceRecord
from seed_extraction import (
    SYSTEM_PROMPT,
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

# Quotes below deliberately contain BOTH the target and indication text
# (required since the round-3 review fix: a quote must mention both, not
# just be a real substring of the record).
QUOTE_1 = "anti-CDCP1 antibody-drug conjugate showed tumor regression in colorectal cancer"
QUOTE_2 = "evaluates a novel anti-TROP2 ADC in gastric cancer"


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
    fake = lambda p: _line("e1", [_claim("CDCP1", "colorectal cancer", QUOTE_1)])

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
    fake = lambda p: _line("e1", [_claim("TROP2", "gastric cancer", QUOTE_2)])

    seeds = extract_seeds_from_records(records, llm_call=fake)

    assert len(seeds) == 1
    assert seeds[0].target == "TROP2"
    assert not any(s.target == "HER2" for s in seeds)


def test_multiple_claims_in_one_record_all_extracted():
    text = "A GPC3 ADC was tested in hepatocellular carcinoma models and separately in lung cancer models."
    records = [_record("e1", evidence_text=text)]
    # Both claims quote the full sentence (a real, contiguous substring of
    # the record) -- it happens to contain "GPC3" and both indications,
    # which is sufficient for _quote_supports_claim's requirement that
    # the quote contain both the target and that specific indication.
    fake = lambda p: _line(
        "e1",
        [
            _claim("GPC3", "hepatocellular carcinoma", text),
            _claim("GPC3", "lung cancer", text),
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


def test_duplicate_output_evidence_id_raises_incomplete_batch_error():
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


def test_duplicate_input_evidence_id_rejected_before_calling_model():
    # Round-3 review: two INPUT records sharing an evidence_id would make
    # any single output line for that ID structurally ambiguous (which
    # record does it answer for?), even though it "covers" the ID exactly
    # once. Reject this precondition violation up front, before any LLM
    # call, rather than accept an unverifiable result.
    calls = []
    records = [_record("e1", evidence_text=TEXT_1), _record("e1", evidence_text=TEXT_2)]

    with pytest.raises(ValueError):
        extract_seeds_from_records(records, llm_call=lambda p: calls.append(p) or "")

    assert calls == []  # never reached the model


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
    misattributed = _claim("TROP2", "gastric cancer", QUOTE_2)
    fake = lambda p: _line("e1", [misattributed]) + "\n" + _line("e2", [])

    seeds = extract_seeds_from_records(records, llm_call=fake)
    assert seeds == []


def test_quote_real_but_unrelated_to_claim_is_dropped():
    # Round-3 review finding: a quote can be a genuine, verbatim
    # substring of the CORRECT record and still say nothing about the
    # specific target/indication being claimed (e.g. a safety-summary
    # sentence attached to a fabricated target/indication pair). Checking
    # only "is this quote real" wasn't enough -- the quote must also
    # actually mention the target and indication.
    text = "Patient received the ADC. No new safety signals were observed during the study period."
    records = [_record("e1", evidence_text=text)]
    claim = _claim("HER2", "breast cancer", "No new safety signals were observed")
    fake = lambda p: _line("e1", [claim])

    seeds = extract_seeds_from_records(records, llm_call=fake)
    assert seeds == []


def test_quote_verification_tolerates_whitespace_and_case_differences():
    records = [_record("e1", evidence_text=TEXT_1)]
    # Same word sequence as TEXT_1's real substring, but with different
    # casing and extra whitespace -- still contains both the target and
    # indication text once normalized.
    claim = _claim(
        "CDCP1",
        "colorectal cancer",
        "AN   anti-CDCP1 ANTIBODY-DRUG CONJUGATE showed tumor regression IN colorectal cancer",
    )
    fake = lambda p: _line("e1", [claim])

    seeds = extract_seeds_from_records(records, llm_call=fake)
    assert len(seeds) == 1


def test_too_short_quote_is_rejected():
    records = [_record("e1", evidence_text=TEXT_1)]
    claim = _claim("CDCP1", "colorectal cancer", "in")  # real substring, but trivially short
    fake = lambda p: _line("e1", [claim])

    seeds = extract_seeds_from_records(records, llm_call=fake)
    assert seeds == []


# --- Structurally malformed claims: batch-fatal (round-3 review) ---


def test_claim_with_non_string_target_raises_incomplete_batch_error():
    # Round-3 review: a structurally malformed claim (wrong JSON type)
    # within an otherwise-covered record was previously just dropped,
    # silently indistinguishable from "zero claims" -- the same
    # claim-level version of the record-level silent-recall-loss problem.
    records = [_record("e1", evidence_text=TEXT_1)]
    line = f'{{"evidence_id": "e1", "claims": [{{"target": 42, "indication": "colorectal cancer", "supporting_quote": "{QUOTE_1}"}}]}}'
    fake = lambda p: line

    with pytest.raises(IncompleteBatchError):
        extract_seeds_from_records(records, llm_call=fake)


def test_claim_entry_not_a_dict_raises_incomplete_batch_error():
    records = [_record("e1", evidence_text=TEXT_1)]
    line = '{"evidence_id": "e1", "claims": ["oops"]}'
    fake = lambda p: line

    with pytest.raises(IncompleteBatchError):
        extract_seeds_from_records(records, llm_call=fake)


def test_claim_missing_required_field_raises_incomplete_batch_error():
    records = [_record("e1", evidence_text=TEXT_1)]
    line = '{"evidence_id": "e1", "claims": [{"target": "CDCP1", "indication": "colorectal cancer"}]}'  # no supporting_quote
    fake = lambda p: line

    with pytest.raises(IncompleteBatchError):
        extract_seeds_from_records(records, llm_call=fake)


@pytest.mark.parametrize("claims_json", ["null", '"oops"'])
def test_claims_field_itself_malformed_shape_raises_incomplete_batch_error(claims_json):
    # Round-4 review: the whole "claims" field not being a list at all
    # (None, a string, a dict, missing entirely) was previously silently
    # treated as "zero claims" -- the same silent-recall-loss pattern
    # already fixed for individual malformed claim entries, just one
    # level up. {"evidence_id": "x", "claims": null} is not evidence the
    # model found zero claims; it's evidence something is wrong with
    # this line, and must fail the batch like every other integrity
    # violation here.
    records = [_record("e1", evidence_text=TEXT_1)]
    line = f'{{"evidence_id": "e1", "claims": {claims_json}}}'
    fake = lambda p: line

    with pytest.raises(IncompleteBatchError):
        extract_seeds_from_records(records, llm_call=fake)


def test_missing_claims_key_entirely_raises_incomplete_batch_error():
    records = [_record("e1", evidence_text=TEXT_1)]
    fake = lambda p: json.dumps({"evidence_id": "e1"})  # no "claims" key at all

    with pytest.raises(IncompleteBatchError):
        extract_seeds_from_records(records, llm_call=fake)


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


def test_claim_normalizing_to_empty_slug_is_dropped():
    records = [_record("e1", evidence_text=TEXT_1)]
    # Well-formed strings, so not batch-fatal -- just not usable as a
    # slug component once cleaned, so this is a content-quality drop.
    claim = _claim("---", "...", QUOTE_1)
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
    extract_seeds_from_records(records, llm_call=fake_llm, batch_size=3)

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

    seeds = extract_seeds_from_records([r1], llm_call=lambda p: _line("e1", [_claim("CDCP1", "colorectal cancer", QUOTE_1)]))
    seeds += extract_seeds_from_records([r2], llm_call=lambda p: _line("e2", [_claim("CDCP1", "colorectal cancer", QUOTE_1)]))

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


# --- SYSTEM_PROMPT sanity ---


def test_system_prompt_has_no_leftover_format_escaping():
    # Round-4 review: SYSTEM_PROMPT is sent to the API verbatim, never
    # through .format() -- an earlier version still had {{ }} escaping
    # left over from before the system/user message split, when the
    # whole prompt (including this JSON example) was one .format()-ed
    # template. That sent literally malformed "{{...}}" text to the
    # model. No test caught it: the injectable llm_call fake only ever
    # receives user_content, never SYSTEM_PROMPT -- this test reads the
    # actual constant instead.
    assert "{{" not in SYSTEM_PROMPT
    assert "}}" not in SYSTEM_PROMPT
