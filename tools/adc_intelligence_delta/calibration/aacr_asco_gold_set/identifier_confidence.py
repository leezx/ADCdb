"""Classifies how much a Layer 3 lineage-confirmation identifier supports
treating a match against ADC_QUERY_TERM as genuine evidence of a seed's
later-publication discovery, independent of whether the substring match
succeeded.

Split into its own module (PR #8 review) so that summarize_layer34.py --
a lightweight aggregation script -- doesn't need to import
task57_exhaustive_layer34.py, a live-network PubMed retrieval script, just
to reach a pure string-classification function.

Known limitations (PR #8 round-2 review; not fully solved, only mitigated,
since a full fix means upstream identifier extraction returning a
structured type -- ASSET_CODE / TARGET / GENERIC_NAME -- instead of this
module inferring one from string shape alone; deferred as later-PR scope):
- HIGH is denylist-based (ASSET_CODE_STOPWORDS): a target/antigen symbol
  not yet added to that list can still be misclassified HIGH.
- MEDIUM detects "shaped like an antibody INN", not "confirmed ADC" -- a
  non-ADC monoclonal antibody name matches the same shape.
- QUERY_TRIGGER_TERMS/ANTIBODY_SUFFIXES below are maintained by hand,
  copied from (not imported from) the actual production query in
  src/sources/pubmed.py -- see test_identifier_confidence.py's drift-guard
  test, which is the current mitigation, not a structural fix.
For this module's actual current use (classifying the 8 linked seeds in
the fixed, human-curated 51-record AACR/ASCO gold set), these are bounded
risks: every CURATED_IDENTIFIERS value in task57_exhaustive_layer34.py is
already a human-verified string, so this classifier is a secondary safety
net on top of that verification, not the primary correctness mechanism.
"""

from __future__ import annotations

import re
import unicodedata

# Same suffix list as task57_exhaustive_layer34.py's ANTIBODY_SUFFIXES, plus
# maytansinoid (DM1/DM4) payload suffixes -- "ravtansine"/"soravtansine"/
# "mertansine" -- that PR #8 review found real approved/investigational ADCs
# use (e.g. mirvetuximab soravtansine, anetumab ravtansine) but that were
# missing, so those constructs fell through to None (excluded) instead of
# MEDIUM. Note these payload suffixes are NOT in QUERY_TRIGGER_TERMS below:
# ADC_QUERY_TERM itself doesn't search for them either (a real, separately
# documented gap in the production query -- see DESIGN.md's "payload
# chemistries outside the current suffix list" note), which is exactly why
# they belong in MEDIUM and not LOW.
ANTIBODY_SUFFIXES = (
    "zumab", "umab", "mab", "vedotin", "deruxtecan", "govitecan",
    "imab", "ximab", "tamab", "mafodotin", "tesirine", "emtansine",
    "ozogamicin", "tirumotecan", "ravtansine", "soravtansine", "mertansine",
)

# Same terms as src/sources/pubmed.py's ADC_QUERY_TERM, without the
# PubMed-specific [tiab] qualifier -- this is the actual production query
# vocabulary, used below to detect when an identifier overlaps it.
QUERY_TRIGGER_TERMS = (
    "antibody-drug conjugate",
    "antibody drug conjugate",
    "antibody-drug conjugates",
    "vedotin",
    "deruxtecan",
    "govitecan",
    "mafodotin",
    "tesirine",
    "emtansine",
    "ozogamicin",
    "tirumotecan",
)

# Asset-code pattern: alphanumeric codes like "OBI-992", "BCG033", "MEN1309".
# Same as task57_exhaustive_layer34.py's ASSET_CODE_PATTERN.
ASSET_CODE_PATTERN = re.compile(r"\b[A-Z]{1,6}-?\d{1,5}(?:-\d{1,5})*[A-Z0-9]*\b")

NCT_PATTERN = re.compile(r"^NCT\d+$")
CD_ANTIGEN_PATTERN = re.compile(r"^CD\d+$")

# Same exclusion list as task57_exhaustive_layer34.py's ASSET_CODE_STOPWORDS
# -- target gene symbols, assay terms, cell lines, mouse strains that are
# shaped like an asset code but are not one. Reused here so a target name
# (e.g. "HER2") can never be misclassified HIGH just because it happens to
# match the code-shape regex.
#
# PR #8 round-2 review found this list was missing common ADC target genes
# (ROR1, DLL3, GPC3, HER3, PDL1, CEACAM5, ...), which the code-shape regex
# alone can't distinguish from a real asset code -- any "few letters + a
# digit" string matches both. Expanded the target-gene section below, but
# this remains a denylist, not a structural fix: a target symbol not yet
# added here can still be misclassified HIGH. The real fix is having
# upstream identifier extraction tag *what kind* of string it found
# (asset code vs. target vs. generic name) rather than inferring it here
# from shape alone -- deferred, since for this fixed 51-seed dataset every
# CURATED_IDENTIFIERS value is already a human-verified string (see that
# table's own docstring), so this denylist is a secondary safety net, not
# the primary correctness mechanism, and the residual risk is bounded to
# future additions to that table.
ASSET_CODE_STOPWORDS = {
    "IC50", "EC50", "IC-50", "EC-50", "COVID19",
    # Target gene / antigen symbols
    "TOP1", "TOP2", "TROP2", "HER2", "HER3", "ERBB2", "ERBB3",
    "DEC205", "PTPN11", "ROR1", "DLL3", "GPC3", "PDL1", "CEACAM5",
    "CEACAM6", "FOLR1", "STEAP1", "STEAP2", "ENPP3", "CDH6",
    "SLC34A2", "SLC39A6", "CLDN18", "MUC1", "MUC16", "B7H3", "B7H4",
    "NAPI2B", "PTK7", "EPHA2",
    "A549", "HEK293", "DU145", "N87", "MCF7", "HELA", "K562", "SKBR3",
    "JIMT1", "MDA231", "MDAMB231", "NCIH", "HCC827", "H1975", "H2110",
    "SW480", "HT29", "LOVO", "COLO205", "PC3", "LNCAP", "OVCAR3",
    "SKOV3", "RAJI", "DAUDI", "NAMALWA", "H460", "CHP134",
    "C57BL", "C57BL6", "BALBC", "NSG", "NODSCID",
}

_WHITESPACE_PATTERN = re.compile(r"\s+")
# Unicode dash/minus variants that NFKC compatibility normalization does
# NOT fold to ASCII "-" (verified: NFKC folds the fullwidth hyphen "－" but
# leaves U+2212 MINUS SIGN "−" alone) -- handled separately after NFKC.
_HYPHEN_VARIANTS = re.compile("[‐‑‒–—−]")


def _normalize(identifier: str) -> str:
    # NFKC folds fullwidth/compatibility character variants (fullwidth
    # letters and digits, the fullwidth hyphen "－", ...) to their ASCII
    # equivalents in one pass -- catches a broad class of visually-similar
    # Unicode look-alikes that a hand-maintained substitution table would
    # miss one at a time. Dash variants NFKC doesn't cover (en dash, em
    # dash, minus sign, ...) are folded separately below. Whitespace
    # (including tabs/non-breaking spaces) collapses to a single ASCII
    # space, so a hand-curated identifier with stray formatting doesn't
    # silently fall through every classification rule.
    normalized = unicodedata.normalize("NFKC", identifier)
    normalized = _HYPHEN_VARIANTS.sub("-", normalized)
    normalized = _WHITESPACE_PATTERN.sub(" ", normalized)
    return normalized.strip()


def classify_identifier_confidence(identifier: str | None) -> str | None:
    """Classify a Layer 3 query identifier into a confidence tier.

    - HIGH: a proprietary company asset code (e.g. "OBI-992", "MEN1309")
      that is not also a known target/CD-antigen/cell-line/NCT-id --
      specific enough that an incidental match is implausible. This is a
      denylist-based judgment (see ASSET_CODE_STOPWORDS), not a structural
      guarantee -- a target symbol not yet added to that list could still
      be misclassified HIGH.
    - MEDIUM: the identifier has the shape of an antibody generic name
      (an -mab/-tansine-style INN suffix). This detects "looks like an
      antibody INN", NOT "is confirmed to be an ADC" -- a bare monoclonal
      antibody name with no payload at all (e.g. "faricimab", a non-ADC
      bispecific) matches the same shape and will also return MEDIUM.
      Treat MEDIUM as "plausible construct name, unconfirmed" rather than
      "verified ADC", and see the module docstring's known-limitations
      note before using this tier for anything higher-stakes than this
      module's own calibration benchmark.
    - LOW: the identifier text itself contains one of ADC_QUERY_TERM's
      trigger words (e.g. "deruxtecan", "govitecan") -- most commonly
      because it's the generic name of an already-approved ADC (whose INN
      is built from the payload name). Matching this identifier against
      ADC_QUERY_TERM is close to tautological: the query and the lineage
      identifier share vocabulary by construction, not because the query
      is good at finding it.
    - None: no identifier, or an identifier that doesn't fit any of the
      above (e.g. a company name, a target-only mention, an extraction
      artifact) -- deliberately NOT a catch-all bucket. Callers should
      exclude None from any recall benchmark rather than default it into
      MEDIUM, since an unclassifiable string is not evidence of anything.
    """
    if not identifier:
        return None
    normalized = _normalize(identifier)
    if not normalized:
        return None
    lowered = normalized.lower()

    # Checked first and independent of shape: an identifier containing a
    # query-vocabulary word is near-tautological regardless of whether it
    # also happens to look like a code (this also makes the biosimilar
    # case "trastuzumab deruxtecan-nxki" correctly LOW, since it still
    # contains "deruxtecan").
    if any(term in lowered for term in QUERY_TRIGGER_TERMS):
        return "LOW"

    stripped = normalized.replace(" ", "").upper()
    if (
        ASSET_CODE_PATTERN.fullmatch(stripped)
        and stripped not in ASSET_CODE_STOPWORDS
        and not CD_ANTIGEN_PATTERN.match(stripped)
        and not NCT_PATTERN.match(stripped)
    ):
        return "HIGH"

    # Checked per whitespace-separated token, not against the whole
    # space-stripped string: removing spaces before matching (as an
    # earlier version of this check did) can create an accidental suffix
    # match spanning a word boundary -- e.g. "random abstract" stripped to
    # "randomabstract" contains "mab" purely by coincidence of where
    # "random" ends and "abstract" begins, which classified unrelated text
    # as MEDIUM. Each token is still allowed an optional trailing
    # brand-suffix code (e.g. "soravtansine-gynx"), matching the same
    # biosimilar-suffix case the LOW check above already handles.
    antibody_pattern = r"^[a-z][a-z0-9]*(?:" + "|".join(ANTIBODY_SUFFIXES) + r")(?:-[a-z0-9]+)?$"
    if any(re.match(antibody_pattern, token) for token in lowered.split(" ") if token):
        return "MEDIUM"

    return None
