"""The deterministic citation audit (spec section 7)."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from medcite.contract import CONTRACT_VERSION
from medcite.contract.audit_models import (
    DEFAULT_FAIL_ON,
    VERDICTS,
    AuditReport,
    AuditSummary,
    CitationIn,
    CitationResult,
    ClaimIn,
    ClaimResult,
    ClaimStatus,
    FailOn,
    Flag,
    HandleCheck,
    OverallVerdict,
    Verdict,
)
from medcite.contract.handles import ForgedHandleError, HandleCodec, Locator, MalformedHandleError
from medcite.contract.models import Passage, Record
from medcite.contract.normalize import closest_match, quote_in_passage


@dataclass(frozen=True)
class Resolved:
    """The cited passage as it exists now, plus the text as issued if cached."""

    passage: Passage
    record: Record
    issued_text: str | None = None


@dataclass(frozen=True)
class Unavailable:
    reason: str


@dataclass(frozen=True)
class Withdrawn:
    reason: str


Resolution = Resolved | Unavailable | Withdrawn


class PassageResolver(Protocol):
    async def resolve(self, locators: Sequence[Locator]) -> Mapping[Locator, Resolution]: ...


def _flags(locator: Locator, record: Record) -> list[Flag]:
    flags: list[Flag] = []
    retraction = record.quality.retraction
    if retraction.state == "known" and retraction.value is not None and retraction.value.retracted:
        flags.append(Flag(name="retracted", detail=retraction.value.notice_doi))
    peer = record.quality.peer_reviewed
    if record.kind == "preprint" or (peer.state == "known" and peer.value is False):
        flags.append(Flag(name="preprint"))
    current = record.provenance.upstream_version
    if locator.version is not None and current != locator.version:
        flags.append(Flag(name="version_changed", detail=current))
    return flags


async def _resolve_all(
    handles: Sequence[str], codec: HandleCodec, resolver: PassageResolver
) -> tuple[dict[str, Locator | Verdict], Mapping[Locator, Resolution]]:
    parsed: dict[str, Locator | Verdict] = {}
    for handle in handles:
        if handle in parsed:
            continue
        try:
            parsed[handle] = codec.verify(handle)
        except ForgedHandleError:
            parsed[handle] = "forged"
        except MalformedHandleError:
            parsed[handle] = "malformed"
    locators = sorted({value for value in parsed.values() if isinstance(value, Locator)})
    resolutions: Mapping[Locator, Resolution] = await resolver.resolve(locators) if locators else {}
    return parsed, resolutions


def _judge(
    citation: CitationIn,
    parsed: Locator | Verdict,
    resolutions: Mapping[Locator, Resolution],
) -> CitationResult:
    if not isinstance(parsed, Locator):
        return CitationResult(handle=citation.handle, quote=citation.quote, verdict=parsed)
    resolution = resolutions.get(parsed, Unavailable("not resolved"))
    if isinstance(resolution, Unavailable):
        return CitationResult(handle=citation.handle, quote=citation.quote, verdict="unavailable")
    if isinstance(resolution, Withdrawn):
        return CitationResult(handle=citation.handle, quote=citation.quote, verdict="withdrawn")
    issued = resolution.issued_text
    text = issued if issued is not None else resolution.passage.text
    result = CitationResult(
        handle=citation.handle,
        quote=citation.quote,
        verdict="ok",
        flags=_flags(parsed, resolution.record),
        checked_against="issued" if issued is not None else "current",
        resolved_version=resolution.record.provenance.upstream_version,
        passage=resolution.passage,
    )
    if citation.quote is not None and not quote_in_passage(citation.quote, text):
        window, similarity = closest_match(citation.quote, text)
        result = result.model_copy(
            update={"verdict": "quote_mismatch", "closest_match": window, "similarity": similarity}
        )
    return result


def _claim_status(citations: Sequence[CitationResult]) -> ClaimStatus:
    if not citations:
        return "uncited"
    return "cited" if any(c.verdict == "ok" for c in citations) else "invalid_only"


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def summarize(claims: Sequence[ClaimResult]) -> AuditSummary:
    citations = [c for claim in claims for c in claim.citations]
    by_verdict: dict[Verdict, int] = dict.fromkeys(VERDICTS, 0)
    for citation in citations:
        by_verdict[citation.verdict] += 1
    statuses = [claim.status for claim in claims]
    quoted = [c for c in citations if c.quote is not None and c.verdict in ("ok", "quote_mismatch")]
    quotes_ok = sum(1 for c in quoted if c.verdict == "ok")
    cited = statuses.count("cited")
    return AuditSummary(
        sentences=len(claims),
        cited_sentences=cited,
        uncited_sentences=statuses.count("uncited"),
        invalid_only_sentences=statuses.count("invalid_only"),
        citations=len(citations),
        by_verdict=by_verdict,
        quotes_checked=len(quoted),
        quotes_ok=quotes_ok,
        citation_validity=_ratio(by_verdict["ok"], len(citations) - by_verdict["unavailable"]),
        sentence_coverage=_ratio(cited, len(claims)),
        quote_accuracy=_ratio(quotes_ok, len(quoted)),
        fabricated=by_verdict["forged"],
        retracted_cited=sum(
            1
            for c in citations
            if c.verdict == "ok" and any(flag.name == "retracted" for flag in c.flags)
        ),
    )


def overall_verdict(summary: AuditSummary, fail_on: Sequence[FailOn]) -> OverallVerdict:
    events: set[str] = {verdict for verdict, count in summary.by_verdict.items() if count}
    if summary.uncited_sentences:
        events.add("uncited")
    if summary.retracted_cited:
        events.add("retracted")
    if events & set(fail_on):
        return "fail"
    if summary.by_verdict["unavailable"]:
        return "incomplete"
    if summary.uncited_sentences or summary.retracted_cited:
        return "warn"
    return "pass"


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def audit(
    claims: Sequence[ClaimIn],
    *,
    codec: HandleCodec,
    resolver: PassageResolver,
    fail_on: Sequence[FailOn] | None = None,
    now: Callable[[], datetime] = _utc_now,
) -> AuditReport:
    handles = [citation.handle for claim in claims for citation in claim.citations]
    parsed, resolutions = await _resolve_all(handles, codec, resolver)
    results: list[ClaimResult] = []
    for index, claim in enumerate(claims):
        judged = [_judge(c, parsed[c.handle], resolutions) for c in claim.citations]
        results.append(
            ClaimResult(
                index=index, sentence=claim.sentence, status=_claim_status(judged), citations=judged
            )
        )
    summary = summarize(results)
    effective_fail_on = list(fail_on) if fail_on is not None else list(DEFAULT_FAIL_ON)
    return AuditReport(
        contract_version=CONTRACT_VERSION,
        audited_at=now(),
        claims=results,
        summary=summary,
        verdict=overall_verdict(summary, effective_fail_on),
        fail_on=effective_fail_on,
    )


async def check_handles(
    handles: Sequence[str], *, codec: HandleCodec, resolver: PassageResolver
) -> list[HandleCheck]:
    parsed, resolutions = await _resolve_all(handles, codec, resolver)
    checks: list[HandleCheck] = []
    for handle in handles:
        result = _judge(CitationIn(handle=handle), parsed[handle], resolutions)
        status = "ok" if result.verdict == "quote_mismatch" else result.verdict
        checks.append(
            HandleCheck(
                handle=handle,
                status=status,
                passage=result.passage,
                flags=result.flags,
                checked_against=result.checked_against,
                resolved_version=result.resolved_version,
            )
        )
    return checks
