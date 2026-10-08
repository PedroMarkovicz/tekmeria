from datetime import UTC, datetime

from tekmerion.sources.base import RecordBundle
from tekmerion.sources.pubmed_parse import parse_efetch, study_designs

NOW = datetime(2026, 10, 7, tzinfo=UTC)

SYNTHETIC = """<?xml version="1.0"?>
<PubmedArticleSet>
<PubmedArticle>
  <MedlineCitation Status="In-Process">
    <PMID>111</PMID>
    <Article>
      <Journal>
        <JournalIssue><PubDate><MedlineDate>2019 Nov-Dec</MedlineDate></PubDate></JournalIssue>
        <Title>Test Journal</Title>
      </Journal>
      <ArticleTitle>Effect of <i>Drug</i>  on   H<sub>2</sub>O</ArticleTitle>
      <Abstract>
        <AbstractText Label="RESULTS">
          HbA<sub>1c</sub> fell by 0.5% (95% CI, 0.3&#8211;0.7).
        </AbstractText>
        <AbstractText Label="EMPTY"></AbstractText>
        <AbstractText Label="CONCLUSIONS">It <b>works</b>.</AbstractText>
      </Abstract>
      <PublicationTypeList>
        <PublicationType>Journal Article</PublicationType>
        <PublicationType>Randomized Controlled Trial</PublicationType>
      </PublicationTypeList>
      <DataBankList>
        <DataBank>
          <DataBankName>ClinicalTrials.gov</DataBankName>
          <AccessionNumberList><AccessionNumber>NCT01234567</AccessionNumber></AccessionNumberList>
        </DataBank>
      </DataBankList>
    </Article>
    <CommentsCorrectionsList>
      <CommentsCorrections RefType="ErratumIn">
        <RefSource>x</RefSource><PMID>222</PMID>
      </CommentsCorrections>
    </CommentsCorrectionsList>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList>
      <ArticleId IdType="pubmed">111</ArticleId>
      <ArticleId IdType="doi">10.1/abc</ArticleId>
    </ArticleIdList>
    <ReferenceList>
      <Reference>
        <ArticleIdList><ArticleId IdType="doi">10.9/not-this</ArticleId></ArticleIdList>
      </Reference>
    </ReferenceList>
  </PubmedData>
</PubmedArticle>
</PubmedArticleSet>"""


def _parse_one(xml: str) -> RecordBundle:
    (bundle,) = parse_efetch(xml, NOW)
    return bundle


def test_inline_markup_keeps_its_text_and_whitespace_is_normalized() -> None:
    bundle = _parse_one(SYNTHETIC)
    assert bundle.record.title == "Effect of Drug on H2O"
    texts = {p.path: p.text for p in bundle.passages}
    assert texts["abstract.1"] == "HbA1c fell by 0.5% (95% CI, 0.3\u20130.7)."
    assert texts["abstract.2"] == "It works."  # the empty section is skipped, numbering stays dense
    assert [p.section_label for p in bundle.passages] == [None, "RESULTS", "CONCLUSIONS"]


def test_not_yet_indexed_article_has_unknown_design() -> None:
    record = _parse_one(SYNTHETIC).record
    assert record.quality.study_designs.state == "unknown"
    assert record.quality.medline_indexed.value is False
    assert record.quality.publication_types.value == [
        "Journal Article",
        "Randomized Controlled Trial",
    ]


def test_ids_links_and_dates() -> None:
    record = _parse_one(SYNTHETIC).record
    assert record.ids.doi == "10.1/abc"  # not the reference's DOI
    assert [(link.rel, link.target) for link in record.links] == [
        ("erratum_in", "pmid:222"),
        ("results_of", "nct:NCT01234567"),
    ]
    assert record.published is not None
    assert (record.published.date, record.published.precision) == ("2019-11", "month")
    assert record.venue == "Test Journal"


def test_study_designs_mapping() -> None:
    assert study_designs(["Journal Article"]) == ["other"]
    assert study_designs(["Meta-Analysis", "Systematic Review", "Review"]) == [
        "meta_analysis",
        "systematic_review",
        "review",
    ]
