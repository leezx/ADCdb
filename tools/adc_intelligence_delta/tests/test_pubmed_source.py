import xml.etree.ElementTree as ET

from contracts import EvidenceRecord
from sources import pubmed

SAMPLE_ARTICLE_XML = """
<PubmedArticleSet>
<PubmedArticle>
  <MedlineCitation>
    <PMID Version="1">42565327</PMID>
    <Article>
      <Journal>
        <Title>International Journal of Fake Oncology</Title>
        <JournalIssue>
          <PubDate><Year>2026</Year><Month>Aug</Month><Day>07</Day></PubDate>
        </JournalIssue>
      </Journal>
      <ArticleTitle>Fake-Drug Vedotin shows activity in a colorectal cancer model.</ArticleTitle>
      <ELocationID EIdType="doi">10.1000/fake.doi</ELocationID>
      <Abstract>
        <AbstractText Label="BACKGROUND">FakeTarget is expressed in colorectal cancer.</AbstractText>
        <AbstractText Label="RESULTS">Fake-Drug Vedotin demonstrated internalization and PDX regression.</AbstractText>
      </Abstract>
    </Article>
  </MedlineCitation>
</PubmedArticle>
</PubmedArticleSet>
"""


def _sample_article_dict() -> dict:
    root = ET.fromstring(SAMPLE_ARTICLE_XML)
    article_el = root.find(".//PubmedArticle")
    return pubmed._parse_article(article_el)


def test_parse_article_extracts_title_abstract_and_date():
    article = _sample_article_dict()

    assert article["pmid"] == "42565327"
    assert "Fake-Drug Vedotin" in article["title"]
    assert "BACKGROUND: FakeTarget" in article["abstract"]
    assert "RESULTS: Fake-Drug Vedotin" in article["abstract"]
    assert article["pub_year"] == "2026"
    assert article["pub_month"] == "Aug"
    assert article["doi"] == "10.1000/fake.doi"


def test_publication_date_converts_month_name_to_iso():
    article = _sample_article_dict()
    assert pubmed._publication_date(article) == "2026-08-07"


def test_publication_date_returns_none_without_year():
    assert pubmed._publication_date({"pub_year": "", "pub_month": "", "pub_day": ""}) is None


def test_extract_asset_mentions_matches_known_suffix():
    mentions = pubmed._extract_asset_mentions("We tested Fake-Drug Vedotin in vitro.")
    assert mentions == ["Fake-Drug Vedotin"]


def test_extract_asset_mentions_excludes_english_connector_words():
    """Regression test for a real false-positive found against the live
    corpus: 'trastuzumab and deruxtecan' extracted 'and deruxtecan' as if
    'and' were part of the drug name."""
    mentions = pubmed._extract_asset_mentions("Combined trastuzumab and deruxtecan therapy.")
    assert mentions == []


def test_extract_asset_mentions_empty_when_no_suffix_present():
    assert pubmed._extract_asset_mentions("No relevant drug names here.") == []


def test_to_evidence_shape_and_verbatim_abstract():
    article = _sample_article_dict()
    evidence = pubmed.to_evidence(article)

    assert isinstance(evidence, EvidenceRecord)
    assert evidence.source_type == "pubmed"
    assert evidence.source_record_id == "42565327"
    assert evidence.source_url == "https://pubmed.ncbi.nlm.nih.gov/42565327/"
    assert evidence.publication_date == "2026-08-07"
    # evidence_text must be the real abstract text here, not a synthesized
    # description (contrast with fda.py) -- this is the "verbatim" case.
    assert evidence.evidence_text == article["abstract"]
    assert "Fake-Drug Vedotin" in evidence.mentioned_assets
    assert evidence.evidence_class == "PUBMED_ARTICLE"
    assert evidence.provenance["doi"] == "10.1000/fake.doi"


def test_to_evidence_id_is_stable_for_same_pmid():
    article = _sample_article_dict()
    first = pubmed.to_evidence(article)
    second = pubmed.to_evidence(article)
    assert first.evidence_id == second.evidence_id
