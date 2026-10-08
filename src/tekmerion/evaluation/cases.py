"""Planted cases: citations whose correct verdict is known before the audit runs (spec 16.10).

Every case is built from the snapshot and a seeded random generator, so the same seed always
yields the same cases. Each category plants one kind of error, or none. What a case plants is
decided by the reference oracle (`oracle.py`), never by the audit's own comparator or codec:
handles are issued, forged and checked by the oracle, and a planted mismatch is one the oracle
cannot find in the passage.
"""

import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from tekmerion.contract.audit_models import FlagName, Verdict
from tekmerion.contract.models import Passage, Record
from tekmerion.evaluation import oracle
from tekmerion.evaluation.snapshot import OFFLINE_RECORD, Snapshot, record_key

Mode = Literal["claims", "text"]
MODES: tuple[Mode, ...] = ("claims", "text")
# Quote marks never appear inside a planted quote, so every quote style can carry it.
_QUOTE_MARKS = frozenset("\"'" + chr(0x2018) + chr(0x2019) + chr(0x201C) + chr(0x201D))
_EN_DASH, _NBSP = chr(0x2013), chr(0x00A0)
_TRAILING_DIGITS = re.compile(r"(\d+)$")
# Redraws per case: a category that cannot be planted means a bug, so fail instead of looping.
_ATTEMPTS = 1000


@dataclass(frozen=True)
class Planted:
    """One citation of a case and the result the audit must give it."""

    handle: str
    quote: str | None
    verdict: Verdict
    flags: tuple[FlagName, ...] = ()


@dataclass(frozen=True)
class Case:
    category: str
    citations: tuple[Planted, ...]
    modes: tuple[Mode, ...] = MODES


def _locator(passage: Passage) -> str:
    """'pmid:30831578/abstract.7': the passage's record ID (with any version) and path."""
    return f"{passage.record_id}/{passage.path}"


class _Builder:
    def __init__(self, snapshot: Snapshot, key: bytes, rng: random.Random) -> None:
        self.key = key
        self.rng = rng
        pairs = snapshot.passages()
        self.clean = [(r, p) for r, p in pairs if not _flagged(r)]
        self.retracted = [(r, p) for r, p in pairs if _retracted(r)]
        self.preprints = [(r, p) for r, p in pairs if r.kind == "preprint"]
        self.versioned = [(r, p) for r, p in self.clean if r.provenance.upstream_version]
        self.quotable = [(r, p) for r, p in self.clean if _windows(p.text)]
        self.numeric = [
            (r, p) for r, p in self.quotable if any(_has_digit(w) for w in _windows(p.text))
        ]
        self.multi = [
            (r, p)
            for r, p in self.clean
            if len(snapshot.records[record_key(r.record_id)].passages) > 1
        ]
        self.numbered = [(r, p) for r, p in self.clean if _TRAILING_DIGITS.search(p.path)]
        self.records = snapshot.records

    def pick(self, pool: list[tuple[Record, Passage]]) -> tuple[Record, Passage]:
        return pool[self.rng.randrange(len(pool))]

    def window(self, text: str, *, digits: bool = False) -> str:
        options = [w for w in _windows(text) if _has_digit(w) or not digits]
        return options[self.rng.randrange(len(options))]

    def handle(self, passage: Passage) -> str:
        """The genuine handle, as issued by the oracle."""
        return oracle.issue(self.key, _locator(passage))

    def _draw(self, category: str, attempt: Callable[[], Case | None]) -> Case:
        """Call `attempt` until it plants a case. A category that can never be planted (for
        example, a check code that ignores the path) is a bug: fail fast, never loop."""
        for _ in range(_ATTEMPTS):
            case = attempt()
            if case is not None:
                return case
        raise RuntimeError(f"{category}: no case found in {_ATTEMPTS} draws")

    def _genuine(self, handle: str) -> bool:
        return oracle.is_genuine(self.key, handle)

    # Each method below returns one case of its category.

    def ok_plain(self) -> Case:
        _, passage = self.pick(self.clean)
        return Case("ok_plain", (Planted(self.handle(passage), None, "ok"),))

    def ok_quote(self) -> Case:
        _, passage = self.pick(self.quotable)
        quote = self.window(passage.text)
        return Case("ok_quote", (Planted(self.handle(passage), quote, "ok"),))

    def ok_quote_reformatted(self) -> Case:
        _, passage = self.pick(self.quotable)
        quote = self.window(passage.text)
        words = quote.split(" ")
        at = self.rng.randrange(1, len(words))
        quote = " ".join(words[:at]) + self.rng.choice(["  ", _NBSP]) + " ".join(words[at:])
        quote = quote.replace("-", _EN_DASH)  # typographic variants are folded before comparing
        return Case("ok_quote_reformatted", (Planted(self.handle(passage), quote, "ok"),))

    def _mismatch(self, category: str, edit: Callable[[str], str], *, digits: bool) -> Case:
        pool = self.numeric if digits else self.quotable

        def attempt() -> Case | None:
            _, passage = self.pick(pool)
            quote = edit(self.window(passage.text, digits=digits))
            if oracle.quote_matches(quote, passage.text):  # the edit recreated real text
                return None
            return Case(category, (Planted(self.handle(passage), quote, "quote_mismatch"),))

        return self._draw(category, attempt)

    def quote_number_changed(self) -> Case:
        def edit(quote: str) -> str:
            spots = [i for i, ch in enumerate(quote) if ch.isdigit()]
            at = self.rng.choice(spots)
            new = str((int(quote[at]) + self.rng.randrange(1, 10)) % 10)
            return quote[:at] + new + quote[at + 1 :]

        return self._mismatch("quote_number_changed", edit, digits=True)

    def quote_negated(self) -> Case:
        def edit(quote: str) -> str:
            words = quote.split(" ")
            at = self.rng.randrange(1, len(words))
            return " ".join([*words[:at], "not", *words[at:]])

        return self._mismatch("quote_negated", edit, digits=False)

    def quote_word_swapped(self) -> Case:
        def edit(quote: str) -> str:
            words = quote.split(" ")
            at = self.rng.randrange(len(words))
            _, other = self.pick(self.quotable)
            donors = [w for w in other.text.split(" ") if w.isalpha() and w != words[at]]
            words[at] = self.rng.choice(donors) if donors else words[at] + "s"
            return " ".join(words)

        return self._mismatch("quote_word_swapped", edit, digits=False)

    def forge_code(self, handle: str) -> str:
        body, code = handle[:-5], handle[-5:]
        at = self.rng.randrange(5)
        new = self.rng.choice([c for c in oracle.ALPHABET if c != code[at]])
        return body + code[:at] + new + code[at + 1 :]

    def forged_check_code(self) -> Case:
        _, passage = self.pick(self.clean)
        forged = self.forge_code(self.handle(passage))
        return Case("forged_check_code", (Planted(forged, None, "forged"),))

    def forged_path_swap(self) -> Case:
        def attempt() -> Case | None:
            record, passage = self.pick(self.multi)
            others = self.records[record_key(record.record_id)].passages
            other = self.rng.choice([p for p in others if p.path != passage.path])
            code = oracle.check_code(self.key, _locator(passage))
            moved = f"{_locator(other)}~{code}"  # the other passage, with this one's code
            if self._genuine(moved):  # a chance collision would be a genuine handle
                return None
            return Case("forged_path_swap", (Planted(moved, None, "forged"),))

        return self._draw("forged_path_swap", attempt)

    def forged_record_id(self) -> Case:
        def attempt() -> Case | None:
            _, passage = self.pick(self.clean)
            scheme, rest = passage.record_id.split(":", 1)
            ident, at_sign, version = rest.partition("@")
            at = max(i for i, ch in enumerate(ident) if ch.isdigit())
            changed = ident[:at] + str((int(ident[at]) + 1) % 10) + ident[at + 1 :]
            code = oracle.check_code(self.key, _locator(passage))
            moved = f"{scheme}:{changed}{at_sign}{version}/{passage.path}~{code}"
            if self._genuine(moved):
                return None
            return Case("forged_record_id", (Planted(moved, None, "forged"),))

        return self._draw("forged_record_id", attempt)

    def malformed(self) -> Case:
        _, passage = self.pick(self.clean)
        handle = self.handle(passage)
        code = handle[-5:]
        corruptions = [
            handle.replace("~", "-"),  # the check code joined with the wrong mark
            handle[:-1],  # a check code one character short
            handle[:-1] + "i",  # a letter the check-code alphabet leaves out
            f"{passage.record_id}~{code}",  # no passage path
            f"{passage.record_id}/{passage.path.upper()}~{code}",  # path in capitals
        ]
        broken = self.rng.choice(corruptions)
        if oracle.well_formed(broken):
            raise RuntimeError(f"malformed: {broken} is well formed")
        return Case("malformed", (Planted(broken, None, "malformed"),))

    def withdrawn_path(self) -> Case:
        record, passage = self.pick(self.numbered)
        paths = {p.path for p in self.records[record_key(record.record_id)].passages}

        def attempt() -> Case | None:
            path = _TRAILING_DIGITS.sub(str(self.rng.randrange(900, 1000)), passage.path)
            if path in paths:
                return None
            handle = oracle.issue(self.key, f"{passage.record_id}/{path}")
            return Case("withdrawn_path", (Planted(handle, None, "withdrawn"),))

        return self._draw("withdrawn_path", attempt)

    def withdrawn_record(self) -> Case:
        record = f"pmid:{90_000_000 + self.rng.randrange(9_000_000)}"
        handle = oracle.issue(self.key, f"{record}/abstract.{self.rng.randrange(1, 10)}")
        return Case("withdrawn_record", (Planted(handle, None, "withdrawn"),))

    def unavailable(self) -> Case:
        handle = oracle.issue(self.key, f"{OFFLINE_RECORD}/abstract.{self.rng.randrange(1, 10)}")
        return Case("unavailable", (Planted(handle, None, "unavailable"),))

    def retracted_cited(self) -> Case:
        _, passage = self.pick(self.retracted)
        planted = Planted(self.handle(passage), None, "ok", ("retracted",))
        return Case("retracted_cited", (planted,))

    def preprint_cited(self) -> Case:
        _, passage = self.pick(self.preprints)
        planted = Planted(self.handle(passage), None, "ok", ("preprint",))
        return Case("preprint_cited", (planted,))

    def version_changed(self) -> Case:
        record, passage = self.pick(self.versioned)
        current = record.provenance.upstream_version or ""
        old = f"2025-0{self.rng.randrange(1, 10)}-01" if "-" in current else str(int(current) - 1)
        locator = f"{record_key(record.record_id)}@{old}/{passage.path}"
        planted = Planted(oracle.issue(self.key, locator), None, "ok", ("version_changed",))
        return Case("version_changed", (planted,))

    def hidden_in_quote(self) -> Case:
        # An unclosed quote mark swallows the next handle; the audit must still find it.
        _, passage = self.pick(self.clean)
        _, other = self.pick(self.clean)
        hidden = self.forge_code(self.handle(other))
        quote = f"see; {hidden} 'here"
        cited = (
            Planted(self.handle(passage), quote, "quote_mismatch"),
            Planted(hidden, None, "forged"),
        )
        return Case("hidden_in_quote", cited, modes=("text",))


CATEGORIES: tuple[str, ...] = (
    "ok_plain",
    "ok_quote",
    "ok_quote_reformatted",
    "quote_number_changed",
    "quote_negated",
    "quote_word_swapped",
    "forged_check_code",
    "forged_path_swap",
    "forged_record_id",
    "malformed",
    "withdrawn_path",
    "withdrawn_record",
    "unavailable",
    "retracted_cited",
    "preprint_cited",
    "version_changed",
    "hidden_in_quote",
)


def generate_cases(snapshot: Snapshot, key: bytes, *, seed: int, per_category: int) -> list[Case]:
    """`per_category` cases of every category, the same ones for the same seed. `key` is the
    handle key the audit under test verifies with."""
    builder = _Builder(snapshot, key, random.Random(seed))
    return [getattr(builder, category)() for category in CATEGORIES for _ in range(per_category)]


def render_text(case: Case, rng: random.Random) -> str:
    """The case as an answer a model might write, in one of the citation styles the
    contract accepts (docs/CONTRACT.md section 4)."""
    if case.category == "hidden_in_quote":
        visible, hidden = case.citations
        group = f"[{visible.handle} 'see; {hidden.handle} 'here']"
    else:
        (planted,) = case.citations
        group = _group(planted, rng)
    sentence = "The cited source reports this finding"
    if rng.random() < 0.5:
        return f"{sentence} {group}."
    return f"{sentence}. {group}"  # citations written after the full stop


def _group(planted: Planted, rng: random.Random) -> str:
    handle, quote = planted.handle, planted.quote
    if quote is None:
        style = rng.choice(["[{h}]", "[`{h}`]", "[**{h}**]", "[{h}.]"])
        return style.format(h=handle)
    style = rng.choice(
        [
            '[{h} "{q}"]',
            "[{h} " + chr(0x201C) + "{q}" + chr(0x201D) + "]",
            "[{h} '{q}']",
            '[{h}"{q}"]',
        ]
    )
    return style.format(h=handle, q=quote)


def _windows(text: str) -> list[str]:
    """Runs of 4 to 12 words with no quote mark: the quotes a case may plant."""
    words = text.split(" ")
    found: list[str] = []
    for start in range(0, max(0, len(words) - 3), 3):
        run = words[start : start + 4 + start % 9]
        if len(run) >= 4 and not any(ch in _QUOTE_MARKS for w in run for ch in w):
            found.append(" ".join(run))
    return found


def _has_digit(text: str) -> bool:
    return any(ch.isdigit() for ch in text)


def _retracted(record: Record) -> bool:
    retraction = record.quality.retraction
    return (
        retraction.state == "known" and retraction.value is not None and retraction.value.retracted
    )


def _flagged(record: Record) -> bool:
    peer = record.quality.peer_reviewed
    return (
        _retracted(record)
        or record.kind == "preprint"
        or (peer.state == "known" and peer.value is False)
    )
