"""Routes requests to adapters, mints handles, and resolves handles for the audit."""

import contextlib
import math
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any

import anyio
from pydantic import JsonValue

from medcite.contract.audit import Resolution, Resolved, Unavailable, Withdrawn
from medcite.contract.handles import HandleCodec, Locator, RecordRef, parse_record_id
from medcite.contract.models import (
    Entity,
    Passage,
    Record,
    Retraction,
    Signal,
    SourceStatusValue,
)
from medcite.contract.tool_models import LabelHit, LiteratureHit, RecordPassages, TrialHit
from medcite.infra.cache import Cache
from medcite.infra.http import UpstreamResponseError, UpstreamUnavailableError
from medcite.sources.base import FetchOutcome, PassageDraft, RecordBundle
from medcite.sources.clinicaltrials import PAGE_SIZE as TRIALS_PAGE_SIZE
from medcite.sources.clinicaltrials import ClinicalTrialsAdapter, TrialQuery
from medcite.sources.clinicaltrials_parse import trial_hit
from medcite.sources.crossref import CrossrefAdapter, merge_retraction
from medcite.sources.errors import InvalidInputError
from medcite.sources.europepmc import EuropePmcAdapter, LoadOutcome, is_citable
from medcite.sources.europepmc_parse import parse_result, record_id_for
from medcite.sources.merge import Candidate, identity_keys, interleave
from medcite.sources.openfda import MAX_SKIP, OpenFdaAdapter
from medcite.sources.openfda_parse import label_hit
from medcite.sources.pubmed import LiteratureQuery, PubMedAdapter
from medcite.sources.pubtator import PubTatorAdapter, attach_entities

SUPPORTED_SCHEMES = ("pmid", "pmcid", "ppr", "nct", "setid")
LITERATURE_SOURCES = ("pubmed", "europepmc")
PREVIEW_CHARS = 300
EPMC_PAGE_SIZE = 25  # fixed, so deep paging never asks Europe PMC for an oversized page
# Shares of the tool deadline. Sources run side by side, each within SOURCE_SHARE; each
# follow-up step (companions, PubTator, Crossref, PubMed rendering) gets STEP_SHARE.
SOURCE_SHARE = 0.4
STEP_SHARE = 0.15
UPSTREAM_FAILURES = (UpstreamUnavailableError, UpstreamResponseError)
# Which source serves each scheme, and its name in messages.
SCHEME_SOURCES = {
    "pmid": "pubmed",
    "pmcid": "europepmc",
    "ppr": "europepmc",
    "nct": "clinicaltrials",
    "setid": "openfda",
}
SOURCE_NAMES = {
    "pubmed": "PubMed",
    "europepmc": "Europe PMC",
    "clinicaltrials": "ClinicalTrials.gov",
    "openfda": "openFDA",
}
# Record IDs that the registries would reject, caught before any request.
_ID_FORMS = {
    "nct": (re.compile(r"^NCT\d{8}$"), "nct:NCT04368728"),
    "setid": (
        re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
        "setid:0cbce382-9c88-4f58-ae0f-532a841e8f95",
    ),
}
# PubMed field tags such as [tiab] or [MeSH Terms]; Europe PMC would search them as text.
_PUBMED_TAG = re.compile(r"\[[A-Za-z][A-Za-z /:-]*\]")


@dataclass(frozen=True)
class SearchState:
    """Where each source's results continue; serialized into the search cursor.

    Europe PMC continues at item `europepmc_skip` of the page at `europepmc_mark`.
    """

    pubmed: int = 0
    europepmc_mark: str = "*"
    europepmc_skip: int = 0
    pubmed_done: bool = False
    europepmc_done: bool = False

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "p": self.pubmed,
            "m": self.europepmc_mark,
            "s": self.europepmc_skip,
            "pd": self.pubmed_done,
            "ed": self.europepmc_done,
        }

    @classmethod
    def from_json(cls, data: JsonValue) -> "SearchState":
        try:
            assert isinstance(data, dict)
            state = cls(
                pubmed=int(data["p"]),  # type: ignore[arg-type]
                europepmc_mark=str(data["m"]),
                europepmc_skip=int(data["s"]),  # type: ignore[arg-type]
                pubmed_done=bool(data["pd"]),
                europepmc_done=bool(data["ed"]),
            )
        except (AssertionError, KeyError, TypeError, ValueError) as exc:
            raise InvalidInputError("cursor state is not valid") from exc
        if state.pubmed < 0 or state.europepmc_skip < 0:
            raise InvalidInputError("cursor state is not valid")
        return state


@dataclass(frozen=True)
class TrialSearchState:
    """Where trial search continues: item `skip` of the registry page at `token`."""

    token: str | None = None  # None is the first registry page
    skip: int = 0

    def to_json(self) -> dict[str, JsonValue]:
        return {"t": self.token, "s": self.skip}

    @classmethod
    def from_json(cls, data: JsonValue) -> "TrialSearchState":
        if not isinstance(data, dict):
            raise InvalidInputError("cursor state is not valid")
        token, skip = data.get("t"), data.get("s")
        if token is not None and not isinstance(token, str):
            raise InvalidInputError("cursor state is not valid")
        if not isinstance(skip, int) or isinstance(skip, bool) or skip < 0:
            raise InvalidInputError("cursor state is not valid")
        return cls(token=token, skip=skip)


@dataclass(frozen=True)
class TrialSearchPage:
    hits: list[TrialHit]
    total: int
    next_state: TrialSearchState | None


@dataclass(frozen=True)
class LabelSearchPage:
    hits: list[LabelHit]
    total: int
    next_offset: int | None


@dataclass(frozen=True)
class SearchPage:
    hits: list[LiteratureHit]
    totals: dict[str, int]
    next_state: SearchState | None
    source_status: dict[str, SourceStatusValue]


@dataclass(frozen=True)
class FetchPage:
    records: list[RecordPassages]
    not_found: list[str]
    unavailable: list[str]
    next_offset: int | None
    source_status: dict[str, SourceStatusValue]


@dataclass
class _Loaded:
    bundles: dict[str, RecordBundle] = field(default_factory=dict[str, RecordBundle])
    missing: list[str] = field(default_factory=list[str])
    failed: dict[str, str] = field(default_factory=dict[str, str])  # record key -> reason
    status: dict[str, SourceStatusValue] = field(default_factory=dict[str, SourceStatusValue])
    no_full_text: set[str] = field(default_factory=set[str])  # record keys whose body failed


@dataclass(frozen=True)
class _EuropePmcSlice:
    items: list[tuple[str, int, dict[str, Any]]]  # (page mark, index in page, citable result)
    end_mark: str | None  # the page after the last one fetched; None when there is none
    total: int


_SEVERITY: dict[SourceStatusValue, int] = {"ok": 0, "degraded": 1, "unavailable": 2}


def _worse(a: SourceStatusValue | None, b: SourceStatusValue) -> SourceStatusValue:
    return b if a is None or _SEVERITY[b] > _SEVERITY[a] else a


def _matches(path: str, prefixes: Sequence[str] | None) -> bool:
    if not prefixes:
        return True
    return any(path == prefix or path.startswith(prefix + ".") for prefix in prefixes)


def canonical(ref: RecordRef) -> RecordRef:
    """IDs as the upstreams spell them: NCT, PMC and PPR upper case, set IDs lower case."""
    if ref.scheme == "setid":
        return replace(ref, id=ref.id.lower())
    if ref.scheme in ("nct", "pmcid", "ppr"):
        return replace(ref, id=ref.id.upper())
    return ref


def _record_ref(record_id: str) -> RecordRef:
    try:
        ref = parse_record_id(record_id)
    except ValueError as exc:
        raise InvalidInputError(
            f"invalid record id {record_id!r}", hint="Use the form pmid:30831578."
        ) from exc
    if ref.scheme not in SUPPORTED_SCHEMES:
        raise InvalidInputError(
            f"scheme {ref.scheme!r} is not supported yet",
            hint="Supported schemes: " + ", ".join(SUPPORTED_SCHEMES),
        )
    ref = canonical(ref)
    if ref.scheme in _ID_FORMS:
        pattern, example = _ID_FORMS[ref.scheme]
        if not pattern.match(ref.id):
            raise InvalidInputError(f"invalid {ref.scheme} id {ref.id!r}", hint=f"e.g. {example}")
    return ref


def _require_any(status: Mapping[str, SourceStatusValue]) -> None:
    if status and all(value == "unavailable" for value in status.values()):
        raise UpstreamUnavailableError("no literature source answered: " + ", ".join(status))


async def _concurrently(*calls: Callable[[], Awaitable[None]]) -> None:
    """Run calls side by side; re-raise the first error as itself, not as an ExceptionGroup."""
    errors: list[Exception] = []

    async def guarded(call: Callable[[], Awaitable[None]]) -> None:
        try:
            await call()
        except Exception as exc:
            errors.append(exc)

    async with anyio.create_task_group() as group:
        for call in calls:
            group.start_soon(guarded, call)
    if errors:
        raise errors[0]


class EvidenceService:
    def __init__(
        self,
        *,
        codec: HandleCodec,
        cache: Cache,
        pubmed: PubMedAdapter,
        europepmc: EuropePmcAdapter,
        pubtator: PubTatorAdapter,
        crossref: CrossrefAdapter,
        trials: ClinicalTrialsAdapter,
        labels: OpenFdaAdapter,
        deadline_s: float = math.inf,
    ) -> None:
        self.codec = codec
        self.cache = cache
        self.pubmed = pubmed
        self.europepmc = europepmc
        self.pubtator = pubtator
        self.crossref = crossref
        self.trials = trials
        self.labels = labels
        # Every upstream step gets a share of the tool deadline, so a slow source yields
        # an `unavailable` or `degraded` status in a full result instead of losing it all.
        self.deadline_s = deadline_s

    async def _within[T](self, share: float, call: Callable[[], Awaitable[T]]) -> T | None:
        """`call`'s result, or None when its upstream fails or it overruns its share."""
        with anyio.move_on_after(self.deadline_s * share):
            try:
                return await call()
            except UPSTREAM_FAILURES:
                return None
        return None

    async def _issue(self, record: Record, drafts: Sequence[PassageDraft]) -> list[Passage]:
        """Mint handles for drafts and remember each text as issued."""
        ref = parse_record_id(record.record_id)
        passages: list[Passage] = []
        rows: list[tuple[str, str, str | None]] = []
        for draft in drafts:
            locator = ref.at(draft.path)
            passages.append(
                Passage(
                    handle=self.codec.issue(locator),
                    record_id=record.record_id,
                    path=draft.path,
                    section_label=draft.section_label,
                    text=draft.text,
                    entities=draft.entities or Signal[list[Entity]].not_checked(),
                )
            )
            rows.append((str(locator), draft.text, record.provenance.upstream_version))
        await self.cache.record_issued(rows)
        return passages

    async def _hit(self, bundle: RecordBundle, *, mint_title: bool) -> LiteratureHit:
        record = bundle.record
        title = next((d for d in bundle.passages if d.path == "title"), None)
        first_abstract = next((d for d in bundle.passages if d.path.startswith("abstract.")), None)
        title_handle = None
        if title is not None and mint_title:
            (issued,) = await self._issue(record, [title])
            title_handle = issued.handle
        return LiteratureHit(
            record_id=record.record_id,
            kind=record.kind,
            title=record.title,
            title_handle=title_handle,
            preview=first_abstract.text[:PREVIEW_CHARS] if first_abstract else None,
            published=record.published,
            venue=record.venue,
            study_designs=record.quality.study_designs,
            peer_reviewed=record.quality.peer_reviewed,
            retraction=record.quality.retraction,
            open_access_full_text=record.quality.open_access_full_text,
            ids=record.ids,
        )

    async def _europepmc_slice(
        self, query: LiteratureQuery, mark: str, skip: int, want: int
    ) -> _EuropePmcSlice:
        """At least `want` citable results from (mark, skip) on, in fixed-size pages."""
        items: list[tuple[str, int, dict[str, Any]]] = []
        total = 0
        current: str | None = mark
        for _ in range(math.ceil(want / EPMC_PAGE_SIZE) + 1):
            assert current is not None
            page = await self.europepmc.search(query, mark=current, size=EPMC_PAGE_SIZE)
            total = page.total
            items.extend(
                (current, index, result)
                for index, result in enumerate(page.results)
                if index >= skip and is_citable(result)
            )
            current, skip = page.next_mark, 0
            if current is None or len(items) >= want:
                break
        return _EuropePmcSlice(items=items, end_mark=current, total=total)

    async def search_literature(
        self, query: LiteratureQuery, *, sources: Sequence[str], state: SearchState, limit: int
    ) -> SearchPage:
        status: dict[str, SourceStatusValue] = {}
        totals: dict[str, int] = {}
        use_pubmed = "pubmed" in sources and not state.pubmed_done
        use_europepmc = "europepmc" in sources and not state.europepmc_done
        skip_europepmc = use_europepmc and _PUBMED_TAG.search(query.query) is not None
        if skip_europepmc:
            if "pubmed" not in sources:
                raise InvalidInputError(
                    "PubMed field tags such as [tiab] do not work on Europe PMC",
                    hint="Search with sources=['pubmed'], or use Europe PMC syntax "
                    "such as TITLE:aspirin.",
                )
            status["europepmc"], use_europepmc = "degraded", False

        pubmed_page: tuple[int, list[str]] | None = None  # (total, pmids)
        europepmc_slice: _EuropePmcSlice | None = None

        async def from_pubmed() -> None:
            nonlocal pubmed_page
            pubmed_page = await self._within(
                SOURCE_SHARE, partial(self.pubmed.search, query, offset=state.pubmed, limit=limit)
            )
            status["pubmed"] = "unavailable" if pubmed_page is None else "ok"

        async def from_europepmc() -> None:
            nonlocal europepmc_slice
            europepmc_slice = await self._within(
                SOURCE_SHARE,
                partial(
                    self._europepmc_slice,
                    query,
                    state.europepmc_mark,
                    state.europepmc_skip,
                    limit,
                ),
            )
            status["europepmc"] = "unavailable" if europepmc_slice is None else "ok"

        await _concurrently(
            *([from_pubmed] if use_pubmed else []),
            *([from_europepmc] if use_europepmc else []),
        )
        _require_any(status)

        pages: list[list[Candidate]] = []
        names: list[str] = []
        if pubmed_page is not None:
            totals["pubmed"], pmids = pubmed_page
            pages.append(
                [
                    Candidate(identity_keys(pmid=p, pmcid=None, doi=None, ppr=None), ("pmid", p))
                    for p in pmids
                ]
            )
            names.append("pubmed")
        if europepmc_slice is not None:
            totals["europepmc"] = europepmc_slice.total
            pages.append(
                [
                    Candidate(
                        identity_keys(
                            pmid=r.get("pmid"),
                            pmcid=r.get("pmcid"),
                            doi=r.get("doi"),
                            ppr=r.get("id") if r.get("source") == "PPR" else None,
                        ),
                        ("europepmc", r),
                    )
                    for _, _, r in europepmc_slice.items
                ]
            )
            names.append("europepmc")

        chosen, consumed = interleave(pages, limit)
        used = dict(zip(names, consumed, strict=True))
        hits = await self._hits(chosen, status)
        _require_any(status)

        next_state = state
        # PubMed resumes where it was if its records could not be fetched: nothing skipped.
        if pubmed_page is not None and status["pubmed"] == "ok":
            total, pmids = pubmed_page
            offset = state.pubmed + used["pubmed"]
            next_state = replace(
                next_state, pubmed=offset, pubmed_done=offset >= total or not pmids
            )
        if europepmc_slice is not None:
            taken = used["europepmc"]
            if taken < len(europepmc_slice.items):
                mark, index, _ = europepmc_slice.items[taken]
                next_state = replace(next_state, europepmc_mark=mark, europepmc_skip=index)
            elif europepmc_slice.end_mark is not None:
                next_state = replace(
                    next_state, europepmc_mark=europepmc_slice.end_mark, europepmc_skip=0
                )
            else:
                next_state = replace(next_state, europepmc_done=True)
        if skip_europepmc:
            next_state = replace(next_state, europepmc_done=True)
        finished = all(
            (name == "pubmed" and next_state.pubmed_done)
            or (name == "europepmc" and next_state.europepmc_done)
            for name in sources
            if name in LITERATURE_SOURCES
        )
        return SearchPage(
            hits=hits,
            totals=totals,
            next_state=None if finished else next_state,
            source_status=status,
        )

    async def search_trials(
        self, query: TrialQuery, *, state: TrialSearchState, limit: int
    ) -> TrialSearchPage:
        """A page of trial hits. Registry pages have a fixed size, so `limit` hits may span
        two of them; the next state names the registry page and position to resume at."""
        assert limit <= TRIALS_PAGE_SIZE
        items: list[tuple[str | None, int, dict[str, Any]]] = []
        token, skip, total = state.token, state.skip, 0
        for _ in range(2):
            page = await self.trials.search(query, token=token)
            total = page.total
            items.extend((token, i, study) for i, study in enumerate(page.studies) if i >= skip)
            token, skip = page.next_token, 0
            if token is None or len(items) >= limit:
                break
        if len(items) > limit:
            resume_token, resume_skip, _ = items[limit]
            next_state: TrialSearchState | None = TrialSearchState(resume_token, resume_skip)
        else:
            next_state = TrialSearchState(token, 0) if token is not None else None
        now = self.trials.now()
        return TrialSearchPage(
            hits=[trial_hit(study, now) for _, _, study in items[:limit]],
            total=total,
            next_state=next_state,
        )

    async def search_labels(self, drug: str, *, offset: int, limit: int) -> LabelSearchPage:
        """Every label naming the drug, newest first, `limit` at a time."""
        page = await self.labels.search(drug, skip=offset, limit=limit)
        end = offset + len(page.results)
        more = bool(page.results) and end < min(page.total, MAX_SKIP)
        now = self.labels.now()
        return LabelSearchPage(
            hits=[label_hit(result, now) for result in page.results],
            total=page.total,
            next_offset=end if more else None,
        )

    async def _hits(
        self, chosen: Sequence[Candidate], status: dict[str, SourceStatusValue]
    ) -> list[LiteratureHit]:
        """PMID-bearing hits are rendered from PubMed, the source of record for `pmid:`.

        If that fetch fails, PubMed's own hits are lost, so a searched PubMed becomes
        `unavailable`; Europe PMC hits only lose their handles, so it is `degraded` otherwise.
        """
        pmids: list[str] = []
        for candidate in chosen:
            kind, value = candidate.payload
            pmid = value if kind == "pmid" else value.get("pmid")
            if pmid:
                pmids.append(str(pmid))
        bundles: dict[str, RecordBundle] = {}
        if pmids and status.get("pubmed") != "unavailable":
            outcome = await self._within(STEP_SHARE, partial(self.pubmed.fetch, pmids))
            if outcome is not None:
                bundles = outcome.bundles
            else:
                status["pubmed"] = "unavailable" if "pubmed" in status else "degraded"
        hits: list[LiteratureHit] = []
        for candidate in chosen:
            kind, value = candidate.payload
            if kind == "pmid":
                bundle = bundles.get(value)
                if bundle is not None:
                    hits.append(await self._hit(bundle, mint_title=True))
                continue
            result: dict[str, Any] = value
            pmid = result.get("pmid")
            if pmid and str(pmid) in bundles:
                hit = await self._hit(bundles[str(pmid)], mint_title=True)
                # PubMed does not say whether full text is open; Europe PMC does.
                epmc_record = parse_result(result, self.europepmc.now()).record
                oa = epmc_record.quality.open_access_full_text
                hits.append(hit.model_copy(update={"open_access_full_text": oa}))
                continue
            bundle = parse_result(result, self.europepmc.now())
            # A pmid: hit is only citable with PubMed's text; without it, no handle.
            mint = not record_id_for(result).startswith("pmid:")
            hits.append(await self._hit(bundle, mint_title=mint))
        return hits

    async def _load(
        self, refs: Sequence[RecordRef], *, full_text: bool, share: float = SOURCE_SHARE
    ) -> _Loaded:
        """Records from every source involved, side by side, each within `share` of the
        deadline."""
        loaded = _Loaded()
        budget = self.deadline_s * share
        loaders: dict[str, Callable[[list[str]], Awaitable[FetchOutcome]]] = {
            "pubmed": self.pubmed.fetch,
            "europepmc": partial(self.europepmc.load, full_text=full_text),
            "clinicaltrials": self.trials.load,
            "openfda": self.labels.load,
        }

        def fail(source: str, group: Sequence[RecordRef], reason: str) -> None:
            loaded.status[source] = "unavailable"
            loaded.failed.update({r.key: reason for r in group})

        async def from_source(source: str, group: list[RecordRef]) -> None:
            with anyio.move_on_after(budget) as scope:
                try:
                    outcome = await loaders[source]([r.id for r in group])
                except UPSTREAM_FAILURES as exc:
                    fail(source, group, str(exc))
                    return
                loaded.status[source] = "ok"
                if isinstance(outcome, LoadOutcome) and outcome.full_text_failed:
                    loaded.status[source] = "degraded"
                    loaded.no_full_text.update(f"pmcid:{i}" for i in outcome.full_text_failed)
                for ref in group:
                    bundle = outcome.bundles.get(ref.id)
                    if bundle is None:
                        loaded.missing.append(ref.key)
                    else:
                        loaded.bundles[ref.key] = bundle
            if scope.cancelled_caught:
                fail(source, group, f"no answer from {SOURCE_NAMES[source]} within {budget:g}s")

        groups: dict[str, list[RecordRef]] = {}
        for ref in refs:
            groups.setdefault(SCHEME_SOURCES[ref.scheme], []).append(ref)
        await _concurrently(*(partial(from_source, s, g) for s, g in groups.items()))
        return loaded

    async def _with_retractions(
        self, bundles: dict[str, RecordBundle], status: dict[str, SourceStatusValue]
    ) -> None:
        """Merge Crossref's retraction status into every record that has a DOI (in place).

        DOIs are looked up side by side; the answers that arrive within the step's share
        of the deadline are merged, and Crossref is `degraded` if any is missing.
        """
        dois = sorted({b.record.ids.doi for b in bundles.values() if b.record.ids.doi})
        if not dois:
            return
        found: dict[str, Retraction] = {}

        async def lookup(doi: str) -> None:
            with contextlib.suppress(*UPSTREAM_FAILURES):
                found[doi] = await self.crossref.retraction(doi)

        with anyio.move_on_after(self.deadline_s * STEP_SHARE):
            await _concurrently(*(partial(lookup, doi) for doi in dois))
        status["crossref"] = "ok" if len(found) == len(dois) else "degraded"
        for key, bundle in bundles.items():
            doi = bundle.record.ids.doi
            if doi in found:
                bundles[key] = replace(bundle, record=merge_retraction(bundle.record, found[doi]))

    async def fetch_passages(
        self,
        record_ids: Sequence[str],
        *,
        sections: Sequence[str] | None,
        max_passages: int,
        offset: int,
        full_text: bool = False,
        include_entities: bool = True,
    ) -> FetchPage:
        refs = list(dict.fromkeys(_record_ref(record_id) for record_id in record_ids))
        loaded = await self._load(refs, full_text=full_text)
        companions: dict[str, str] = {}  # pmid key -> pmcid key holding its full text
        if full_text:
            requested = {r.key.lower() for r in refs}
            extra = [
                RecordRef("pmcid", b.record.ids.pmcid)
                for k, b in loaded.bundles.items()
                if k.startswith("pmid:")
                and b.record.ids.pmcid
                and f"pmcid:{b.record.ids.pmcid}".lower() not in requested
            ]
            if extra:
                more = await self._load(extra, full_text=True, share=STEP_SHARE)
                for name, value in more.status.items():
                    # A missing companion only costs full text, not the requested record.
                    value = "degraded" if value == "unavailable" else value
                    loaded.status[name] = _worse(loaded.status.get(name), value)
                for key, bundle in list(loaded.bundles.items()):
                    pmcid = bundle.record.ids.pmcid
                    companion = more.bundles.get(f"pmcid:{pmcid}") if pmcid else None
                    if not key.startswith("pmid:") or companion is None:
                        continue
                    # PubMed does not say whether full text is open; Europe PMC does.
                    oa = companion.record.quality.open_access_full_text
                    quality = bundle.record.quality.model_copy(update={"open_access_full_text": oa})
                    record = bundle.record.model_copy(update={"quality": quality})
                    loaded.bundles[key] = replace(bundle, record=record)
                    if any(d.path.startswith("body.") for d in companion.passages):
                        companions[key] = f"pmcid:{pmcid}"
                        loaded.bundles[f"pmcid:{pmcid}"] = companion
        if not loaded.bundles and loaded.failed:
            raise UpstreamUnavailableError("no source answered for the requested records")
        if include_entities:
            await self._with_entities(loaded.bundles, loaded.status)
        await self._with_retractions(loaded.bundles, loaded.status)

        ordered: list[RecordBundle] = []
        for ref in refs:
            bundle = loaded.bundles.get(ref.key)
            if bundle is None:
                continue
            ordered.append(bundle)
            if ref.key in companions:
                ordered.append(loaded.bundles[companions[ref.key]])
        flat = [(b, d) for b in ordered for d in b.passages if _matches(d.path, sections)]
        page = flat[offset : offset + max_passages]
        records: list[RecordPassages] = []
        for bundle in ordered:
            drafts = [d for b, d in page if b is bundle]
            has_any = any(b is bundle for b, _ in flat)
            if drafts or (offset == 0 and not has_any):
                passages = await self._issue(bundle.record, drafts)
                records.append(RecordPassages(record=bundle.record, passages=passages))
        end = offset + len(page)
        position = {r.key: i for i, r in enumerate(refs)}
        not_found = sorted(loaded.missing, key=lambda key: position.get(key, len(position)))
        return FetchPage(
            records=records,
            not_found=not_found if offset == 0 else [],
            unavailable=sorted(loaded.failed) if offset == 0 else [],
            next_offset=end if end < len(flat) else None,
            source_status=loaded.status,
        )

    async def _with_entities(
        self, bundles: dict[str, RecordBundle], status: dict[str, SourceStatusValue]
    ) -> None:
        """PubTator3 entities on PubMed title and abstract passages (in place)."""
        pmids = [
            b.record.ids.pmid
            for k, b in bundles.items()
            if k.startswith("pmid:") and b.record.ids.pmid
        ]
        if not pmids:
            return
        documents = await self._within(STEP_SHARE, partial(self.pubtator.documents, pmids))
        if documents is None:
            status["pubtator"] = "degraded"
            return
        status["pubtator"] = "ok"
        for key, bundle in bundles.items():
            if key.startswith("pmid:") and bundle.record.ids.pmid:
                document = documents.get(bundle.record.ids.pmid)
                bundles[key] = replace(bundle, passages=attach_entities(bundle.passages, document))

    async def resolve(self, locators: Sequence[Locator]) -> Mapping[Locator, Resolution]:
        """PassageResolver for the audit: current text, plus the text as issued when cached."""
        results: dict[Locator, Resolution] = {}
        supported = [loc for loc in locators if loc.scheme in SUPPORTED_SCHEMES]
        for locator in locators:
            if locator.scheme not in SUPPORTED_SCHEMES:
                results[locator] = Unavailable(f"scheme {locator.scheme} is not supported yet")
        if not supported:
            return results
        refs = list(dict.fromkeys(RecordRef(loc.scheme, loc.id) for loc in supported))
        full_text = any(loc.path.startswith("body.") for loc in supported)
        loaded = await self._load(refs, full_text=full_text)
        await self._with_retractions(loaded.bundles, loaded.status)
        issued = await self.cache.get_issued([str(loc) for loc in supported])
        for locator in supported:
            key = RecordRef(locator.scheme, locator.id).key
            if key in loaded.failed:
                results[locator] = Unavailable(loaded.failed[key])
                continue
            bundle = loaded.bundles.get(key)
            if bundle is None:
                results[locator] = Withdrawn("record not found upstream")
                continue
            draft = next((d for d in bundle.passages if d.path == locator.path), None)
            if draft is None and key in loaded.no_full_text and locator.path.startswith("body."):
                results[locator] = Unavailable("full text could not be fetched")
                continue
            if draft is None:
                results[locator] = Withdrawn(f"passage {locator.path} not found in record")
                continue
            passage = Passage(
                handle=self.codec.issue(locator),
                record_id=bundle.record.record_id,
                path=draft.path,
                section_label=draft.section_label,
                text=draft.text,
            )
            row = issued.get(str(locator))
            results[locator] = Resolved(
                passage=passage, record=bundle.record, issued_text=row.text if row else None
            )
        return results
