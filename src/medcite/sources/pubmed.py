"""PubMed through NCBI E-utilities: search (ESearch) and records (EFetch)."""

import json
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from medcite.contract.models import StudyDesign
from medcite.infra.http import HostConfig, Upstream, UpstreamResponseError
from medcite.sources.base import FetchOutcome
from medcite.sources.errors import InvalidInputError
from medcite.sources.pubmed_parse import parse_efetch

SEARCH_TTL_S = 3600.0
RECORD_TTL_S = 7 * 24 * 3600.0
EFETCH_BATCH = 200

# Publication-type filters pushed to PubMed (spec 6.2). "other" cannot be filtered.
STUDY_DESIGN_FILTERS: dict[StudyDesign, str] = {
    "meta_analysis": "meta-analysis[pt]",
    "systematic_review": "systematic review[pt]",
    "randomized_controlled_trial": "randomized controlled trial[pt]",
    "clinical_trial": "clinical trial[pt]",
    "observational_study": "observational study[pt]",
    "case_report": "case reports[pt]",
    "review": "review[pt]",
    "guideline": "guideline[pt] OR practice guideline[pt]",
}


def ncbi_host(*, api_key: str | None, email: str | None) -> HostConfig:
    """NCBI asks for `tool` and `email`; an API key raises the limit from 3 to 10 per second."""
    params = {"tool": "medcite"}
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key
    return HostConfig(
        name="ncbi",
        base_url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/",
        rate=10.0 if api_key else 3.0,
        default_params=params,
    )


@dataclass(frozen=True)
class LiteratureQuery:
    query: str
    date_from: str | None = None  # YYYY or YYYY-MM-DD
    date_to: str | None = None
    study_designs: tuple[StudyDesign, ...] = ()
    peer_reviewed_only: bool = False
    open_access_only: bool = False
    exclude_retracted: bool = False


def build_term(query: LiteratureQuery) -> str:
    term = f"({query.query})"
    if query.study_designs:
        unfilterable = [d for d in query.study_designs if d not in STUDY_DESIGN_FILTERS]
        if unfilterable:
            raise InvalidInputError(
                f"cannot filter by study design {unfilterable[0]!r}",
                hint="Filterable designs: " + ", ".join(STUDY_DESIGN_FILTERS),
            )
        designs = " OR ".join(STUDY_DESIGN_FILTERS[d] for d in query.study_designs)
        term += f" AND ({designs})"
    if query.open_access_only:
        term += ' AND "pubmed pmc open access"[filter]'
    if query.peer_reviewed_only:
        term += " NOT preprint[pt]"
    if query.exclude_retracted:
        term += " NOT retracted publication[pt]"
    return term


def _date_params(query: LiteratureQuery) -> dict[str, str]:
    if query.date_from is None and query.date_to is None:
        return {}
    # E-utilities needs both bounds whenever one is given.
    return {
        "datetype": "pdat",
        "mindate": (query.date_from or "1800").replace("-", "/"),
        "maxdate": (query.date_to or "3000").replace("-", "/"),
    }


def _check_esearch(body: str) -> None:
    """Reject anything but an ESearch JSON result before it is cached."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise UpstreamResponseError(200, "ESearch returned a body that is not JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("esearchresult"), dict):
        raise UpstreamResponseError(200, "ESearch response has no esearchresult")


def _check_efetch(body: str) -> None:
    """Reject anything but a PubmedArticleSet, e.g. <eFetchResult><ERROR>, before caching.

    Otherwise an error page that happens to be valid XML would read as "no such
    article" and turn genuine handles into `withdrawn`.
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise UpstreamResponseError(200, "EFetch returned a body that is not XML") from exc
    if root.tag != "PubmedArticleSet":
        raise UpstreamResponseError(200, f"EFetch returned <{root.tag}>, not PubmedArticleSet")


class PubMedAdapter:
    def __init__(
        self, upstream: Upstream, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.upstream = upstream
        self._clock = clock

    async def search(
        self, query: LiteratureQuery, *, offset: int, limit: int
    ) -> tuple[int, list[str]]:
        params = {
            "db": "pubmed",
            "term": build_term(query),
            "retstart": str(offset),
            "retmax": str(limit),
            "retmode": "json",
            "sort": "relevance",
            **_date_params(query),
        }
        body = await self.upstream.get_text(
            "esearch.fcgi", params, ttl_s=SEARCH_TTL_S, validate=_check_esearch
        )
        result = json.loads(body)["esearchresult"]
        if "ERROR" in result:
            raise InvalidInputError(
                f"PubMed rejected the query: {result['ERROR']}",
                hint="Check PubMed query syntax, e.g. quotes and field tags like [tiab].",
            )
        return int(result.get("count", "0")), [str(pmid) for pmid in result.get("idlist", [])]

    async def fetch(self, pmids: Sequence[str]) -> FetchOutcome:
        outcome = FetchOutcome()
        wanted = list(dict.fromkeys(pmids))
        for start in range(0, len(wanted), EFETCH_BATCH):
            batch = wanted[start : start + EFETCH_BATCH]
            params = {"db": "pubmed", "id": ",".join(batch), "retmode": "xml"}
            body = await self.upstream.get_text(
                "efetch.fcgi", params, ttl_s=RECORD_TTL_S, validate=_check_efetch
            )
            for bundle in parse_efetch(body, self._clock()):
                outcome.bundles[bundle.record.ids.pmid or ""] = bundle
        outcome.missing = [pmid for pmid in wanted if pmid not in outcome.bundles]
        return outcome
