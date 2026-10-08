"""Whole answers: several planted cases in one multi-sentence answer (spec section 16.11).

A whole answer mixes planted cases with the layout a model writes: a Markdown heading, bold
lead-ins, uncited sentences, and citations before or after the full stop. Its expected result
is derived from the planted citations by the oracle's reading of the contract (sections 7 to
9): the sentences and their statuses, every citation's verdict and flags, the metrics and the
overall verdict.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass

from tekmerion.contract.audit_models import FlagName, Verdict
from tekmerion.evaluation import oracle
from tekmerion.evaluation.cases import Case, Planted, after_full_stop, render_group

ANSWER_SIZE = 4  # cases per mixed answer
CLEAN_CATEGORIES = frozenset({"ok_plain", "ok_quote", "ok_quote_reformatted"})


@dataclass(frozen=True)
class Answer:
    kind: str  # "mixed", "clean" or "paired"
    text: str
    sentences: tuple[tuple[Planted, ...], ...]  # the citations of each sentence, in order


def _lines(cases: Sequence[Case], rng: random.Random, *, layout: bool, kind: str) -> Answer:
    lines: list[str] = []
    sentences: list[tuple[Planted, ...]] = []
    if layout and rng.random() < 0.5:
        lines.append("## Findings")
        sentences.append(())  # a heading is a sentence of its own, uncited
    for number, case in enumerate(cases, start=1):
        group = render_group(case, rng)
        claim = f"The source reports finding {number}"
        prefix = ""
        if layout and rng.random() < 0.3:
            prefix = f"**Point {number}.** "
            sentences.append(())  # a bold lead-in is a sentence of its own, uncited
        if after_full_stop(group, rng):
            lines.append(f"{prefix}{claim}. {group}")
        else:
            lines.append(f"{prefix}{claim} {group}.")
        sentences.append(case.citations)
    if layout and rng.random() < 0.5:
        lines.append("Further studies are needed.")
        sentences.append(())
    return Answer(kind=kind, text="\n".join(lines), sentences=tuple(sentences))


def compose_answers(cases: Sequence[Case], seed: int) -> list[Answer]:
    """Three kinds of answer, from the cases that can be written as text:
    - mixed: every case goes into one, four at a time, with Markdown layout;
    - clean: genuine citations only, without layout, so the `pass` verdict is reached;
    - paired: each category's first case between two genuine citations, without layout, so
      every category's effect on the overall verdict is scored on its own."""
    rng = random.Random(f"answers:{seed}")
    text_cases = [case for case in cases if "text" in case.modes]
    order = rng.sample(text_cases, len(text_cases))
    answers = [
        _lines(order[i : i + ANSWER_SIZE], rng, layout=True, kind="mixed")
        for i in range(0, len(order), ANSWER_SIZE)
    ]
    clean = [case for case in text_cases if case.category in CLEAN_CATEGORIES]
    clean = rng.sample(clean, len(clean))
    answers += [
        _lines(clean[i : i + 3], rng, layout=False, kind="clean")
        for i in range(0, len(clean) - 2, 3)
    ]
    genuine = [case for case in text_cases if case.category == "ok_plain"]
    firsts: dict[str, Case] = {}
    for case in text_cases:
        firsts.setdefault(case.category, case)
    if genuine:
        answers += [
            _lines([genuine[0], case, genuine[-1]], rng, layout=False, kind="paired")
            for case in firsts.values()
        ]
    return answers


@dataclass(frozen=True)
class Expected:
    statuses: list[str]
    citations: list[tuple[Verdict, list[FlagName]]]  # (verdict, sorted flags) in answer order
    verdict: str
    metrics: dict[str, float | int | None]


def expected_result(answer: Answer) -> Expected:
    statuses = [oracle.claim_status([p.verdict for p in cited]) for cited in answer.sentences]
    planted = [p for cited in answer.sentences for p in cited]
    citations: list[tuple[Verdict, list[FlagName]]] = [
        (p.verdict, sorted(p.flags)) for p in planted
    ]
    facts = [(p.verdict, p.quote is not None, "retracted" in p.flags) for p in planted]
    retracted_ok = sum(1 for verdict, _, retracted in facts if verdict == "ok" and retracted)
    return Expected(
        statuses=statuses,
        citations=citations,
        verdict=oracle.overall_verdict(statuses, [p.verdict for p in planted], retracted_ok),
        metrics=oracle.metrics(statuses, facts),
    )
