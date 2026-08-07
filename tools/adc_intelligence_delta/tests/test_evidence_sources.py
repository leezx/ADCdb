from datetime import date

from contracts import EvidenceRecord
from sources import clinicaltrials, fda

SAMPLE_CT_STUDY = {
    "protocolSection": {
        "identificationModule": {
            "nctId": "NCT99999999",
            "briefTitle": "A Study of Fake-ADC in Advanced Solid Tumors",
            "officialTitle": "A Phase 1 Study of Fake-ADC-101 in Patients With Advanced Solid Tumors",
        },
        "statusModule": {
            "overallStatus": "RECRUITING",
            "lastUpdatePostDateStruct": {"date": "2026-07-15"},
            "startDateStruct": {"date": "2026-06-01"},
        },
        "designModule": {"phases": ["PHASE1"]},
        "armsInterventionsModule": {
            "interventions": [
                {"name": "Fake-ADC-101", "otherNames": ["FA-101"]},
            ]
        },
        "conditionsModule": {"conditions": ["Colorectal Cancer"]},
        "descriptionModule": {"briefSummary": "A first-in-human study of Fake-ADC-101."},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Fake Pharma Inc."}},
    }
}

SAMPLE_FDA_RECORD = {
    "application_number": "BLA999999",
    "sponsor_name": "FAKE PHARMA INC",
    "openfda": {"generic_name": ["fake-vedotin"], "brand_name": ["FAKEDRUG"]},
    "submissions": [
        {
            "submission_type": "ORIG",
            "submission_number": "1",
            "submission_status": "AP",
            "submission_status_date": "20260710",
            "review_priority": "PRIORITY",
        },
        {
            # outside the requested window, must be excluded
            "submission_type": "ORIG",
            "submission_number": "0",
            "submission_status": "AP",
            "submission_status_date": "20100101",
        },
    ],
}


def test_clinicaltrials_to_evidence_shape():
    evidence = clinicaltrials.to_evidence(SAMPLE_CT_STUDY)

    assert isinstance(evidence, EvidenceRecord)
    assert evidence.source_type == "clinicaltrials"
    assert evidence.source_record_id == "NCT99999999"
    assert evidence.source_url == "https://clinicaltrials.gov/study/NCT99999999"
    assert "Fake-ADC-101" in evidence.mentioned_assets
    assert "FA-101" in evidence.mentioned_assets
    assert evidence.mentioned_indications == ["Colorectal Cancer"]
    assert evidence.evidence_class == "CLINICAL_TRIAL_RECORD"
    assert evidence.confidence == "raw"
    assert evidence.provenance["overall_status"] == "RECRUITING"
    assert evidence.publication_date == "2026-07-15"


def test_clinicaltrials_evidence_id_is_stable_for_same_input():
    first = clinicaltrials.to_evidence(SAMPLE_CT_STUDY)
    second = clinicaltrials.to_evidence(SAMPLE_CT_STUDY)
    assert first.evidence_id == second.evidence_id


def test_fda_to_evidence_filters_by_window_and_shapes_record():
    since = date(2026, 6, 1)
    records = fda.to_evidence(SAMPLE_FDA_RECORD, since)

    assert len(records) == 1  # the 2010 submission must be excluded
    evidence = records[0]
    assert isinstance(evidence, EvidenceRecord)
    assert evidence.source_type == "fda"
    assert evidence.source_record_id == "BLA999999"
    assert evidence.publication_date == "2026-07-10"
    assert "fake-vedotin" in evidence.mentioned_assets
    assert "FAKEDRUG" in evidence.mentioned_assets
    assert evidence.evidence_class == "REGULATORY_SUBMISSION"
    assert evidence.provenance["submission_status"] == "AP"


def test_fda_to_evidence_returns_empty_list_when_nothing_in_window():
    since = date(2026, 8, 1)
    records = fda.to_evidence(SAMPLE_FDA_RECORD, since)
    assert records == []
