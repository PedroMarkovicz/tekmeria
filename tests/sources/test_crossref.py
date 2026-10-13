from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tekmeria.contract.models import Provenance, Quality, Record, RecordIds, Retraction, Signal
from tekmeria.infra.cache import Cache
from tekmeria.infra.http import Upstream, UpstreamResponseError
from tekmeria.sources.crossref import (
    CrossrefAdapter,
    crossref_host,
    merge_retraction,
    retraction_from_items,
)

DOI = "10.1016/S0140-6736(97)11096-0"


def _notice(doi: str, kind: str, year: int) -> dict[str, Any]:
    return {
        "DOI": doi,
        "update-to": [
            {"DOI": DOI.lower(), "type": kind, "updated": {"date-parts": [[year, 1, 1]]}}
        ],
    }


def _record(retraction: Signal[Retraction]) -> Record:
    return Record(
        record_id="pmid:9500320",
        kind="journal_article",
        ids=RecordIds(doi=DOI),
        title="t",
        provenance=Provenance(source="pubmed", endpoint="efetch", retrieved_at=datetime.now(UTC)),
        quality=Quality(retraction=retraction),
    )


def test_newest_retraction_notice_wins_and_corrections_are_ignored() -> None:
    items = [
        _notice("10.1/correction", "correction", 2004),
        _notice("10.1/retraction-old", "retraction", 2005),
        _notice("10.1/retraction-new", "retraction", 2010),
    ]
    assert retraction_from_items(DOI, items) == Retraction(
        retracted=True, notice_doi="10.1/retraction-new"
    )


def test_no_retraction_notice() -> None:
    assert retraction_from_items(DOI, [_notice("10.1/c", "correction", 2004)]) == Retraction(
        retracted=False
    )


def test_merge_keeps_both_sources_and_any_retraction() -> None:
    pubmed = Signal[Retraction].known(Retraction(retracted=True, notice_doi="10.1/pm"), "pubmed")
    merged = merge_retraction(_record(pubmed), Retraction(retracted=False))
    assert merged.quality.retraction.sources == ["pubmed", "crossref"]
    assert merged.quality.retraction.value == Retraction(retracted=True, notice_doi="10.1/pm")


def test_merge_onto_not_checked() -> None:
    merged = merge_retraction(
        _record(Signal[Retraction].not_checked()), Retraction(retracted=True, notice_doi="10.1/cr")
    )
    assert merged.quality.retraction.state == "known"
    assert merged.quality.retraction.sources == ["crossref"]


@pytest.mark.anyio
async def test_mailto_is_sent_and_bad_body_is_an_upstream_error() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="<html/>")

    async with Cache(":memory:") as cache:
        upstream = Upstream(
            crossref_host(mailto="me@example.org"),
            cache=cache,
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(UpstreamResponseError):
            await CrossrefAdapter(upstream).retraction(DOI)
        await upstream.aclose()
    assert seen[0].url.params["mailto"] == "me@example.org"
    assert seen[0].url.params["filter"] == f"updates:{DOI}"
