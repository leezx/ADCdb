import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "calibration" / "aacr_asco_gold_set"))

from identifier_confidence import classify_identifier_confidence


def test_proprietary_asset_codes_are_high_confidence():
    for code in ("MEN1309", "OBI-992", "DXC006", "NN3201", "RGX-019", "MBRC-101"):
        assert classify_identifier_confidence(code) == "HIGH", code


def test_query_vocabulary_overlap_is_low_confidence_not_medium():
    # Prior version only caught this via a 1-entry hardcoded allowlist
    # (KNOWN_APPROVED_ADC_GENERIC_NAMES = {"trastuzumab deruxtecan"}), so
    # any other approved ADC's generic name silently fell through to
    # MEDIUM and polluted the primary benchmark. Classification should be
    # driven by actual overlap with ADC_QUERY_TERM's vocabulary instead.
    assert classify_identifier_confidence("trastuzumab deruxtecan") == "LOW"
    assert classify_identifier_confidence("TRASTUZUMAB DERUXTECAN") == "LOW"
    assert classify_identifier_confidence("sacituzumab govitecan") == "LOW"
    # Biosimilar/brand suffix variant -- still contains "deruxtecan".
    assert classify_identifier_confidence("trastuzumab deruxtecan-nxki") == "LOW"


def test_unclassifiable_strings_are_excluded_not_defaulted_to_medium():
    # Regression guard: an earlier version treated MEDIUM as a catch-all
    # for anything that wasn't HIGH or LOW, so garbage text, company
    # names, and target-gene symbols all silently counted toward the
    # primary recall benchmark.
    assert classify_identifier_confidence("some random abstract fragment text") is None
    assert classify_identifier_confidence("Genentech Inc") is None
    assert classify_identifier_confidence(None) is None
    assert classify_identifier_confidence("") is None


def test_target_gene_shaped_like_a_code_is_not_high_confidence():
    # HER2 has the same alphanumeric shape as a real asset code (letters
    # then digits) but is a target, not a construct -- must not be
    # misclassified HIGH just because it matches the code regex.
    assert classify_identifier_confidence("HER2") is None
    assert classify_identifier_confidence("CD33") is None


def test_generic_antibody_name_without_query_overlap_is_medium():
    assert classify_identifier_confidence("faricimab") == "MEDIUM"


def test_whitespace_and_dash_normalization():
    assert classify_identifier_confidence(" trastuzumab deruxtecan ") == "LOW"
    assert classify_identifier_confidence("OBI–992") == "HIGH"  # en dash variant
