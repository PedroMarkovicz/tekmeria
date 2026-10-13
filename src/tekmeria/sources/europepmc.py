"""Europe PMC REST: literature search, preprints, and PubMed Central open-access full text."""

import json
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from tekmeria.contract.models import StudyDesign
from tekmeria.infra.http import (
    HostConfig,
    Upstream,
    UpstreamNotFoundError,
    UpstreamResponseError,
    UpstreamUnavailableError,
)
from tekmeria.sources.base import FetchOutcome
from tekmeria.sources.errors import InvalidInputError
from tekmeria.sources.europepmc_parse import parse_full_text, parse_result
from tekmeria.sources.pubmed import STUDY_DESIGN_FILTERS, LiteratureQuery

SEARCH_TTL_S = 3600.0
RECORD_TTL_S = 7 * 24 * 3600.0
MAX_IDS_PER_QUERY = 50

# Sources with a record_id scheme. Others (patents, theses, Agricola, ...) cannot be cited.
CITABLE_SOURCES = ("MED", "PMC", "PPR")
_FULL_TEXT_FAILURES = (UpstreamUnavailableError, UpstreamResponseError, UpstreamNotFoundError)

# Europe PMC indexes MEDLINE publication types under PUB_TYPE.
_PUB_TYPES: dict[StudyDesign, tuple[str, ...]] = {
    "meta_analysis": ("Meta-Analysis",),
    "systematic_review": ("Systematic Review",),
    "randomized_controlled_trial": ("Randomized Controlled Trial",),
    "clinical_trial": ("Clinical Trial",),
    "observational_study": ("Observational Study",),
    "case_report": ("Case Reports",),
    "review": ("Review",),
    "guideline": ("Guideline", "Practice Guideline"),
}


def europepmc_host() -> HostConfig:
    return HostConfig(
        name="europepmc",
        base_url="https://www.ebi.ac.uk/europepmc/webservices/rest/",
        rate=5.0,
    )


def _date(value: str, *, end: bool) -> str:
    if len(value) == 4:
        return f"{value}-12-31" if end else f"{value}-01-01"
    return value


def build_query(query: LiteratureQuery) -> str:
    """The same filters as PubMed, in Europe PMC syntax."""
    clauses = [f"({query.query})", "(" + " OR ".join(f"SRC:{s}" for s in CITABLE_SOURCES) + ")"]
    if query.study_designs:
        unfilterable = [d for d in query.study_designs if d not in STUDY_DESIGN_FILTERS]
        if unfilterable:
            raise InvalidInputError(
                f"cannot filter by study design {unfilterable[0]!r}",
                hint="Filterable designs: " + ", ".join(STUDY_DESIGN_FILTERS),
            )
        types = [f'PUB_TYPE:"{t}"' for d in query.study_designs for t in _PUB_TYPES[d]]
        clauses.append("(" + " OR ".join(types) + ")")
    if query.date_from or query.date_to:
        start = _date(query.date_from or "1800", end=False)
        stop = _date(query.date_to or "3000", end=True)
        clauses.append(f"FIRST_PDATE:[{start} TO {stop}]")
    if query.open_access_only:
        clauses.append("OPEN_ACCESS:y")
    text = " AND ".join(clauses)
    if query.peer_reviewed_only:
        text += " NOT SRC:PPR"
    if query.exclude_retracted:
        text += ' NOT PUB_TYPE:"Retracted Publication"'
    return text


def is_citable(result: dict[str, Any]) -> bool:
    return result.get("source") in CITABLE_SOURCES


def _check_search(body: str) -> None:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise UpstreamResponseError(
            200, "Europe PMC search returned a body that is not JSON"
        ) from exc
    if not isinstance(data, dict) or not isinstance(
        (data.get("resultList") or {}).get("result"), list
    ):
        raise UpstreamResponseError(200, "Europe PMC search response has no resultList")


def _check_full_text(body: str) -> None:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise UpstreamResponseError(200, "Europe PMC full text is not XML") from exc
    if root.tag != "article":
        raise UpstreamResponseError(
            200, f"Europe PMC full text is <{root.tag}>, not a JATS article"
        )


@dataclass(frozen=True)
class SearchPage:
    results: list[dict[str, Any]]
    total: int
    next_mark: str | None  # None when there is no further page


@dataclass
class LoadOutcome(FetchOutcome):
    """Also lists the PMCIDs whose full text failed; their metadata passages are kept."""

    full_text_failed: list[str] = field(default_factory=list[str])


class EuropePmcAdapter:
    def __init__(
        self, upstream: Upstream, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.upstream = upstream
        self._clock = clock

    def now(self) -> datetime:
        return self._clock()

    async def _search(self, query: str, *, mark: str, size: int, ttl_s: float) -> SearchPage:
        params = {
            "query": query,
            "resultType": "core",
            "format": "json",
            "pageSize": str(size),
            "cursorMark": mark,
        }
        body = await self.upstream.get_text("search", params, ttl_s=ttl_s, validate=_check_search)
        data = json.loads(body)
        next_mark = data.get("nextCursorMark")
        results = list(data["resultList"]["result"])
        return SearchPage(
            results=results,
            total=int(data.get("hitCount") or 0),
            next_mark=next_mark if next_mark and next_mark != mark and results else None,
        )

    async def search(self, query: LiteratureQuery, *, mark: str, size: int) -> SearchPage:
        return await self._search(build_query(query), mark=mark, size=size, ttl_s=SEARCH_TTL_S)

    async def load(self, ids: Sequence[str], *, full_text: bool) -> LoadOutcome:
        """Records for `pmcid:` and `ppr:` ids (the bare ids, e.g. 'PMC123', 'PPR456').

        Full text is fetched only for open-access PMC articles, as `body.*` passages. A PMCID
        whose full text fails keeps its metadata passages and is listed in `full_text_failed`.
        """
        outcome = LoadOutcome()
        wanted = list(dict.fromkeys(ids))
        pmcids = [i for i in wanted if i.upper().startswith("PMC")]
        pprs = [i for i in wanted if i.upper().startswith("PPR")]
        for id_field, group, scheme in (("PMCID", pmcids, "pmcid"), ("EXT_ID", pprs, "ppr")):
            for start in range(0, len(group), MAX_IDS_PER_QUERY):
                batch = group[start : start + MAX_IDS_PER_QUERY]
                clause = " OR ".join(f"{id_field}:{i}" for i in batch)
                query = f"({clause}) AND SRC:PPR" if scheme == "ppr" else f"({clause})"
                page = await self._search(query, mark="*", size=len(batch) * 2, ttl_s=RECORD_TTL_S)
                for result in page.results:
                    key = str(result.get("pmcid") if scheme == "pmcid" else result.get("id") or "")
                    match = next((i for i in batch if i.upper() == key.upper()), None)
                    if match is None or match in outcome.bundles:
                        continue
                    outcome.bundles[match] = parse_result(
                        result, self._clock(), record_id=f"{scheme}:{match}"
                    )
        if full_text:
            for pmcid in pmcids:
                bundle = outcome.bundles.get(pmcid)
                oa = bundle.record.quality.open_access_full_text if bundle else None
                if bundle is None or oa is None or oa.value is None or not oa.value.available:
                    continue
                try:
                    body = await self.upstream.get_text(
                        f"{pmcid}/fullTextXML", {}, ttl_s=RECORD_TTL_S, validate=_check_full_text
                    )
                except _FULL_TEXT_FAILURES:
                    outcome.full_text_failed.append(pmcid)
                    continue
                outcome.bundles[pmcid] = replace(
                    bundle, passages=[*bundle.passages, *parse_full_text(body)]
                )
        outcome.missing = [i for i in wanted if i not in outcome.bundles]
        return outcome
