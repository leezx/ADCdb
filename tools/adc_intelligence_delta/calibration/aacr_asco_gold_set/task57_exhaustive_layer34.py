#!/usr/bin/env python3
"""
Task #57: Exhaustive Layer 3/4 for all 51 PRECLINICAL_ADC_SEED records.

For each seed:
1. Extract the most specific identifier available (asset code > antibody
   generic name > target gene). Asset code / antibody name preferred over
   bare target, because a target-only query would match any paper about
   that target -- not specifically about this seed's construct. That would
   make "lineage confirmation" meaningless (everything "matches" by target).
2. Query PubMed independently (not DOI-based) for that identifier, dated
   after the seed's conference year (a "later publication").
3. Lineage confirmation: fetch each candidate PMID's title+abstract and
   verify the same identifier actually appears in it -- this is what
   distinguishes "later publication of THIS seed" from "PubMed found a
   paper that happens to share a word with the query."
4. For lineage-confirmed PMIDs, apply ADC_QUERY_TERM and compute recall.
   Any confirmed PMID that ADC_QUERY_TERM does NOT match is a genuine
   miss -- write a miss taxonomy explaining why.

Seeds without ANY extractable identifier (no asset code, no antibody name)
cannot be independently linked at all -- they are reported as
UNLINKABLE, not silently dropped and not counted as recall failures
(they were never candidates for the recall measurement in the first place).
"""

from __future__ import annotations

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

OUT_DIR = Path(__file__).parent
SEEDS_FILE = OUT_DIR / "unique_adc_seeds.jsonl"
OUT_PATH = OUT_DIR / "layer34_exhaustive_results.jsonl"

ESEARCH_ENDPOINT = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_ENDPOINT = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
SLEEP = 0.34  # NCBI rate limit (3 req/sec unauthenticated)

# Same ADC_QUERY_TERM as src/sources/pubmed.py (production query, not modified)
_QUERY_TERMS = (
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
ADC_QUERY_TERM = " OR ".join(f'"{term}"[tiab]' for term in _QUERY_TERMS)

# Antibody-name suffixes (generic drug names), same list used elsewhere in this repo
ANTIBODY_SUFFIXES = (
    "zumab", "umab", "mab", "vedotin", "deruxtecan", "govitecan",
    "imab", "ximab", "tamab", "mafodotin", "tesirine", "emtansine",
    "ozogamicin", "tirumotecan",
)

# Asset-code pattern: alphanumeric codes like "OBI-992", "BCG033", "MEN1309",
# "SG3249", "C004", "CPT113", "TORL-4-500", "RGX-019" -- letters followed by
# digits, optionally multi-segment-hyphenated, must contain at least one
# digit (to exclude bare words like "TROP" or "HER").
ASSET_CODE_PATTERN = re.compile(r"\b[A-Z]{1,6}-?\d{1,5}(?:-\d{1,5})*[A-Z0-9]*\b")

# NCT clinical-trial IDs match the asset-code shape but are never a
# construct identifier -- exclude by prefix rather than trying to
# enumerate every trial number.
NCT_PATTERN = re.compile(r"^NCT\d+$")

# Common false-positive matches to exclude from asset-code extraction:
# assay readouts, target gene/CD-antigen symbols (queried separately,
# never as the primary identifier -- see module docstring), common cancer
# cell lines, and mouse/rat strain names that show up constantly in
# preclinical abstracts but identify an experimental system or target,
# not the construct under study.
CD_ANTIGEN_PATTERN = re.compile(r"^CD\d+$")
ASSET_CODE_STOPWORDS = {
    # Assay/readout terms
    "IC50", "EC50", "IC-50", "EC-50", "COVID19",
    # Target gene symbols (not construct identifiers)
    "TOP1", "TOP2", "TROP2", "HER2", "ERBB2", "DEC205", "PTPN11",
    # Common cancer cell lines
    "A549", "HEK293", "DU145", "N87", "MCF7", "HELA", "K562", "SKBR3",
    "JIMT1", "MDA231", "MDAMB231", "NCIH", "HCC827", "H1975", "H2110",
    "SW480", "HT29", "LOVO", "COLO205", "PC3", "LNCAP", "OVCAR3",
    "SKOV3", "RAJI", "DAUDI", "NAMALWA", "H460", "CHP134",
    # Mouse/rat strain names
    "C57BL", "C57BL6", "BALBC", "NSG", "NODSCID",
}


def _filter_asset_codes(raw_codes: set[str]) -> list[str]:
    return sorted(
        c for c in raw_codes
        if c not in ASSET_CODE_STOPWORDS
        and not CD_ANTIGEN_PATTERN.match(c)
        and not NCT_PATTERN.match(c)
    )


def extract_identifiers(title: str, abstract: str) -> dict:
    """Extract candidate identifiers from seed text, ranked by specificity
    and by whether they appear in the title (titles almost always name the
    specific construct under study, e.g. 'a novel TROP2-targeting
    antibody-drug conjugate OBI-992' -- abstract bodies mention many more
    incidental terms, including approved comparator drugs and cell lines)."""
    antibody_pattern = r"\b[a-zA-Z][a-zA-Z0-9]*(?:" + "|".join(ANTIBODY_SUFFIXES) + r")\b"

    title_antibodies = sorted(set(re.findall(antibody_pattern, title or "", re.IGNORECASE)), key=len, reverse=True)
    title_codes = _filter_asset_codes(set(ASSET_CODE_PATTERN.findall(title or "")))

    full_text = f"{title or ''} {abstract or ''}"
    all_antibodies = sorted(set(re.findall(antibody_pattern, full_text, re.IGNORECASE)), key=len, reverse=True)
    all_codes = _filter_asset_codes(set(ASSET_CODE_PATTERN.findall(full_text)))

    return {
        "title_antibodies": title_antibodies,
        "title_asset_codes": title_codes,
        "antibodies": all_antibodies,
        "asset_codes": all_codes,
    }


# Manually curated (source, record_id) -> query identifier, built by
# reading all 51 seed titles/abstracts directly. Regex-based extraction
# (extract_identifiers/choose_query_identifier below) was tried first and
# kept for transparency/fallback, but it reliably confused payload codes
# (DM1/DM4), target gene symbols (PD-1, GD2, FGFR4, CCR8, TACSTD2), assay
# terms, and cell-line/mouse-strain names for the actual novel-construct
# identifier -- distinguishing "the construct this abstract is about" from
# "every alphanumeric token in a preclinical abstract" needs the kind of
# judgment call a human/LLM reader makes in seconds and a regex cannot.
# A value of None means the abstract does not name a specific novel
# construct (methodology papers, general target-biology papers without a
# tested ADC candidate, or platform-level descriptions) -- these are
# correctly UNLINKABLE, not an extraction failure.
CURATED_IDENTIFIERS: dict[tuple[str, str], str | None] = {
    ("ASCO", "e14039"): "MEN1309",
    ("AACR", "2616"): "BCG033",
    ("AACR", "3130"): "OBI-992",
    ("AACR", "3136"): None,
    ("AACR", "5760"): None,
    ("AACR", "5804"): "LCB84",
    ("AACR", "5819"): "CPT113",
    ("AACR", "7179"): "OBI-992",
    ("AACR", "LB448"): "VBC103",
    ("AACR", "3128"): "HRA00184-C004",
    ("AACR", "1084"): None,
    ("AACR", "1865"): "HDP-102",
    ("AACR", "1884"): "DXC006",
    ("AACR", "1890"): "HMA800067",
    ("AACR", "1896"): "TORL-4-500",
    ("AACR", "2360"): "ADV101",
    ("AACR", "2610"): None,
    ("AACR", "2611"): None,
    ("AACR", "2613"): None,
    ("AACR", "3122"): None,
    ("AACR", "3123"): "AMB302",
    ("AACR", "3129"): "BYON4413",
    ("AACR", "3132"): "BR113",
    ("AACR", "3139"): "BL-M11D1",
    ("AACR", "3142"): "NN3201",
    ("AACR", "3145"): "HRA00242-C004",
    ("AACR", "4695"): None,
    ("AACR", "5085"): "PL2202",
    ("AACR", "5089"): None,
    ("AACR", "5907"): "LM-317",
    ("AACR", "5908"): None,
    ("AACR", "6341"): "RGX-019",
    ("AACR", "6355"): None,
    ("AACR", "7168"): "PBI-410",
    ("AACR", "718"): None,
    ("AACR", "738"): "MBRC-101",
    ("AACR", "739"): "AMT-253",
    ("AACR", "742"): "PYX-201",
    ("AACR", "LB042"): None,
    ("AACR", "LB124"): None,
    ("AACR", "LB342"): "Hu002",
    ("AACR", "ND08"): "M3554",
    ("ASCO", "10049"): "trastuzumab deruxtecan",
    ("ASCO", "e15010"): "DM005",
    ("ASCO", "e15022"): None,
    ("AACR", "3168"): None,
    ("AACR", "3387"): None,
    ("AACR", "6343"): None,
    ("AACR", "2957"): None,
    ("AACR", "4448"): None,
    ("ASCO", "e15045"): "SynchroLINK T2X",
}


# Identifiers that are the generic INN name of an already-approved ADC
# rather than a proprietary/pre-approval asset code. Matching one of these
# against ADC_QUERY_TERM is close to tautological -- the term is often
# literally in ADC_QUERY_TERM's own vocabulary (e.g. "deruxtecan") -- so
# lineage confirmed via one of these names is weaker evidence of a genuine
# "PubMed found this seed's later publication" link than a proprietary
# code match, even though the substring match itself is just as exact.
KNOWN_APPROVED_ADC_GENERIC_NAMES = {
    "trastuzumab deruxtecan",
}


def classify_identifier_confidence(identifier: str | None) -> str | None:
    """Classify how much the identifier itself supports treating a
    lineage-confirmed match as genuine evidence of later-publication
    discovery, independent of whether the substring match succeeded.

    - HIGH: a proprietary company asset code (e.g. "OBI-992", "MEN1309") --
      specific enough that an incidental match is implausible.
    - MEDIUM: an antibody/construct generic name (an -mab/-vedotin/...
      style INN) that is not a known-already-approved drug -- more generic
      than a code, but not a term ADC_QUERY_TERM itself already searches
      for.
    - LOW: the generic name of an already-FDA-approved ADC (see
      KNOWN_APPROVED_ADC_GENERIC_NAMES) -- the query term and the lineage
      identifier overlap, so a match here is much weaker signal about
      whether ADC_QUERY_TERM can find genuinely NEW constructs.
    - None: no identifier (UNLINKABLE seed).
    """
    if not identifier:
        return None
    lowered = identifier.lower()
    if lowered in KNOWN_APPROVED_ADC_GENERIC_NAMES:
        return "LOW"
    stripped = identifier.replace(" ", "")
    if ASSET_CODE_PATTERN.fullmatch(stripped) and not CD_ANTIGEN_PATTERN.match(stripped) and not NCT_PATTERN.match(stripped):
        return "HIGH"
    return "MEDIUM"


def choose_query_identifier(identifiers: dict) -> str | None:
    """Pick the single most specific identifier to query PubMed with.

    Priority: title asset code > title antibody name > any asset code >
    any antibody name > None (unlinkable).

    Title-appearing identifiers are strongly preferred: preclinical ADC
    abstracts almost always name the specific construct under study in the
    title itself (e.g. 'a novel TROP2-targeting ADC OBI-992'), while the
    abstract body mentions many more incidental terms -- approved
    comparator drugs cited in the background, cell lines used in the
    assay, etc. Asset codes are preferred over antibody generic names at
    each tier because a generic name (-mab/-vedotin/...) in a preclinical
    abstract is very often an ALREADY-APPROVED reference drug, not the
    novel construct. Target genes are deliberately never used alone --
    see module docstring.
    """
    if identifiers["title_asset_codes"]:
        return identifiers["title_asset_codes"][0]
    if identifiers["title_antibodies"]:
        return identifiers["title_antibodies"][0]
    if identifiers["asset_codes"]:
        return identifiers["asset_codes"][0]
    if identifiers["antibodies"]:
        return identifiers["antibodies"][0]
    return None


def esearch(term: str, mindate: str, maxdate: str, retmax: int = 20, retries: int = 3) -> list[str]:
    params = {
        "db": "pubmed",
        "term": term,
        "datetype": "pdat",
        "mindate": mindate,
        "maxdate": maxdate,
        "retmax": str(retmax),
        "retmode": "json",
    }
    for attempt in range(retries):
        try:
            response = requests.get(ESEARCH_ENDPOINT, params=params, timeout=30)
            response.raise_for_status()
            result = response.json().get("esearchresult", {})
            return result.get("idlist", [])
        except requests.exceptions.RequestException:
            time.sleep(1.5 * (attempt + 1))
    return []


def efetch_titles_abstracts(pmids: list[str], retries: int = 3) -> dict[str, dict]:
    """Fetch title+abstract for a batch of PMIDs. Returns {pmid: {title, abstract}}."""
    if not pmids:
        return {}
    params = {"db": "pubmed", "id": ",".join(pmids), "rettype": "abstract", "retmode": "xml"}
    for attempt in range(retries):
        try:
            response = requests.get(EFETCH_ENDPOINT, params=params, timeout=30)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            break
        except (requests.exceptions.RequestException, ET.ParseError):
            time.sleep(1.5 * (attempt + 1))
    else:
        return {}

    out = {}
    for article_el in root.findall(".//PubmedArticle"):
        pmid_el = article_el.find(".//PMID")
        pmid = pmid_el.text if pmid_el is not None else ""
        title_el = article_el.find(".//ArticleTitle")
        title = "".join(title_el.itertext()) if title_el is not None else ""
        abstract_pieces = []
        for abstract_text_el in article_el.findall(".//Abstract/AbstractText"):
            abstract_pieces.append("".join(abstract_text_el.itertext()))
        abstract = " ".join(abstract_pieces)
        out[pmid] = {"title": title, "abstract": abstract}
    return out


def confirm_lineage(identifier: str, candidate_text: dict) -> bool:
    """Verify the identifier actually appears in the candidate's title/abstract
    (case-insensitive substring match). This is the lineage-confirmation step
    that distinguishes a genuine later-publication from a coincidental match."""
    combined = f"{candidate_text.get('title', '')} {candidate_text.get('abstract', '')}".lower()
    return identifier.lower() in combined


def query_matches_adc_term(pmid: str, retries: int = 3) -> bool:
    """Test if a PMID matches the production ADC_QUERY_TERM."""
    params = {
        "db": "pubmed",
        "term": f"({ADC_QUERY_TERM}) AND {pmid}[uid]",
        "retmode": "json",
    }
    for attempt in range(retries):
        try:
            response = requests.get(ESEARCH_ENDPOINT, params=params, timeout=30)
            response.raise_for_status()
            count = int(response.json()["esearchresult"]["count"])
            return count > 0
        except requests.exceptions.RequestException:
            time.sleep(1.5 * (attempt + 1))
    return False


def process_seed(seed: dict) -> dict:
    """Run the full Layer 3/4 pipeline for one seed."""
    key = (seed["source"], seed["record_id"])
    identifiers = extract_identifiers(seed.get("title", ""), seed.get("abstract", ""))

    # Prefer the manually curated identifier (verified by reading all 51
    # abstracts) over the regex fallback -- see CURATED_IDENTIFIERS docstring
    # for why regex alone was unreliable. Falls back to regex only for
    # records somehow missing from the curated table (should not happen for
    # this fixed 51-seed set, but keeps the script from silently erroring
    # if unique_adc_seeds.jsonl is regenerated with different records).
    if key in CURATED_IDENTIFIERS:
        query_id = CURATED_IDENTIFIERS[key]
        identifier_source = "curated"
    else:
        query_id = choose_query_identifier(identifiers)
        identifier_source = "regex_fallback"

    result = {
        "source": seed["source"],
        "year": seed["year"],
        "record_id": seed["record_id"],
        "title": seed["title"],
        "identifiers_extracted": identifiers,
        "query_identifier": query_id,
        "identifier_source": identifier_source,
        "identifier_confidence": classify_identifier_confidence(query_id),
        "status": None,
        "candidate_pmids": [],
        "lineage_confirmed_pmids": [],
        "adc_query_term_matches": [],
        "adc_query_term_misses": [],
    }

    if not query_id:
        result["status"] = "UNLINKABLE_NO_IDENTIFIER"
        return result

    seed_year = seed["year"]
    mindate = f"{seed_year + 1}/01/01"
    maxdate = "2027/12/31"

    candidates = esearch(query_id, mindate, maxdate, retmax=20)
    time.sleep(SLEEP)
    result["candidate_pmids"] = candidates

    if not candidates:
        result["status"] = "NO_CANDIDATES_FOUND"
        return result

    # Fetch title/abstract for lineage confirmation
    texts = efetch_titles_abstracts(candidates)
    time.sleep(SLEEP)

    confirmed = [pmid for pmid in candidates if pmid in texts and confirm_lineage(query_id, texts[pmid])]
    result["lineage_confirmed_pmids"] = confirmed

    if not confirmed:
        result["status"] = "CANDIDATES_FOUND_NONE_CONFIRMED"
        return result

    # Apply ADC_QUERY_TERM to confirmed candidates
    matches, misses = [], []
    for pmid in confirmed:
        if query_matches_adc_term(pmid):
            matches.append(pmid)
        else:
            misses.append(pmid)
        time.sleep(SLEEP)

    result["adc_query_term_matches"] = matches
    result["adc_query_term_misses"] = misses
    result["status"] = "LINKED_AND_TESTED"

    return result


def main() -> None:
    seeds = [json.loads(line) for line in SEEDS_FILE.open(encoding="utf-8")]
    print(f"Processing {len(seeds)} seeds for exhaustive Layer 3/4...", file=sys.stderr)

    # Resume support
    already_done = set()
    if OUT_PATH.exists():
        for line in OUT_PATH.open(encoding="utf-8"):
            r = json.loads(line)
            already_done.add((r["source"], r["record_id"]))
        print(f"Resuming: {len(already_done)} seeds already processed", file=sys.stderr)

    with OUT_PATH.open("a", encoding="utf-8") as f:
        for i, seed in enumerate(seeds):
            key = (seed["source"], seed["record_id"])
            if key in already_done:
                continue

            print(f"[{i+1}/{len(seeds)}] {seed['source']} {seed['record_id']}: {seed['title'][:60]}...", file=sys.stderr)
            result = process_seed(seed)
            print(f"  -> {result['status']} (candidates={len(result['candidate_pmids'])}, confirmed={len(result['lineage_confirmed_pmids'])})", file=sys.stderr)

            f.write(json.dumps(result, ensure_ascii=False) + "\n")
            f.flush()

    print(f"\nDone. Results written to {OUT_PATH}")


if __name__ == "__main__":
    main()
