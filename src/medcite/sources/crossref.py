"""Crossref retraction status, including Retraction Watch data (spec section 8)."""

import json
from typing import Any

from medcite.contract.models import Record, Retraction, Signal
from medcite.infra.http import HostConfig, Upstream, UpstreamResponseError

SOURCE = "crossref"
TTL_S = 24 * 3600.0


def crossref_host(*, mailto: str | None) -> HostConfig:
    """`mailto` puts requests in Crossref's polite pool; it is optional."""
    return HostConfig(
        name="crossref",
        base_url="https://api.crossref.org/",
        rate=5.0,
        default_params={"mailto": mailto} if mailto else None,
    )


def _check(body: str) -> None:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise UpstreamResponseError(200, "Crossref returned a body that is not JSON") from exc
    if not isinstance(data, dict) or not isinstance((data.get("message") or {}).get("items"), list):
        raise UpstreamResponseError(200, "Crossref response has no message.items")


def _updated(entry: dict[str, Any]) -> tuple[int, ...]:
    parts = ((entry.get("updated") or {}).get("date-parts") or [[0]])[0]
    return tuple(int(p) for p in parts)


def retraction_from_items(doi: str, items: list[dict[str, Any]]) -> Retraction:
    """Retracted when any notice updates this DOI with type "retraction"; newest notice wins."""
    target = doi.lower()
    notices: list[tuple[tuple[int, ...], str]] = []
    for item in items:
        for entry in item.get("update-to") or []:
            if str(entry.get("DOI", "")).lower() == target and entry.get("type") == "retraction":
                notices.append((_updated(entry), str(item.get("DOI"))))
    if not notices:
        return Retraction(retracted=False)
    return Retraction(retracted=True, notice_doi=max(notices)[1])


def merge_retraction(record: Record, crossref: Retraction) -> Record:
    """Combine an existing retraction signal (e.g. PubMed's) with Crossref's."""
    current = record.quality.retraction
    known = current.value if current.state == "known" else None
    sources = list(current.sources) if known is not None else []
    merged = Retraction(
        retracted=crossref.retracted or bool(known and known.retracted),
        notice_doi=crossref.notice_doi or (known.notice_doi if known else None),
    )
    signal = Signal[Retraction].known(merged, *sources, SOURCE)
    return record.model_copy(
        update={"quality": record.quality.model_copy(update={"retraction": signal})}
    )


class CrossrefAdapter:
    def __init__(self, upstream: Upstream) -> None:
        self.upstream = upstream

    async def retraction(self, doi: str) -> Retraction:
        body = await self.upstream.get_text(
            "v1/works", {"filter": f"updates:{doi}", "rows": "20"}, ttl_s=TTL_S, validate=_check
        )
        return retraction_from_items(doi, json.loads(body)["message"]["items"])
