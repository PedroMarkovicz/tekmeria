"""Compact text for clients that only pass text to the model: handle first, one line per item."""

from collections.abc import Mapping

from tekmerion.contract.audit_models import AuditReport, HandleCheck
from tekmerion.contract.models import Signal, SourceStatusValue, ToolError
from tekmerion.contract.tool_models import (
    ContractDescription,
    FetchResult,
    LabelSearchResult,
    LiteratureSearchResult,
    TrialSearchResult,
)


def _signal(signal: Signal[bool]) -> str:
    return "?" if signal.state != "known" else ("yes" if signal.value else "no")


def _status(status: Mapping[str, SourceStatusValue]) -> list[str]:
    """One line naming the sources that were not ok; none when all were."""
    not_ok = [f"{name}={value}" for name, value in status.items() if value != "ok"]
    return ["source_status: " + ", ".join(not_ok)] if not_ok else []


def render_search(result: LiteratureSearchResult) -> str:
    lines = [f"{len(result.hits)} of {result.total} results"]
    for hit in result.hits:
        designs = (
            ",".join(hit.study_designs.value or []) if hit.study_designs.state == "known" else "?"
        )
        retracted = hit.retraction.value is not None and hit.retraction.value.retracted
        date = hit.published.date if hit.published else "n.d."
        lines.append(
            f"{hit.title_handle or hit.record_id}  {hit.title} ({hit.venue or 'unknown venue'}, "
            f"{date}) designs={designs} peer_reviewed={_signal(hit.peer_reviewed)}"
            + ("  RETRACTED" if retracted else "")
            + ("" if hit.title_handle else "  (no handle: cite after fetch_passages)")
        )
    lines += _status(result.source_status)
    if result.next_cursor:
        lines.append(f"next_cursor: {result.next_cursor}")
    return "\n".join(lines)


def render_trials(result: TrialSearchResult) -> str:
    lines = [f"{len(result.hits)} of {result.total} trials"]
    for hit in result.hits:
        phases = "/".join(hit.phases) or "phase n/a"
        size = f"n={hit.enrollment}" if hit.enrollment is not None else "n=?"
        posted = "results posted" if hit.has_results else "no results posted"
        pmids = f"  results in: {', '.join(hit.linked_pmids)}" if hit.linked_pmids else ""
        lines.append(
            f"{hit.record_id}  {hit.title} ({hit.status or 'status unknown'}, {phases}, "
            f"{size}, {posted}){pmids}"
        )
    lines += _status(result.source_status)
    if result.next_cursor:
        lines.append(f"next_cursor: {result.next_cursor}")
    return "\n".join(lines)


def render_labels(result: LabelSearchResult) -> str:
    lines = [f"{len(result.hits)} of {result.total} labels"]
    for hit in result.hits:
        routes = ",".join(hit.routes) or "route n/a"
        lines.append(
            f"{hit.record_id}  {hit.title} ({hit.manufacturer or 'unknown manufacturer'}, "
            f"{hit.effective_date or 'n.d.'}, {routes})"
            + ("  BOXED WARNING" if hit.has_boxed_warning else "")
        )
    lines += _status(result.source_status)
    if result.next_cursor:
        lines.append(f"next_cursor: {result.next_cursor}")
    return "\n".join(lines)


def render_fetch(result: FetchResult) -> str:
    lines: list[str] = []
    for item in result.records:
        record = item.record
        retracted = (
            record.quality.retraction.value is not None
            and record.quality.retraction.value.retracted
        )
        lines.append(f"# {record.record_id}  {record.title}" + ("  RETRACTED" if retracted else ""))
        for passage in item.passages:
            label = f"[{passage.section_label}] " if passage.section_label else ""
            lines.append(f"{passage.handle}  {label}{passage.text}")
    if result.not_found:
        lines.append("not found: " + ", ".join(result.not_found))
    if result.unavailable:
        lines.append("source unavailable for: " + ", ".join(result.unavailable))
    lines += _status(result.source_status)
    if result.next_cursor:
        lines.append(f"next_cursor: {result.next_cursor}")
    return "\n".join(lines)


def render_checks(checks: list[HandleCheck]) -> str:
    lines: list[str] = []
    for check in checks:
        flags = "".join(f" [{flag.name}]" for flag in check.flags)
        text = f"  {check.passage.text}" if check.passage else ""
        lines.append(f"{check.handle}  {check.status}{flags}{text}")
    return "\n".join(lines)


def render_audit(report: AuditReport) -> str:
    s = report.summary
    counts = ", ".join(f"{count} {verdict}" for verdict, count in s.by_verdict.items() if count)
    lines = [
        f"verdict: {report.verdict.upper()}  ({s.citations} citations: {counts or 'none'})",
        f"fabricated: {s.fabricated}  retracted cited: {s.retracted_cited}  "
        f"cited sentences: {s.cited_sentences}/{s.sentences}  "
        f"verbatim quotes: {s.quotes_ok}/{s.quotes_checked}",
    ]
    for claim in report.claims:
        lines.append(f"[{claim.index + 1}] {claim.status}: {claim.sentence}")
        for citation in claim.citations:
            flags = "".join(f" [{flag.name}]" for flag in citation.flags)
            quote = f' "{citation.quote}"' if citation.quote else ""
            lines.append(f"    {citation.verdict:<14} {citation.handle}{quote}{flags}")
            if citation.closest_match is not None:
                lines.append(
                    f'    {"":<14} closest: "{citation.closest_match}" ({citation.similarity})'
                )
    return "\n".join(lines)


def render_contract(description: ContractDescription) -> str:
    verdicts = "\n".join(f"  {t.name}: {t.meaning}" for t in description.verdicts)
    return (
        f"tekmerion contract {description.contract_version}\n"
        f"handle pattern: {description.handle_pattern}\n"
        f"verdicts (first failure wins):\n{verdicts}\n\n{description.instructions}"
    )


def render_error(error: ToolError) -> str:
    hint = f"\nhint: {error.hint}" if error.hint else ""
    return f"error {error.code}: {error.message}{hint}"
