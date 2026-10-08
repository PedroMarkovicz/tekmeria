from typing import Any

import httpx
import pytest

from tekmerion.infra.cache import Cache
from tekmerion.infra.http import Upstream, UpstreamResponseError
from tekmerion.sources.base import PassageDraft
from tekmerion.sources.pubtator import PubTatorAdapter, attach_entities, pubtator_host

TITLE = "TP53 mutations in breast cancer"
SECTION_ONE = "We studied TP53 in 40 patients."
SECTION_TWO = "Cisplatin helped."


def _annotation(kind: str, identifier: str, text: str, offset: int) -> dict[str, Any]:
    return {
        "infons": {"type": kind, "identifier": identifier},
        "text": text,
        "locations": [{"offset": offset, "length": len(text)}],
    }


def _document() -> dict[str, Any]:
    abstract = f"BACKGROUND: {SECTION_ONE} RESULTS: {SECTION_TWO}"
    abstract_offset = len(TITLE) + 1
    tp53 = abstract_offset + abstract.index("TP53")
    cisplatin = abstract_offset + abstract.index("Cisplatin")
    return {
        "id": "1",
        "passages": [
            {
                "infons": {"type": "title"},
                "offset": 0,
                "text": TITLE,
                "annotations": [
                    _annotation("Gene", "7157", "TP53", 0),
                    _annotation("Disease", "MESH:D001943", "breast cancer", 18),
                ],
            },
            {
                "infons": {"type": "abstract"},
                "offset": abstract_offset,
                "text": abstract,
                "annotations": [
                    _annotation("Gene", "7157", "TP53", tp53),
                    _annotation("Chemical", "MESH:D002945", "Cisplatin", cisplatin),
                    _annotation("Species", "-", "patients", tp53 + 8),  # no identifier: skipped
                ],
            },
        ],
    }


DRAFTS = [
    PassageDraft("title", TITLE),
    PassageDraft("abstract.1", SECTION_ONE, "BACKGROUND"),
    PassageDraft("abstract.2", SECTION_TWO, "RESULTS"),
]


def test_entities_land_on_each_section_with_local_spans() -> None:
    title, first, second = attach_entities(DRAFTS, _document())
    assert title.entities is not None and title.entities.value is not None
    assert [(e.id, e.type, e.span) for e in title.entities.value] == [
        ("NCBIGene:7157", "gene", (0, 4)),
        ("MESH:D001943", "disease", (18, 31)),
    ]
    assert first.entities is not None and first.entities.value is not None
    assert [(e.id, SECTION_ONE[e.span[0] : e.span[1]]) for e in first.entities.value] == [
        ("NCBIGene:7157", "TP53")
    ]
    assert second.entities is not None and second.entities.value is not None
    assert [e.type for e in second.entities.value] == ["chemical"]


def test_a_span_that_does_not_fit_makes_the_section_unknown() -> None:
    document = _document()
    document["passages"][0]["annotations"][0]["text"] = "TP5"
    title, first, _ = attach_entities(DRAFTS, document)
    assert title.entities is not None and title.entities.state == "unknown"
    assert first.entities is not None and first.entities.state == "known"


def test_not_indexed_document_is_unknown() -> None:
    assert all(
        d.entities is not None and d.entities.state == "unknown"
        for d in attach_entities(DRAFTS, None)
    )


def test_text_missing_from_pubtator_passage_is_unknown() -> None:
    (draft,) = attach_entities(
        [PassageDraft("abstract.1", "Text PubTator never saw.")], _document()
    )
    assert draft.entities is not None and draft.entities.state == "unknown"


@pytest.mark.anyio
async def test_body_without_pubtator_list_is_an_upstream_error() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="[]"))
    async with Cache(":memory:") as cache:
        upstream = Upstream(pubtator_host(), cache=cache, transport=transport)
        with pytest.raises(UpstreamResponseError):
            await PubTatorAdapter(upstream).documents(["1"])
        await upstream.aclose()
