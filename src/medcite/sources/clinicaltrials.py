"""ClinicalTrials.gov API v2: trial search and records (spec section 8)."""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from medcite.infra.http import HostConfig, Upstream, UpstreamResponseError
from medcite.sources.base import FetchOutcome
from medcite.sources.clinicaltrials_parse import parse_study
from medcite.sources.errors import InvalidInputError

SEARCH_TTL_S = 3600.0
RECORD_TTL_S = 24 * 3600.0
PAGE_SIZE = 50  # fixed: page tokens are only valid for the page size they were issued with
MAX_IDS_PER_CALL = 20

TrialStatus = Literal[
    "NOT_YET_RECRUITING",
    "RECRUITING",
    "ENROLLING_BY_INVITATION",
    "ACTIVE_NOT_RECRUITING",
    "SUSPENDED",
    "TERMINATED",
    "COMPLETED",
    "WITHDRAWN",
    "UNKNOWN",
]
TrialPhase = Literal["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"]

# Search hits need only these fields, which keeps a 50-study page small.
HIT_FIELDS = ",".join(
    [
        "NCTId",
        "BriefTitle",
        "OfficialTitle",
        "OverallStatus",
        "Phase",
        "EnrollmentCount",
        "Condition",
        "InterventionName",
        "HasResults",
        "ReferencePMID",
        "ReferenceType",
        "LastUpdatePostDate",
        "StudyFirstPostDate",
    ]
)
# Records leave out locations, contacts, baseline data and adverse events (megabytes).
RECORD_FIELDS = ",".join(
    [
        *(
            f"protocolSection.{module}"
            for module in (
                "identificationModule",
                "statusModule",
                "sponsorCollaboratorsModule",
                "descriptionModule",
                "conditionsModule",
                "designModule",
                "armsInterventionsModule",
                "outcomesModule",
                "eligibilityModule",
                "referencesModule",
            )
        ),
        "resultsSection.outcomeMeasuresModule",
        "hasResults",
    ]
)


def clinicaltrials_host() -> HostConfig:
    """About 50 requests per minute per IP address, the limit ClinicalTrials.gov asks
    API users to stay under (https://clinicaltrials.gov/data-api/api)."""
    return HostConfig(
        name="clinicaltrials",
        base_url="https://clinicaltrials.gov/api/v2/",
        rate=50.0,
        per_seconds=60.0,
    )


@dataclass(frozen=True)
class TrialQuery:
    condition: str | None = None
    intervention: str | None = None
    terms: str | None = None
    statuses: tuple[TrialStatus, ...] = ()
    phases: tuple[TrialPhase, ...] = ()
    has_results: bool | None = None


def build_params(query: TrialQuery) -> dict[str, str]:
    """Query and filter parameters; statuses, phases and results are filtered upstream."""
    if not (query.condition or query.intervention or query.terms):
        raise InvalidInputError(
            "give at least one of condition, intervention or terms",
            hint="For example condition='heart failure', intervention='dapagliflozin'.",
        )
    params: dict[str, str] = {}
    for name, value in (
        ("query.cond", query.condition),
        ("query.intr", query.intervention),
        ("query.term", query.terms),
    ):
        if value:
            params[name] = value
    if query.statuses:
        params["filter.overallStatus"] = ",".join(query.statuses)
    advanced: list[str] = []
    if query.phases:
        advanced.append("AREA[Phase](" + " OR ".join(query.phases) + ")")
    if query.has_results is not None:
        advanced.append(f"AREA[HasResults]{'true' if query.has_results else 'false'}")
    if advanced:
        params["filter.advanced"] = " AND ".join(advanced)
    return params


def _check(body: str) -> None:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise UpstreamResponseError(
            200, "ClinicalTrials.gov returned a body that is not JSON"
        ) from exc
    studies = data.get("studies") if isinstance(data, dict) else None
    if not isinstance(studies, list):
        raise UpstreamResponseError(200, "ClinicalTrials.gov response has no studies list")
    for study in cast("list[object]", studies):
        if not _nct_id(study):
            raise UpstreamResponseError(200, "ClinicalTrials.gov study has no nctId")


def _nct_id(study: object) -> object:
    """protocolSection.identificationModule.nctId, or None if any level is not an object."""
    value = study
    for key in ("protocolSection", "identificationModule", "nctId"):
        if not isinstance(value, dict):
            return None
        value = cast("dict[str, object]", value).get(key)
    return value


def nct_of(study: dict[str, Any]) -> str:
    return str(study["protocolSection"]["identificationModule"]["nctId"])


@dataclass(frozen=True)
class TrialsPage:
    studies: list[dict[str, Any]]
    total: int | None  # the registry sends the count with the first page only
    next_token: str | None  # None on the last page


class ClinicalTrialsAdapter:
    def __init__(
        self, upstream: Upstream, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.upstream = upstream
        self._clock = clock

    def now(self) -> datetime:
        return self._clock()

    async def search(self, query: TrialQuery, *, token: str | None) -> TrialsPage:
        params = {
            **build_params(query),
            "fields": HIT_FIELDS,
            "pageSize": str(PAGE_SIZE),
            "countTotal": "true",
            "format": "json",
        }
        if token:
            params["pageToken"] = token
        try:
            body = await self.upstream.get_text(
                "studies", params, ttl_s=SEARCH_TTL_S, validate=_check
            )
        except UpstreamResponseError as exc:
            if exc.status != 400:
                raise
            # The registry rejected the query itself; its message says what to fix.
            raise InvalidInputError(
                f"ClinicalTrials.gov rejected the query: {exc}",
                hint="Use plain words, or check the ClinicalTrials.gov search syntax.",
            ) from exc
        data = json.loads(body)
        return TrialsPage(
            studies=list(data["studies"]),
            total=int(data["totalCount"]) if "totalCount" in data else None,
            next_token=data.get("nextPageToken") or None,
        )

    async def load(self, ids: Sequence[str]) -> FetchOutcome:
        """Trial records for NCT IDs (upper case, e.g. 'NCT04368728'), in batches."""
        outcome = FetchOutcome()
        wanted = list(dict.fromkeys(ids))
        for start in range(0, len(wanted), MAX_IDS_PER_CALL):
            batch = wanted[start : start + MAX_IDS_PER_CALL]
            params = {
                "filter.ids": ",".join(batch),
                "fields": RECORD_FIELDS,
                "pageSize": str(len(batch)),
                "format": "json",
            }
            body = await self.upstream.get_text(
                "studies", params, ttl_s=RECORD_TTL_S, validate=_check
            )
            for study in json.loads(body)["studies"]:
                # An obsolete ID merged into this study is listed in nctIdAliases; the
                # registry answers it with the study under its current ID.
                ident = study["protocolSection"]["identificationModule"]
                ids = [nct_of(study), *(str(a) for a in ident.get("nctIdAliases") or [])]
                requested = [i for i in ids if i in batch]
                if requested:
                    bundle = parse_study(study, self._clock())
                    for nct in requested:
                        outcome.bundles[nct] = bundle
        outcome.missing = [i for i in wanted if i not in outcome.bundles]
        return outcome
