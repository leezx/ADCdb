import pytest

from contracts import EvidenceRecord
from sources import company_pr

SAMPLE_EDGAR_HIT = {
    "_id": "0001125345-26-000050:exhibit991_clinicalupdatem.htm",
    "_source": {
        "ciks": ["0001125345"],
        "display_names": ["FAKEGENICS INC  (FAKE)  (CIK 0001125345)"],
        "file_date": "2026-07-23",
        "form": "8-K",
        "adsh": "0001125345-26-000050",
        "file_type": "EX-99.1",
        "file_description": "EX-99.1",
        "items": ["8.01"],
    },
}

SAMPLE_EDGAR_HIT_NO_CIK = {
    "_id": "0001125345-26-000050:exhibit991_clinicalupdatem.htm",
    "_source": {
        "ciks": [],
        "display_names": ["FAKEGENICS INC"],
        "file_date": "2026-07-23",
        "form": "8-K",
        "adsh": "0001125345-26-000050",
    },
}


def test_company_pr_to_evidence_shape():
    evidence = company_pr.to_evidence(SAMPLE_EDGAR_HIT)

    assert isinstance(evidence, EvidenceRecord)
    assert evidence.source_type == "company_pr"
    assert evidence.source_record_id == "0001125345-26-000050"
    assert evidence.publication_date == "2026-07-23"
    assert evidence.evidence_class == "COMPANY_DISCLOSURE"
    assert evidence.confidence == "raw"
    assert "FAKEGENICS" in evidence.title
    assert evidence.provenance["items"] == ["8.01"]


def test_company_pr_filing_url_reconstruction():
    evidence = company_pr.to_evidence(SAMPLE_EDGAR_HIT)

    assert evidence.source_url == (
        "https://www.sec.gov/Archives/edgar/data/1125345/000112534526000050/"
        "exhibit991_clinicalupdatem.htm"
    )
    assert evidence.provenance["filing_url"] == evidence.source_url


def test_company_pr_evidence_id_is_stable_for_same_input():
    first = company_pr.to_evidence(SAMPLE_EDGAR_HIT)
    second = company_pr.to_evidence(SAMPLE_EDGAR_HIT)
    assert first.evidence_id == second.evidence_id


def test_company_pr_handles_missing_cik_gracefully():
    evidence = company_pr.to_evidence(SAMPLE_EDGAR_HIT_NO_CIK)

    # No CIK means we cannot reconstruct a filing URL -- must not crash,
    # source_url should be empty rather than a malformed link.
    assert evidence.source_url == ""
    assert evidence.provenance["cik"] is None


def test_user_agent_raises_when_unset(monkeypatch):
    # Regression guard: an earlier version defaulted to a placeholder
    # string containing an em-dash, which crashes deep inside
    # requests.get() with UnicodeEncodeError (http.client encodes header
    # values as Latin-1) -- confirmed against a live request during PR #8
    # review. Missing config must fail loudly and immediately instead.
    monkeypatch.delenv("ADCDB_EDGAR_USER_AGENT", raising=False)
    with pytest.raises(company_pr.MissingUserAgentError):
        company_pr._user_agent()


def test_user_agent_raises_when_empty_or_whitespace(monkeypatch):
    monkeypatch.setenv("ADCDB_EDGAR_USER_AGENT", "   ")
    with pytest.raises(company_pr.MissingUserAgentError):
        company_pr._user_agent()


def test_user_agent_raises_on_non_latin1_value(monkeypatch):
    monkeypatch.setenv("ADCDB_EDGAR_USER_AGENT", "ADCdb research — contact@example.org")
    with pytest.raises(company_pr.MissingUserAgentError):
        company_pr._user_agent()


def test_user_agent_returns_valid_configured_value(monkeypatch):
    monkeypatch.setenv("ADCDB_EDGAR_USER_AGENT", "ADCdb Intelligence Delta (adc@example.org)")
    assert company_pr._user_agent() == "ADCdb Intelligence Delta (adc@example.org)"
