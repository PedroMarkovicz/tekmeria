from datetime import UTC, datetime
from typing import Any

from medcite.sources.europepmc_parse import (
    parse_full_text,
    parse_result,
    record_id_for,
    split_abstract,
)

NOW = datetime(2026, 10, 7, tzinfo=UTC)

PREPRINT: dict[str, Any] = {
    "id": "PPR1297067",
    "source": "PPR",
    "doi": "10.21203/rs.3.rs-1/v1",
    "title": "A <i>new</i> vaccine trial",
    "abstractText": "<title>Abstract</title> <p>Background  We tested it.</p>"
    "<title>Results</title><p>It worked.</p>",
    "firstPublicationDate": "2026-08-12",
    "pubTypeList": {"pubType": ["Preprint"]},
    "isOpenAccess": "N",
    "bookOrReportDetails": {"publisher": "Research Square"},
    "commentCorrectionList": {
        "commentCorrection": [{"source": "MED", "id": "42759151", "type": "Preprint of"}]
    },
}

PMC_ONLY: dict[str, Any] = {
    "id": "PMC123",
    "source": "PMC",
    "pmcid": "PMC123",
    "title": "Mumps policies",
    "abstractText": "<h4>Background</h4>Mumps returned.<h4>Conclusions</h4>Vaccinate.",
    "pubYear": "2025",
    "pubTypeList": {"pubType": ["review-article"]},
    "isOpenAccess": "Y",
    "license": "cc by",
    "journalInfo": {"journal": {"title": "Epidemiologia"}},
}

JATS = """<article article-type="research-article"><front/><body>
<p>Opening paragraph.</p>
<sec sec-type="intro"><title>1. Introduction</title>
  <p>Intro <italic>one</italic>.</p><p>Intro two.</p>
</sec>
<sec><title>2. Policies</title>
  <sec><title>2.1. United States</title><p>US text.</p>
    <table-wrap><p>table cell</p></table-wrap>
  </sec>
  <sec><title>2.2. Japan</title><p>Japan text.</p></sec>
  <sec><title>2.3. United States</title><p>Again.</p></sec>
</sec>
<sec><title>3. Empty</title></sec>
</body></article>"""


def test_split_abstract_handles_both_heading_styles() -> None:
    assert split_abstract("<h4>Background</h4>A.<h4>Methods</h4>B.") == [
        ("Background", "A."),
        ("Methods", "B."),
    ]
    assert split_abstract(PREPRINT["abstractText"]) == [
        (None, "Background We tested it."),
        ("Results", "It worked."),
    ]
    assert split_abstract("Plain text only.") == [(None, "Plain text only.")]
    assert split_abstract(None) == []


def test_preprint_record() -> None:
    bundle = parse_result(PREPRINT, NOW)
    record = bundle.record
    assert record.record_id == "ppr:PPR1297067"
    assert record.kind == "preprint"
    assert record.title == "A new vaccine trial"
    assert record.quality.peer_reviewed.value is False
    assert record.quality.study_designs.state == "unknown"
    assert record.venue == "Research Square"
    assert record.published is not None and record.published.precision == "day"
    assert [(link.rel, link.target) for link in record.links] == [("published_as", "pmid:42759151")]
    assert [p.path for p in bundle.passages] == ["title", "abstract.1", "abstract.2"]


def test_pmc_only_record_and_open_access() -> None:
    record = parse_result(PMC_ONLY, NOW).record
    assert record_id_for(PMC_ONLY) == "pmcid:PMC123" == record.record_id
    assert record.quality.study_designs.value == ["review"]
    oa = record.quality.open_access_full_text.value
    assert oa is not None and (oa.available, oa.license) == (True, "cc by")
    assert record.published is not None and (record.published.date, record.published.precision) == (
        "2025",
        "year",
    )
    assert record.quality.retraction.state == "not_checked"


def test_unmatched_publication_types_are_unknown_not_other() -> None:
    result = {**PMC_ONLY, "pubTypeList": {"pubType": ["research-article"]}}
    assert parse_result(result, NOW).record.quality.study_designs.state == "unknown"


def test_record_id_prefers_pmid() -> None:
    assert record_id_for({**PMC_ONLY, "pmid": "99"}) == "pmid:99"


def test_full_text_paths_slugs_and_skipped_elements() -> None:
    drafts = parse_full_text(JATS)
    assert [(d.path, d.text) for d in drafts] == [
        ("body.p1", "Opening paragraph."),
        ("body.intro.p1", "Intro one."),
        ("body.intro.p2", "Intro two."),
        ("body.policies.united_states.p1", "US text."),
        ("body.policies.japan.p1", "Japan text."),
        ("body.policies.united_states_2.p1", "Again."),
    ]
    assert drafts[3].section_label == "2.1. United States"


def test_full_text_without_body() -> None:
    assert parse_full_text("<article><front/></article>") == []
