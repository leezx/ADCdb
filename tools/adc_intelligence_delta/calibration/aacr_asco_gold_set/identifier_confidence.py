"""Classifies how much a Layer 3 lineage-confirmation identifier supports
treating a match against ADC_QUERY_TERM as genuine evidence of a seed's
later-publication discovery, independent of whether the substring match
succeeded.

Split into its own module (PR #8 review) so that summarize_layer34.py --
a lightweight aggregation script -- doesn't need to import
task57_exhaustive_layer34.py, a live-network PubMed retrieval script, just
to reach a pure string-classification function.
"""

from __future__ import annotations

import re

# Same suffix list as task57_exhaustive_layer34.py's ANTIBODY_SUFFIXES.
ANTIBODY_SUFFIXES = (
    "zumab", "umab", "mab", "vedotin", "deruxtecan", "govitecan",
    "imab", "ximab", "tamab", "mafodotin", "tesirine", "emtansine",
    "ozogamicin", "tirumotecan",
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
ASSET_CODE_STOPWORDS = {
    "IC50", "EC50", "IC-50", "EC-50", "COVID19",
    "TOP1", "TOP2", "TROP2", "HER2", "ERBB2", "DEC205", "PTPN11",
    "A549", "HEK293", "DU145", "N87", "MCF7", "HELA", "K562", "SKBR3",
    "JIMT1", "MDA231", "MDAMB231", "NCIH", "HCC827", "H1975", "H2110",
    "SW480", "HT29", "LOVO", "COLO205", "PC3", "LNCAP", "OVCAR3",
    "SKOV3", "RAJI", "DAUDI", "NAMALWA", "H460", "CHP134",
    "C57BL", "C57BL6", "BALBC", "NSG", "NODSCID",
}

_WHITESPACE_PATTERN = re.compile(r"\s+")
_HYPHEN_VARIANTS = re.compile("[‐‑‒–—]")


def _normalize(identifier: str) -> str:
    # Collapses all whitespace (including tabs/non-breaking spaces) to a
    # single ASCII space and folds Unicode dash variants (en dash, em
    # dash, ...) to ASCII "-", so a hand-curated identifier with stray
    # formatting doesn't silently fall through every classification rule.
    normalized = _HYPHEN_VARIANTS.sub("-", identifier)
    normalized = _WHITESPACE_PATTERN.sub(" ", normalized)
    return normalized.strip()


def classify_identifier_confidence(identifier: str | None) -> str | None:
    """Classify a Layer 3 query identifier into a confidence tier.

    - HIGH: a proprietary company asset code (e.g. "OBI-992", "MEN1309")
      that is not also a known target/CD-antigen/cell-line/NCT-id --
      specific enough that an incidental match is implausible.
    - MEDIUM: an antibody/construct generic name (an -mab-style INN) that
      does not overlap ADC_QUERY_TERM's own vocabulary.
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

    antibody_pattern = r"^[a-z][a-z0-9]*(?:" + "|".join(ANTIBODY_SUFFIXES) + r")$"
    if re.match(antibody_pattern, lowered.replace(" ", "")):
        return "MEDIUM"

    return None
