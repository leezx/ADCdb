"""Rolling PubMed radar: fetches articles entered into PubMed within a date
window and normalizes ADC-related ones into EvidenceRecords.

No API key required — free NCBI E-utilities
(https://eutils.ncbi.nlm.nih.gov/entrez/eutils/). Without a key NCBI asks
for <=3 requests/second; this module sleeps between calls to stay under
that voluntarily rather than requiring a key for a monthly batch job.

Uses `datetype=edat` (Entrez date — when the record entered PubMed, not
when the journal published it) for the window filter. This mirrors the
CT.gov adapter's use of LastUpdatePostDate rather than the trial's own
start date: for a rolling delta, "when did this become visible to us" is
the right filter, not the underlying event's own date. The source design
doc (see repo history) explicitly recommends a 45-60 day window rather than
a strict 30 days, because PubMed indexing lag means a strict prior-month
cutoff silently drops articles — pass `since` accordingly when calling this
module, it does not enforce a window width itself.
"""

from __future__ import annotations

import hashlib
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from typing import Iterator

import requests

from contracts import EvidenceRecord

ESEARCH_ENDPOINT = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_ENDPOINT = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# Same precision-first lesson as clinicaltrials.py: no bare "ADC" (matches
# unrelated acronyms constantly in PubMed's much larger corpus), multi-word
# phrases and payload-name suffixes only. Every term is [tiab]-qualified
# (title/abstract, literal match) to disable PubMed's automatic term
# mapping -- verified against the live API that without it, "emtansine"
# silently expands to "maytansine"[Supplementary Concept] OR
# "maytansine"[MeSH Terms], pulling in the whole maytansinoid payload
# class rather than just emtansine. [tiab] dropped the 45-day window's
# result count from 529 to 515 and made querytranslation match intent
# exactly (empty translationset instead of a silent MeSH substitution).
_TERMS = (
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
ADC_QUERY_TERM = " OR ".join(f'"{term}"[tiab]' for term in _TERMS)

# For mentioned_assets extraction from unstructured title/abstract text —
# see to_evidence() docstring for why this is a coarse heuristic, not NER.
ASSET_CODE_SUFFIXES = (
    "vedotin",
    "deruxtecan",
    "govitecan",
    "mafodotin",
    "tesirine",
    "emtansine",
    "ozogamicin",
    "tirumotecan",
)

# The token immediately before a suffix must not be a common English
# connector — otherwise "trastuzumab and deruxtecan" extracts "and
# deruxtecan" as if "and" were part of the drug name.
_STOPWORDS = {"and", "or", "with", "plus", "the", "a", "an", "to", "in", "for", "of", "vs", "versus"}

MONTH_NAMES = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Jun": "06",
    "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}


def _esearch_ids(since: date, until: date, retmax: int, timeout: int, sleep: float) -> list[str]:
    pmids: list[str] = []
    retstart = 0
    while True:
        params = {
            "db": "pubmed",
            "term": ADC_QUERY_TERM,
            "datetype": "edat",
            "mindate": since.strftime("%Y/%m/%d"),
            "maxdate": until.strftime("%Y/%m/%d"),
            "retmax": str(retmax),
            "retstart": str(retstart),
            "retmode": "json",
        }
        response = requests.get(ESEARCH_ENDPOINT, params=params, timeout=timeout)
        response.raise_for_status()
        result = response.json().get("esearchresult", {})
        ids = result.get("idlist", []) or []
        pmids.extend(ids)
        total = int(result.get("count", "0") or "0")
        retstart += retmax
        if not ids or retstart >= total:
            break
        time.sleep(sleep)
    return pmids


def _efetch_batch(pmids: list[str], timeout: int) -> ET.Element:
    params = {"db": "pubmed", "id": ",".join(pmids), "rettype": "abstract", "retmode": "xml"}
    response = requests.get(EFETCH_ENDPOINT, params=params, timeout=timeout)
    response.raise_for_status()
    return ET.fromstring(response.content)


def _parse_article(article_el: ET.Element) -> dict:
    pmid_el = article_el.find(".//PMID")
    pmid = pmid_el.text if pmid_el is not None else ""

    title_el = article_el.find(".//ArticleTitle")
    title = "".join(title_el.itertext()) if title_el is not None else ""

    abstract_pieces = []
    for abstract_text_el in article_el.findall(".//Abstract/AbstractText"):
        label = abstract_text_el.get("Label")
        text = "".join(abstract_text_el.itertext())
        abstract_pieces.append(f"{label}: {text}" if label else text)
    abstract = " ".join(abstract_pieces)

    journal_el = article_el.find(".//Journal/Title")
    journal = journal_el.text if journal_el is not None else ""

    pub_date_el = article_el.find(".//Journal/JournalIssue/PubDate")
    year = month = day = ""
    if pub_date_el is not None:
        year_el = pub_date_el.find("Year")
        month_el = pub_date_el.find("Month")
        day_el = pub_date_el.find("Day")
        year = year_el.text if year_el is not None else ""
        month = month_el.text if month_el is not None else ""
        day = day_el.text if day_el is not None else ""

    doi_el = article_el.find(".//ELocationID[@EIdType='doi']")
    doi = doi_el.text if doi_el is not None else ""

    return {
        "pmid": pmid,
        "title": title,
        "abstract": abstract,
        "journal": journal,
        "pub_year": year,
        "pub_month": month,
        "pub_day": day,
        "doi": doi,
    }


def fetch_articles(
    since: date,
    until: date | None = None,
    retmax: int = 200,
    batch_size: int = 200,
    timeout: int = 30,
    sleep: float = 0.34,
) -> Iterator[dict]:
    """Yield raw parsed-article dicts (see _parse_article) for PubMed
    records entered between since and until (default: today)."""
    until = until or date.today()
    pmids = _esearch_ids(since, until, retmax, timeout, sleep)
    for start in range(0, len(pmids), batch_size):
        batch = pmids[start : start + batch_size]
        root = _efetch_batch(batch, timeout)
        for article_el in root.findall(".//PubmedArticle"):
            yield _parse_article(article_el)
        time.sleep(sleep)


def _publication_date(article: dict) -> str | None:
    year, month, day = article["pub_year"], article["pub_month"], article["pub_day"]
    if not year:
        return None
    month_num = MONTH_NAMES.get(month, month if month.isdigit() else "01")
    day_num = day if day.isdigit() else "01"
    try:
        return f"{int(year):04d}-{int(month_num):02d}-{int(day_num):02d}"
    except ValueError:
        return None


def _extract_asset_mentions(text: str) -> list[str]:
    """Coarse heuristic, not NER: pull out `<Word>-<suffix>` or
    `<Word> <suffix>` tokens where <suffix> is a known ADC payload-name
    suffix (the INN convention: brentuximab **vedotin**, trastuzumab
    **deruxtecan**, etc). PubMed's API has no structured drug-name field to
    read the way CT.gov's interventions or FDA's generic_name/brand_name
    do, so this is the only mention-extraction available without adding an
    LLM/NER step, which is explicitly out of scope for this PR. Expect
    low recall (informal abbreviations like 'T-DXd' won't match, and
    mid-sentence prose names may be missed) — false negatives here just
    mean fewer resolvable mentions per article, not a wrong resolution, so
    this is an acceptable v0.1 tradeoff, not a correctness risk."""
    mentions: list[str] = []
    seen: set[str] = set()
    for suffix in ASSET_CODE_SUFFIXES:
        pattern = rf"\b([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)[- ]{suffix}\b"
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            prefix = match.group(1)
            if prefix.lower() in _STOPWORDS:
                # e.g. "...trastuzumab and deruxtecan..." must not extract
                # "and deruxtecan" as if "and" were part of the drug name
                continue
            token = match.group(0)
            key = token.lower()
            if key not in seen:
                mentions.append(token)
                seen.add(key)
    return mentions


def to_evidence(article: dict) -> EvidenceRecord:
    """Turn one parsed PubMed article dict into an EvidenceRecord.
    evidence_text here genuinely is verbatim source text (the abstract) —
    unlike fda.py's synthesized description, this is the case
    EvidenceRecord.evidence_text's docstring calls out as the "real"
    verbatim option."""
    pmid = article["pmid"]
    title = article["title"]
    abstract = article["abstract"]
    combined_text = f"{title} {abstract}"

    return EvidenceRecord(
        evidence_id=_evidence_id(pmid),
        source_type="pubmed",
        source_name="PubMed",
        source_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
        source_record_id=pmid,
        publication_date=_publication_date(article),
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        title=title,
        evidence_text=abstract,
        mentioned_assets=_extract_asset_mentions(combined_text),
        mentioned_targets=[],
        mentioned_indications=[],
        evidence_class="PUBMED_ARTICLE",
        confidence="raw",
        provenance={
            "pmid": pmid,
            "journal": article["journal"],
            "doi": article["doi"],
        },
    )


def _evidence_id(pmid: str) -> str:
    digest = hashlib.sha1(f"pubmed|{pmid}".encode("utf-8"))
    return digest.hexdigest()[:16]
