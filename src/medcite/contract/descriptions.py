"""Human-readable contract text: served by describe_contract and the cite_by_handle prompt."""

from medcite.contract import CONTRACT_VERSION
from medcite.contract.audit_models import FLAG_NAMES, VERDICTS
from medcite.contract.handles import HANDLE_PATTERN, SCHEMES
from medcite.contract.tool_models import ContractDescription, TermInfo

VERDICT_MEANINGS: dict[str, str] = {
    "malformed": "The handle does not match the grammar.",
    "forged": "The check code is wrong: the server never issued this handle (fabricated).",
    "unavailable": "Genuine handle, but the upstream source could not be reached. Retryable; "
    "never counted as fabrication.",
    "withdrawn": "The record, or the passage path within it, no longer exists upstream.",
    "quote_mismatch": "The quote is not a verbatim substring of the passage after normalization.",
    "ok": "Every check passed.",
}

FLAG_MEANINGS: dict[str, str] = {
    "retracted": "The cited record has been retracted; detail is the notice DOI when known.",
    "preprint": "The cited record is not peer-reviewed.",
    "version_changed": "The upstream's current version differs from the version in the handle.",
}

METRIC_MEANINGS: dict[str, str] = {
    "citation_validity": "ok / (citations - unavailable); null when the denominator is 0.",
    "sentence_coverage": "cited sentences / sentences; null when there are no sentences.",
    "quote_accuracy": "verbatim quotes / quotes checked; null when no quote was checked.",
    "fabricated": "Number of forged citations.",
    "retracted_cited": "Number of ok citations whose record is retracted.",
}

PASSAGE_PATHS: dict[str, str] = {
    "title": "Article title.",
    "abstract.N": "Nth non-empty abstract section, in document order (1-based).",
    "body.<section>[.<subsection>].pN": "Nth paragraph of an open-access full-text section "
    "(pmcid: records only).",
}

CITE_BY_HANDLE_INSTRUCTIONS = """\
Answer only from passages you fetched with medcite tools in this conversation.
- After each sentence that makes a claim, cite 1-3 handles in square brackets: [handle; handle].
- Handles look like pmid:30831578/abstract.7~7hq3k. Copy them exactly; never invent or edit one.
- For specific numbers, doses or outcomes, add a verbatim quote: [handle "exact words"].
- If no fetched passage supports a sentence, say so instead of citing.
- Before answering, you may call audit_answer on your draft and fix every non-ok citation.
"""


def contract_description() -> ContractDescription:
    def terms(meanings: dict[str, str], order: tuple[str, ...]) -> list[TermInfo]:
        return [TermInfo(name=name, meaning=meanings[name]) for name in order]

    return ContractDescription(
        contract_version=CONTRACT_VERSION,
        handle_pattern=HANDLE_PATTERN,
        schemes=list(SCHEMES),
        passage_paths=terms(PASSAGE_PATHS, tuple(PASSAGE_PATHS)),
        verdicts=terms(VERDICT_MEANINGS, VERDICTS),
        flags=terms(FLAG_MEANINGS, FLAG_NAMES),
        metrics=terms(METRIC_MEANINGS, tuple(METRIC_MEANINGS)),
        instructions=CITE_BY_HANDLE_INSTRUCTIONS,
    )
