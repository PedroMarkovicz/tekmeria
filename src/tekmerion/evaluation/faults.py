"""Injected faults: each breaks one decision of the audit as a real bug would (spec section
16.12). The evaluation must notice every fault that changes what the audit decides on the
snapshot; a fault it cannot notice is reported, never hidden.

    python -m tekmerion.evaluation.faults [--seed N] [--per-category N]

prints, for each fault, how many planted cases and whole answers still come out as planted.
"""

import argparse
import re
import string
import sys
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import anyio

from tekmerion.contract import audit as audit_module
from tekmerion.contract.audit_models import AuditSummary, CitationResult, ClaimResult
from tekmerion.contract.handles import HandleCodec, Locator
from tekmerion.evaluation import oracle
from tekmerion.evaluation.run import evaluate_snapshot
from tekmerion.evaluation.snapshot import Snapshot

Comparator = Callable[[str, str], bool]
_PUNCTUATION = str.maketrans("", "", string.punctuation)
_SPACE = re.compile(r"\s")
_ANY_OTHER = chr(0xFFFD)  # stands for a character the comparator no longer folds
# Whitespace normalization already reads a no-break space as a space, so deleting its fold
# entry changes nothing; its fault is a comparator that does not read it as whitespace.
_NO_BREAK_SPACE = chr(0x00A0)


@dataclass(frozen=True)
class Fault:
    name: str
    target: tuple[Any, str]  # the object and attribute the fault replaces
    make: Callable[[Any], Any]  # the real attribute -> the faulty one


def _comparator(name: str, change: Callable[[Comparator, str, str], bool]) -> Fault:
    def make(real: Comparator) -> Comparator:
        return lambda quote, passage: change(real, quote, passage)

    return Fault(name, (audit_module, "quote_in_passage"), make)


def _unfolded(char: str, name: str | None = None) -> Fault:
    """A comparator for which `char` no longer matches its plain form."""

    def change(real: Comparator, quote: str, passage: str) -> bool:
        return real(quote.replace(char, _ANY_OTHER), passage.replace(char, _ANY_OTHER))

    return _comparator(name or f"no fold for U+{ord(char):04X}", change)


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _summary_fault(name: str, change: Callable[[AuditSummary], dict[str, Any]]) -> Fault:
    def make(real: Callable[[Sequence[ClaimResult]], AuditSummary]) -> Any:
        def faulty(claims: Sequence[ClaimResult]) -> AuditSummary:
            summary = real(claims)
            return summary.model_copy(update=change(summary))

        return faulty

    return Fault(name, (audit_module, "summarize"), make)


def _never_warns(real: Callable[..., str]) -> Callable[..., str]:
    def faulty(summary: AuditSummary, fail_on: Sequence[str]) -> str:
        verdict = real(summary, fail_on)
        return "pass" if verdict == "warn" else verdict

    return faulty


def _earlier_warn_rule(*, invalid_only: bool) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """An earlier rule for `warn`: before section 16.13 only uncited sentences and retracted
    sources warned; before 16.14 invalid_only sentences did too, but an invalid citation beside
    a valid one did not."""

    def make(real: Callable[..., str]) -> Callable[..., str]:
        def faulty(summary: AuditSummary, fail_on: Sequence[str]) -> str:
            verdict = real(summary, fail_on)
            reasons = [summary.uncited_sentences, summary.retracted_cited]
            if invalid_only:
                reasons.append(summary.invalid_only_sentences)
            return "pass" if verdict == "warn" and not any(reasons) else verdict

        return faulty

    return make


def _invalid_as_uncited(real: Callable[[Sequence[CitationResult]], str]) -> Any:
    def faulty(citations: Sequence[CitationResult]) -> str:
        status = real(citations)
        return "uncited" if status == "invalid_only" else status

    return faulty


def _path_blind(real: Callable[[HandleCodec, Locator], str]) -> Any:
    return lambda codec, locator: real(codec, locator.record.at("title"))


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


FAULTS: tuple[Fault, ...] = (
    _comparator("quote compared on its first 40 characters", lambda r, q, p: r(q[:40], p)),
    _comparator("comparator ignores letter case", lambda r, q, p: r(q.lower(), p.lower())),
    _comparator(
        "comparator ignores punctuation",
        lambda r, q, p: r(q.translate(_PUNCTUATION), p.translate(_PUNCTUATION)),
    ),
    _comparator(
        "comparator uses NFKC",
        lambda r, q, p: r(unicodedata.normalize("NFKC", q), unicodedata.normalize("NFKC", p)),
    ),
    _comparator(
        "comparator ignores accents", lambda r, q, p: r(_strip_accents(q), _strip_accents(p))
    ),
    _comparator(
        "comparator ignores whitespace", lambda r, q, p: r(_SPACE.sub("", q), _SPACE.sub("", p))
    ),
    *(_unfolded(char) for char in oracle.FOLDS if char != _NO_BREAK_SPACE),
    _unfolded(_NO_BREAK_SPACE, "no-break space not read as whitespace"),
    *(_unfolded(char, f"no removal of U+{ord(char):04X}") for char in sorted(oracle.REMOVED)),
    Fault("check code ignores the path", (HandleCodec, "check_code"), _path_blind),
    Fault("flags dropped", (audit_module, "_flags"), lambda real: lambda locator, record: []),
    Fault("overall verdict never warns", (audit_module, "overall_verdict"), _never_warns),
    Fault(
        "invalid_only sentences do not warn",
        (audit_module, "overall_verdict"),
        _earlier_warn_rule(invalid_only=False),
    ),
    Fault("invalid_only read as uncited", (audit_module, "_claim_status"), _invalid_as_uncited),
    _summary_fault(
        "coverage = cited / (cited + invalid_only)",
        lambda s: {
            "sentence_coverage": _ratio(
                s.cited_sentences, s.cited_sentences + s.invalid_only_sentences
            )
        },
    ),
    _summary_fault(
        "validity = ok / all citations, unavailable included",
        lambda s: {"citation_validity": _ratio(s.by_verdict["ok"], s.citations)},
    ),
)


# Faults the snapshot cannot show, with the reason. They are reported, and they alone may go
# uncaught.
NOT_EXERCISED = {"no fold for U+2026": "no passage of the snapshot contains an ellipsis"}


@contextmanager
def injected(fault: Fault) -> Iterator[None]:
    owner, attribute = fault.target
    real = getattr(owner, attribute)
    setattr(owner, attribute, fault.make(real))
    try:
        yield
    finally:
        setattr(owner, attribute, real)


@dataclass(frozen=True)
class Row:
    fault: str
    cases: int
    cases_correct: int
    answers: int
    answers_correct: int

    @property
    def caught(self) -> bool:
        return self.cases_correct < self.cases or self.answers_correct < self.answers


async def measure(
    snapshot: Snapshot, key: bytes, *, seed: int = 0, per_category: int = 25
) -> list[Row]:
    """The evaluation under each fault, after checking that it passes without any."""
    if not (await evaluate_snapshot(snapshot, key, seed=seed, per_category=per_category)).passed():
        raise RuntimeError("the evaluation fails without any fault; fix that first")
    rows = []
    for fault in FAULTS:
        with injected(fault):
            report = await evaluate_snapshot(snapshot, key, seed=seed, per_category=per_category)
        rows.append(
            Row(
                fault=fault.name,
                cases=report.cases,
                cases_correct=report.correct,
                answers=sum(a.answers for a in report.answers),
                answers_correct=sum(a.correct for a in report.answers),
            )
        )
    return rows


def passed(rows: Sequence[Row]) -> bool:
    """Every fault caught, except those the snapshot cannot show."""
    return all(row.caught or row.fault in NOT_EXERCISED for row in rows)


def render(rows: Sequence[Row]) -> str:
    caught = sum(row.caught for row in rows)
    lines = [
        f"injected faults: {caught}/{len(rows)} caught",
        "",
        "| Fault | Planted cases correct | Whole answers correct |",
        "|---|---|---|",
    ]
    for row in rows:
        reason = NOT_EXERCISED.get(row.fault, "a fault the evaluation misses")
        mark = "" if row.caught else f" (not caught: {reason})"
        lines.append(
            f"| {row.fault}{mark} | {row.cases_correct}/{row.cases} | "
            f"{row.answers_correct}/{row.answers} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tekmerion.evaluation.faults")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--per-category", type=int, default=25)
    args = parser.parse_args(argv)

    from tekmerion.__main__ import demo_config  # the demo's offline runtime
    from tekmerion.evaluation.snapshot import load_snapshot
    from tekmerion.server.runtime import open_runtime

    async def run() -> list[Row]:
        config = demo_config()
        async with open_runtime(config) as app:
            snapshot = await load_snapshot(app.service)
        return await measure(
            snapshot, config.handle_key, seed=args.seed, per_category=args.per_category
        )

    rows = anyio.run(run)
    print(render(rows))
    sys.exit(0 if passed(rows) else 1)


if __name__ == "__main__":
    main()
