from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest

from tekmeria.contract.audit import (
    Resolution,
    Resolved,
    Unavailable,
    Withdrawn,
    audit,
    check_handles,
)
from tekmeria.contract.audit_models import CitationIn, ClaimIn
from tekmeria.contract.handles import HandleCodec, Locator
from tekmeria.contract.models import (
    Passage,
    Provenance,
    Quality,
    Record,
    RecordIds,
    Retraction,
    Signal,
)

pytestmark = pytest.mark.anyio

KEY = bytes(range(32))
CODEC = HandleCodec(KEY)
FIXED_NOW = datetime(2026, 10, 7, tzinfo=UTC)
TEXT = "Among 657,461 children, MMR vaccination did not increase the risk for autism."

OK_LOC = Locator("pmid", "1", None, "abstract.1")
RETRACTED_LOC = Locator("pmid", "2", None, "abstract.1")
GONE_LOC = Locator("pmid", "3", None, "abstract.1")
DOWN_LOC = Locator("pmid", "4", None, "abstract.1")
TRIAL_LOC = Locator("nct", "NCT1", "2025-01-01", "summary.1")
REVISED_LOC = Locator("pmid", "5", None, "abstract.1")


def _record(record_id: str, *, retracted: bool = False, version: str | None = None) -> Record:
    return Record(
        record_id=record_id,
        kind="journal_article",
        ids=RecordIds(),
        title="t",
        provenance=Provenance(
            source="pubmed", endpoint="efetch", retrieved_at=FIXED_NOW, upstream_version=version
        ),
        quality=Quality(
            retraction=Signal[Retraction].known(
                Retraction(retracted=retracted, notice_doi="10.1/notice" if retracted else None),
                "pubmed",
            ),
            peer_reviewed=Signal[bool].known(True, "pubmed"),
        ),
    )


def _resolved(locator: Locator, record: Record, issued: str | None = None) -> Resolved:
    passage = Passage(
        handle=CODEC.issue(locator), record_id=record.record_id, path=locator.path, text=TEXT
    )
    return Resolved(passage=passage, record=record, issued_text=issued)


class FakeResolver:
    def __init__(self) -> None:
        self.calls: list[list[Locator]] = []

    async def resolve(self, locators: Sequence[Locator]) -> Mapping[Locator, Resolution]:
        self.calls.append(list(locators))
        table: dict[Locator, Resolution] = {
            OK_LOC: _resolved(OK_LOC, _record("pmid:1")),
            RETRACTED_LOC: _resolved(RETRACTED_LOC, _record("pmid:2", retracted=True)),
            GONE_LOC: Withdrawn("record deleted"),
            DOWN_LOC: Unavailable("timeout"),
            REVISED_LOC: _resolved(
                REVISED_LOC, _record("pmid:5"), issued="An older wording of this abstract."
            ),
            TRIAL_LOC: _resolved(
                TRIAL_LOC, _record("nct:NCT1@2025-06-01", version="2025-06-01"), issued=TEXT
            ),
        }
        return {loc: table[loc] for loc in locators if loc in table}


def forged(locator: Locator) -> str:
    handle = CODEC.issue(locator)
    last = handle[-1]
    return handle[:-1] + ("0" if last != "0" else "1")


@pytest.mark.parametrize(
    ("handle", "quote", "verdict"),
    [
        ("pmid:1-abstract", None, "malformed"),
        (forged(OK_LOC), None, "forged"),
        (CODEC.issue(DOWN_LOC), None, "unavailable"),
        (CODEC.issue(GONE_LOC), None, "withdrawn"),
        (CODEC.issue(OK_LOC), "did increase the risk", "quote_mismatch"),
        (CODEC.issue(OK_LOC), "did not increase the risk", "ok"),
        (CODEC.issue(OK_LOC), None, "ok"),
        # precedence: a forged handle with a bad quote is forged, not quote_mismatch
        (forged(OK_LOC), "did increase", "forged"),
        # precedence: an unavailable handle with a bad quote is unavailable
        (CODEC.issue(DOWN_LOC), "did increase", "unavailable"),
    ],
)
async def test_verdict_precedence(handle: str, quote: str | None, verdict: str) -> None:
    report = await audit(
        [ClaimIn(sentence="s", citations=[CitationIn(handle=handle, quote=quote)])],
        codec=CODEC,
        resolver=FakeResolver(),
        now=lambda: FIXED_NOW,
    )
    assert report.claims[0].citations[0].verdict == verdict


async def test_quote_mismatch_reports_closest_match() -> None:
    claim = ClaimIn(
        sentence="s",
        citations=[CitationIn(handle=CODEC.issue(OK_LOC), quote="did increase the risk")],
    )
    report = await audit([claim], codec=CODEC, resolver=FakeResolver(), now=lambda: FIXED_NOW)
    citation = report.claims[0].citations[0]
    assert citation.closest_match is not None and citation.similarity is not None
    assert citation.similarity < 1.0


async def test_retracted_flag_and_warn_verdict() -> None:
    claim = ClaimIn(sentence="s", citations=[CitationIn(handle=CODEC.issue(RETRACTED_LOC))])
    report = await audit([claim], codec=CODEC, resolver=FakeResolver(), now=lambda: FIXED_NOW)
    citation = report.claims[0].citations[0]
    assert citation.verdict == "ok"
    assert [(f.name, f.detail) for f in citation.flags] == [("retracted", "10.1/notice")]
    assert report.summary.retracted_cited == 1
    assert report.verdict == "warn"


@pytest.mark.parametrize(
    "citation",
    [
        CitationIn(handle=CODEC.issue(OK_LOC), quote="MMR vaccination increased"),  # misquoted
        CitationIn(handle=CODEC.issue(GONE_LOC)),  # withdrawn
    ],
)
async def test_a_sentence_cited_only_by_invalid_citations_warns(citation: CitationIn) -> None:
    # Neither a misquote nor a withdrawn record is fabrication, but neither supports the
    # sentence: the answer cannot pass (docs/CONTRACT.md section 9).
    claim = ClaimIn(sentence="s", citations=[citation])
    report = await audit([claim], codec=CODEC, resolver=FakeResolver(), now=lambda: FIXED_NOW)
    assert report.claims[0].status == "invalid_only"
    assert report.verdict == "warn"


@pytest.mark.parametrize(
    "citation",
    [
        CitationIn(handle=CODEC.issue(OK_LOC), quote="MMR vaccination increased"),  # misquoted
        CitationIn(handle=CODEC.issue(GONE_LOC)),  # withdrawn
    ],
)
async def test_an_invalid_citation_beside_a_valid_one_warns(citation: CitationIn) -> None:
    # The genuine citation makes the sentence cited, but the invalid one beside it still
    # reaches the reader: the answer cannot pass (docs/CONTRACT.md section 9).
    claim = ClaimIn(sentence="s", citations=[CitationIn(handle=CODEC.issue(OK_LOC)), citation])
    report = await audit([claim], codec=CODEC, resolver=FakeResolver(), now=lambda: FIXED_NOW)
    assert report.claims[0].status == "cited"
    assert report.verdict == "warn"


async def test_a_forged_citation_left_out_of_fail_on_still_warns() -> None:
    claim = ClaimIn(
        sentence="s",
        citations=[CitationIn(handle=CODEC.issue(OK_LOC)), CitationIn(handle=forged(OK_LOC))],
    )
    report = await audit(
        [claim], codec=CODEC, resolver=FakeResolver(), fail_on=[], now=lambda: FIXED_NOW
    )
    assert report.verdict == "warn"


async def test_version_changed_flag_and_issued_text() -> None:
    claim = ClaimIn(sentence="s", citations=[CitationIn(handle=CODEC.issue(TRIAL_LOC))])
    report = await audit([claim], codec=CODEC, resolver=FakeResolver(), now=lambda: FIXED_NOW)
    citation = report.claims[0].citations[0]
    assert [(f.name, f.detail) for f in citation.flags] == [("version_changed", "2025-06-01")]
    assert citation.checked_against == "issued"
    assert citation.resolved_version == "2025-06-01"


async def test_summary_metrics_and_fail_verdict() -> None:
    claims = [
        ClaimIn(
            sentence="a",
            citations=[CitationIn(handle=CODEC.issue(OK_LOC), quote="MMR vaccination")],
        ),
        ClaimIn(sentence="b", citations=[CitationIn(handle=forged(OK_LOC))]),
        ClaimIn(sentence="c", citations=[CitationIn(handle=CODEC.issue(DOWN_LOC))]),
        ClaimIn(sentence="d", citations=[]),
    ]
    report = await audit(claims, codec=CODEC, resolver=FakeResolver(), now=lambda: FIXED_NOW)
    s = report.summary
    assert (s.sentences, s.cited_sentences, s.uncited_sentences, s.invalid_only_sentences) == (
        4,
        1,
        1,
        2,
    )
    assert s.citations == 3
    assert s.by_verdict == {
        "malformed": 0,
        "forged": 1,
        "unavailable": 1,
        "withdrawn": 0,
        "quote_mismatch": 0,
        "ok": 1,
    }
    assert s.citation_validity == pytest.approx(1 / 2)  # ok / (citations - unavailable)
    assert s.sentence_coverage == pytest.approx(1 / 4)
    assert (s.quotes_checked, s.quotes_ok, s.quote_accuracy) == (1, 1, 1.0)
    assert s.fabricated == 1
    assert report.verdict == "fail"
    assert report.fail_on == ["forged", "malformed"]
    assert report.audited_at == FIXED_NOW


async def test_incomplete_when_only_unavailable() -> None:
    claim = ClaimIn(sentence="s", citations=[CitationIn(handle=CODEC.issue(DOWN_LOC))])
    report = await audit([claim], codec=CODEC, resolver=FakeResolver(), now=lambda: FIXED_NOW)
    assert report.verdict == "incomplete"
    assert report.summary.citation_validity is None


async def test_fail_on_can_add_uncited() -> None:
    report = await audit(
        [ClaimIn(sentence="no citation")],
        codec=CODEC,
        resolver=FakeResolver(),
        fail_on=["uncited"],
        now=lambda: FIXED_NOW,
    )
    assert report.verdict == "fail"


async def test_empty_answer_passes_with_null_ratios() -> None:
    report = await audit([], codec=CODEC, resolver=FakeResolver(), now=lambda: FIXED_NOW)
    assert report.verdict == "pass"
    assert report.summary.sentence_coverage is None


async def test_each_locator_resolved_once() -> None:
    resolver = FakeResolver()
    handle = CODEC.issue(OK_LOC)
    claims = [ClaimIn(sentence=str(i), citations=[CitationIn(handle=handle)]) for i in range(3)]
    await audit(claims, codec=CODEC, resolver=resolver, now=lambda: FIXED_NOW)
    assert resolver.calls == [[OK_LOC]]


async def test_check_handles_reports_status_per_handle() -> None:
    results = await check_handles(
        [CODEC.issue(OK_LOC), forged(OK_LOC), "nope"], codec=CODEC, resolver=FakeResolver()
    )
    assert [r.status for r in results] == ["ok", "forged", "malformed"]
    assert results[0].passage is not None and results[0].passage.text == TEXT


async def test_quote_of_the_current_text_after_a_revision_is_ok() -> None:
    claim = ClaimIn(
        sentence="s",
        citations=[CitationIn(handle=CODEC.issue(REVISED_LOC), quote="did not increase the risk")],
    )
    report = await audit([claim], codec=CODEC, resolver=FakeResolver(), now=lambda: FIXED_NOW)
    citation = report.claims[0].citations[0]
    assert citation.verdict == "ok"
    assert citation.checked_against == "current"


async def test_quote_of_the_issued_text_after_a_revision_is_ok() -> None:
    claim = ClaimIn(
        sentence="s",
        citations=[CitationIn(handle=CODEC.issue(REVISED_LOC), quote="older wording")],
    )
    report = await audit([claim], codec=CODEC, resolver=FakeResolver(), now=lambda: FIXED_NOW)
    citation = report.claims[0].citations[0]
    assert (citation.verdict, citation.checked_against) == ("ok", "issued")
