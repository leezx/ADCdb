import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "calibration" / "aacr_asco_gold_set"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from identifier_confidence import QUERY_TRIGGER_TERMS, classify_identifier_confidence
from sources import pubmed


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


def test_common_adc_target_genes_are_not_misclassified_high():
    # PR #8 round-2 review: the target-gene denylist only covered targets
    # already referenced elsewhere in the codebase (HER2, TROP2, ...) --
    # these common ADC targets weren't listed and would have matched the
    # asset-code shape regex and returned HIGH.
    for target in ("ROR1", "DLL3", "GPC3", "HER3", "PDL1", "CEACAM5"):
        assert classify_identifier_confidence(target) is None, target


def test_generic_antibody_name_without_query_overlap_is_medium():
    # faricimab (Vabysmo) is a real drug but NOT an ADC -- it's included
    # here specifically to document the known limitation that MEDIUM
    # detects "shaped like an antibody INN", not "confirmed to be an ADC"
    # (see classify_identifier_confidence's docstring). This assertion is
    # not claiming faricimab is correct output, only that it's the
    # current, understood, and accepted behavior.
    assert classify_identifier_confidence("faricimab") == "MEDIUM"


def test_maytansinoid_payload_adcs_are_medium_not_excluded():
    # PR #8 round-2 review: real ADCs using maytansinoid (DM1/DM4)
    # payloads weren't recognized by either the LOW check (their INN
    # doesn't overlap ADC_QUERY_TERM's vocabulary -- a real, separately
    # documented gap in the production query itself) or the old MEDIUM
    # check (ANTIBODY_SUFFIXES didn't include these payload suffixes) --
    # they were incorrectly excluded (None) instead of landing MEDIUM.
    assert classify_identifier_confidence("mirvetuximab soravtansine") == "MEDIUM"
    assert classify_identifier_confidence("anetumab ravtansine") == "MEDIUM"
    # Biosimilar/brand-suffix variant, same reasoning as the LOW check's
    # "trastuzumab deruxtecan-nxki" case.
    assert classify_identifier_confidence("mirvetuximab soravtansine-gynx") == "MEDIUM"


def test_medium_check_does_not_match_across_word_boundaries():
    # Regression guard for a bug introduced and caught within PR #8's own
    # round-2 fix: stripping spaces before matching created accidental
    # cross-word suffix matches, e.g. "random abstract" -> "randomabstract"
    # contains "mab" purely by coincidence of where the two words join.
    assert classify_identifier_confidence("some random abstract fragment text") is None
    assert classify_identifier_confidence("random abstract") is None


def test_whitespace_and_dash_normalization():
    assert classify_identifier_confidence(" trastuzumab deruxtecan ") == "LOW"
    assert classify_identifier_confidence("OBI–992") == "HIGH"  # en dash variant
    assert classify_identifier_confidence("OBI−992") == "HIGH"  # minus sign (not folded by NFKC)
    assert classify_identifier_confidence("ＯＢＩ－９９２") == "HIGH"  # fullwidth (folded by NFKC)


def test_query_trigger_terms_do_not_silently_drift_from_production_query():
    # identifier_confidence.py hand-copies the production ADC_QUERY_TERM
    # vocabulary (see module docstring's known-limitations note) rather
    # than importing it, so a future change to pubmed.py's _TERMS would
    # not automatically propagate here. This test is the current
    # mitigation for that gap -- it fails loudly on drift instead of
    # letting the two definitions silently diverge.
    assert set(QUERY_TRIGGER_TERMS) == set(pubmed._TERMS)
