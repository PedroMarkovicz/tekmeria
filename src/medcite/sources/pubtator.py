"""PubTator3 entity annotations, mapped onto PubMed title and abstract passages."""

import json
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from medcite.contract.models import Entity, EntityType, Signal
from medcite.infra.http import HostConfig, Upstream, UpstreamResponseError
from medcite.sources.base import PassageDraft

SOURCE = "pubtator"
RECORD_TTL_S = 7 * 24 * 3600.0
BATCH = 100

ENTITY_TYPES: dict[str, EntityType] = {
    "Gene": "gene",
    "Disease": "disease",
    "Chemical": "chemical",
    "Species": "species",
    "Mutation": "mutation",
    "Variant": "mutation",
    "DNAMutation": "mutation",
    "ProteinMutation": "mutation",
    "SNP": "mutation",
    "CellLine": "cell_line",
}


def pubtator_host() -> HostConfig:
    # Same NCBI limit as E-utilities without a key; PubTator3 never gets the keyed rate.
    return HostConfig(
        name="pubtator",
        base_url="https://www.ncbi.nlm.nih.gov/research/pubtator3-api/",
        rate=3.0,
    )


def _check(body: str) -> None:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise UpstreamResponseError(200, "PubTator3 returned a body that is not JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("PubTator3"), list):
        raise UpstreamResponseError(200, "PubTator3 response has no PubTator3 list")


def _entity_id(kind: str, identifier: str) -> str:
    if kind == "Gene" and identifier.isdigit():
        return f"NCBIGene:{identifier}"
    if kind == "Species" and identifier.isdigit():
        return f"NCBITaxon:{identifier}"
    return identifier


def _passage_entities(draft: PassageDraft, passage: dict[str, Any]) -> Signal[list[Entity]]:
    """Entities whose spans land exactly on the draft text; any misfit makes it unknown."""
    source_text = str(passage.get("text") or "")
    start_in_source = source_text.find(draft.text)
    if start_in_source < 0:
        return Signal[list[Entity]].unknown(SOURCE)
    base = int(passage.get("offset") or 0) + start_in_source
    entities: list[Entity] = []
    for annotation in passage.get("annotations") or []:
        infons = annotation.get("infons") or {}
        kind = str(infons.get("type") or "")
        identifier = infons.get("identifier")
        locations = annotation.get("locations") or []
        if kind not in ENTITY_TYPES or not identifier or identifier == "-" or len(locations) != 1:
            continue
        start = int(locations[0]["offset"]) - base
        end = start + int(locations[0]["length"])
        if end <= 0 or start >= len(draft.text):
            continue  # belongs to another section of the same PubTator passage
        text = str(annotation.get("text") or "")
        if start < 0 or end > len(draft.text) or draft.text[start:end] != text:
            return Signal[list[Entity]].unknown(SOURCE)
        entities.append(
            Entity(
                id=_entity_id(kind, str(identifier)),
                type=ENTITY_TYPES[kind],
                text=text,
                span=(start, end),
            )
        )
    entities.sort(key=lambda e: (e.span, e.id))
    return Signal[list[Entity]].known(entities, SOURCE)


def attach_entities(
    drafts: Sequence[PassageDraft], document: dict[str, Any] | None
) -> list[PassageDraft]:
    """Set `entities` on title and abstract drafts from one PubTator3 BioC document."""
    by_type: dict[str, dict[str, Any]] = {}
    for passage in (document or {}).get("passages") or []:
        kind = str((passage.get("infons") or {}).get("type") or "")
        by_type.setdefault(kind, passage)
    result: list[PassageDraft] = []
    for draft in drafts:
        kind = (
            "title"
            if draft.path == "title"
            else "abstract"
            if draft.path.startswith("abstract.")
            else ""
        )
        passage = by_type.get(kind)
        if passage is None:
            result.append(replace(draft, entities=Signal[list[Entity]].unknown(SOURCE)))
        else:
            result.append(replace(draft, entities=_passage_entities(draft, passage)))
    return result


class PubTatorAdapter:
    def __init__(self, upstream: Upstream) -> None:
        self.upstream = upstream

    async def documents(self, pmids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """BioC documents by PMID; PMIDs PubTator3 has not indexed are simply absent."""
        found: dict[str, dict[str, Any]] = {}
        wanted = list(dict.fromkeys(pmids))
        for start in range(0, len(wanted), BATCH):
            batch = wanted[start : start + BATCH]
            body = await self.upstream.get_text(
                "publications/export/biocjson",
                {"pmids": ",".join(batch)},
                ttl_s=RECORD_TTL_S,
                validate=_check,
            )
            for document in json.loads(body)["PubTator3"]:
                found[str(document.get("id"))] = document
        return found
