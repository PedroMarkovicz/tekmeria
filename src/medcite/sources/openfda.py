"""openFDA drug labels: search by drug name and records by SPL set ID (spec section 8)."""

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import anyio

from medcite.contract.normalize import normalize_text
from medcite.infra.http import HostConfig, Upstream, UpstreamNotFoundError, UpstreamResponseError
from medcite.sources.base import FetchOutcome
from medcite.sources.errors import InvalidInputError
from medcite.sources.openfda_parse import parse_label

SEARCH_TTL_S = 3600.0
RECORD_TTL_S = 24 * 3600.0
MAX_IDS_PER_CALL = 20
MAX_SKIP = 25000  # openFDA refuses a larger skip
NAME_FIELDS = ("brand_name", "generic_name", "substance_name")
# openFDA now and then answers "No matches found!" (404) for a query that has matches,
# so "no match" is believed only when a second attempt, a moment later, says the same.
NOT_FOUND_ATTEMPTS = 2
NOT_FOUND_RETRY_DELAY_S = 1.0


def openfda_host(*, api_key: str | None) -> HostConfig:
    """240 requests per minute; without a key also 1,000 per day per IP address:
    https://open.fda.gov/apis/authentication/"""
    return HostConfig(
        name="openfda",
        base_url="https://api.fda.gov/",
        rate=240.0,
        per_seconds=60.0,
        default_params={"api_key": api_key} if api_key else None,
    )


def label_query(drug: str) -> str:
    """Brand, generic or substance name, as an exact phrase in any of the three.

    About two thirds of labels have no harmonized `openfda` block; for those, the phrase
    is matched in the product data elements, which name the product and its ingredients.
    """
    name = normalize_text(drug)
    if not name or '"' in name or "\\" in name:
        raise InvalidInputError(
            f"drug name {drug!r} is not usable",
            hint="Give a brand, generic or substance name without quotes, e.g. warfarin.",
        )
    names = " OR ".join(f'openfda.{field}:"{name}"' for field in NAME_FIELDS)
    return f'{names} OR (_missing_:openfda AND spl_product_data_elements:"{name}")'


def _check(body: str) -> None:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise UpstreamResponseError(200, "openFDA returned a body that is not JSON") from exc
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise UpstreamResponseError(200, "openFDA response has no results list")
    for result in results:
        if not isinstance(result, dict) or not result.get("set_id") or not result.get("version"):
            raise UpstreamResponseError(200, "openFDA label has no set_id or version")


@dataclass(frozen=True)
class LabelsPage:
    results: list[dict[str, Any]]
    total: int


class OpenFdaAdapter:
    def __init__(
        self,
        upstream: Upstream,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        self.upstream = upstream
        self._clock = clock
        self._sleep = sleep

    def now(self) -> datetime:
        return self._clock()

    async def _search(self, search: str, *, skip: int, limit: int, ttl_s: float) -> LabelsPage:
        params = {
            "search": search,
            "limit": str(limit),
            "skip": str(skip),
            "sort": "effective_time:desc",
        }
        body = None
        for attempt in range(NOT_FOUND_ATTEMPTS):
            if attempt:
                await self._sleep(NOT_FOUND_RETRY_DELAY_S)
            try:
                body = await self.upstream.get_text(
                    "drug/label.json", params, ttl_s=ttl_s, validate=_check
                )
                break
            except UpstreamNotFoundError:
                continue
        if body is None:
            return LabelsPage(results=[], total=0)  # openFDA answers "no matches" with a 404
        data = json.loads(body)
        total = int(((data.get("meta") or {}).get("results") or {}).get("total") or 0)
        return LabelsPage(results=list(data["results"]), total=total)

    async def search(self, drug: str, *, skip: int, limit: int) -> LabelsPage:
        """Every label naming the drug, newest first."""
        return await self._search(label_query(drug), skip=skip, limit=limit, ttl_s=SEARCH_TTL_S)

    async def load(self, set_ids: Sequence[str]) -> FetchOutcome:
        """Current labels for SPL set IDs (lower case UUIDs), in batches."""
        outcome = FetchOutcome()
        wanted = list(dict.fromkeys(set_ids))
        for start in range(0, len(wanted), MAX_IDS_PER_CALL):
            batch = wanted[start : start + MAX_IDS_PER_CALL]
            search = " OR ".join(f'set_id:"{set_id}"' for set_id in batch)
            page = await self._search(search, skip=0, limit=len(batch), ttl_s=RECORD_TTL_S)
            for result in page.results:
                set_id = str(result["set_id"]).lower()
                if set_id in batch and set_id not in outcome.bundles:
                    outcome.bundles[set_id] = parse_label(result, self._clock())
        outcome.missing = [i for i in wanted if i not in outcome.bundles]
        return outcome
