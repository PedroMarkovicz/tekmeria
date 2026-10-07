"""Routes requests to adapters, mints handles, and resolves handles for the audit."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from medcite.contract.audit import Resolution, Resolved, Unavailable, Withdrawn
from medcite.contract.handles import HandleCodec, Locator, RecordRef, parse_record_id
from medcite.contract.models import Passage, Record
from medcite.contract.tool_models import LiteratureHit, RecordPassages
from medcite.infra.cache import Cache
from medcite.infra.http import UpstreamResponseError, UpstreamUnavailableError
from medcite.sources.base import RecordBundle
from medcite.sources.errors import InvalidInputError
from medcite.sources.pubmed import LiteratureQuery, PubMedAdapter

SUPPORTED_SCHEMES = ("pmid",)
PREVIEW_CHARS = 300


@dataclass(frozen=True)
class SearchPage:
    hits: list[LiteratureHit]
    total: int
    next_offset: int | None


@dataclass(frozen=True)
class FetchPage:
    records: list[RecordPassages]
    not_found: list[str]
    next_offset: int | None


def _matches(path: str, prefixes: Sequence[str] | None) -> bool:
    if not prefixes:
        return True
    return any(path == prefix or path.startswith(prefix + ".") for prefix in prefixes)


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
    return ref


class EvidenceService:
    def __init__(self, *, codec: HandleCodec, cache: Cache, pubmed: PubMedAdapter) -> None:
        self.codec = codec
        self.cache = cache
        self.pubmed = pubmed

    async def _issue(
        self, record: Record, drafts: Sequence[tuple[str, str, str | None]]
    ) -> list[Passage]:
        """Mint handles for (path, text, section_label) drafts and remember the text as issued."""
        ref = parse_record_id(record.record_id)
        passages: list[Passage] = []
        rows: list[tuple[str, str, str | None]] = []
        for path, text, label in drafts:
            locator = ref.at(path)
            passages.append(
                Passage(
                    handle=self.codec.issue(locator),
                    record_id=record.record_id,
                    path=path,
                    section_label=label,
                    text=text,
                )
            )
            rows.append((str(locator), text, record.provenance.upstream_version))
        await self.cache.record_issued(rows)
        return passages

    async def search_literature(
        self, query: LiteratureQuery, *, offset: int, limit: int
    ) -> SearchPage:
        total, pmids = await self.pubmed.search(query, offset=offset, limit=limit)
        outcome = await self.pubmed.fetch(pmids)
        hits: list[LiteratureHit] = []
        for pmid in pmids:
            bundle = outcome.bundles.get(pmid)
            if bundle is None:
                continue
            record = bundle.record
            title = next((d for d in bundle.passages if d.path == "title"), None)
            first_abstract = next(
                (d for d in bundle.passages if d.path.startswith("abstract.")), None
            )
            title_handle = None
            if title is not None:
                (issued,) = await self._issue(record, [(title.path, title.text, None)])
                title_handle = issued.handle
            hits.append(
                LiteratureHit(
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
            )
        end = offset + len(pmids)
        return SearchPage(
            hits=hits, total=total, next_offset=end if pmids and end < total else None
        )

    async def _bundles(
        self, refs: Sequence[RecordRef]
    ) -> tuple[dict[str, RecordBundle], list[str]]:
        outcome = await self.pubmed.fetch([ref.id for ref in refs])
        return outcome.bundles, [f"pmid:{pmid}" for pmid in outcome.missing]

    async def fetch_passages(
        self,
        record_ids: Sequence[str],
        *,
        sections: Sequence[str] | None,
        max_passages: int,
        offset: int,
    ) -> FetchPage:
        refs = list(dict.fromkeys(_record_ref(record_id) for record_id in record_ids))
        bundles, not_found = await self._bundles(refs)
        ordered = [bundles[ref.id] for ref in refs if ref.id in bundles]
        flat = [
            (bundle, draft)
            for bundle in ordered
            for draft in bundle.passages
            if _matches(draft.path, sections)
        ]
        page = flat[offset : offset + max_passages]
        records: list[RecordPassages] = []
        for bundle in ordered:
            drafts = [(d.path, d.text, d.section_label) for b, d in page if b is bundle]
            has_any = any(b is bundle for b, _ in flat)
            if drafts or (offset == 0 and not has_any):
                records.append(
                    RecordPassages(
                        record=bundle.record, passages=await self._issue(bundle.record, drafts)
                    )
                )
        end = offset + len(page)
        return FetchPage(
            records=records,
            not_found=not_found if offset == 0 else [],
            next_offset=end if end < len(flat) else None,
        )

    async def resolve(self, locators: Sequence[Locator]) -> Mapping[Locator, Resolution]:
        """PassageResolver for the audit: current text, plus the text as issued when cached."""
        results: dict[Locator, Resolution] = {}
        pubmed_locators = [loc for loc in locators if loc.scheme == "pmid"]
        for locator in locators:
            if locator.scheme != "pmid":
                results[locator] = Unavailable(f"scheme {locator.scheme} is not supported yet")
        if not pubmed_locators:
            return results
        try:
            outcome = await self.pubmed.fetch([loc.id for loc in pubmed_locators])
        except (UpstreamUnavailableError, UpstreamResponseError) as exc:
            for locator in pubmed_locators:
                results[locator] = Unavailable(str(exc))
            return results
        issued = await self.cache.get_issued([str(loc) for loc in pubmed_locators])
        for locator in pubmed_locators:
            bundle = outcome.bundles.get(locator.id)
            if bundle is None:
                results[locator] = Withdrawn("record not found upstream")
                continue
            draft = next((d for d in bundle.passages if d.path == locator.path), None)
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
