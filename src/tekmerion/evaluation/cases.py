"""Planted cases: citations whose correct verdict is known before the audit runs (spec 16.10).

Every case is built from the snapshot and a seeded random generator, so the same seed always
yields the same cases. Each category plants one kind of error, or none. What a case plants is
decided by the reference oracle (`oracle.py`), never by the audit's own comparator or codec:
handles are issued, forged and checked by the oracle, and a planted mismatch is one the oracle
cannot find in the passage.
"""

import random
import re
import unicodedata
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
# Edits that change biomedical meaning while keeping almost every character.
_DECIMAL = re.compile(r"\d+\.\d+")
_FLIPS = {"<": ">", ">": "<", chr(0x2264): chr(0x2265), chr(0x2265): chr(0x2264)}
_COMPARISON = re.compile("[" + "".join(_FLIPS) + "]")
_UNIT_SWAPS = {
    "mg/kg": "mg",
    "mg/dL": "mg/L",
    "mcg": "mg",
    "mg": "g",
    "kg": "g",
    "mmol": "mol",
    "mL": "L",
    "IU": "mg",
    "g": "mg",
    "L": "mL",
    "%": chr(0x2030),
}
_UNIT = re.compile(r"(?<=\d) ?(mg/kg|mg/dL|mcg|mg|kg|mmol|mL|IU|g|L|%)(?![A-Za-z])")
_UNSIGNED_NUMBER = re.compile(r"(?<![\w.,+-])\d")
_WORD = re.compile(r"\b[A-Za-z][a-z]+\b")
# Typographic variants a verbatim quote may use (spec 4.5): each folds back to the passage.
_APOSTROPHES = {"'": chr(0x2019), chr(0x2019): "'"}
_DOUBLE_MARKS = {'"': chr(0x201C), chr(0x201C): '"', chr(0x201D): '"'}
# Every other fold of spec 4.5, written into a verbatim quote one at a time, in turn.
_DASH_FOLDS = tuple(map(chr, (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xFF0D)))
_HYPHEN = re.compile("-")
_SINGLE_FOLDS = tuple(map(chr, (0x2018, 0x2019, 0x201A, 0x201B, 0x02BC, 0x2032)))
_DOUBLE_FOLDS = (chr(0x201C), chr(0x201D), chr(0x201E), chr(0x201F))
# Invisible characters the comparison removes, written inside a word of a verbatim quote.
_INVISIBLE = (chr(0x00AD), chr(0x200B), chr(0x2060), chr(0xFEFF))
_INSIDE_WORD = re.compile(r"[A-Za-z](?=[A-Za-z])")
# Edits a lenient comparator would accept: an accent, a compatibility form, a lost space.
_ACCENTS = {"a": chr(0xE1), "e": chr(0xE9), "i": chr(0xED), "o": chr(0xF3), "u": chr(0xFA)}
_VOWEL = re.compile("[aeiou]")
_SUPERSCRIPTS = dict(
    zip(
        "0123456789",
        [chr(c) for c in (0x2070, 0xB9, 0xB2, 0xB3)] + [chr(c) for c in range(0x2074, 0x207A)],
        strict=True,
    )
)
_DIGIT = re.compile(r"\d")
_INNER_SPACE = re.compile(r"(?<=\w) (?=\w)")
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
        self.decimal = self._having(_DECIMAL)
        self.comparison = self._having(_COMPARISON)
        self.unit = self._having(_UNIT)
        self.unsigned = self._having(_UNSIGNED_NUMBER)
        self.apostrophe = self._having(re.compile("[" + "".join(_APOSTROPHES) + "]"), _APOSTROPHES)
        self.decomposable = self._having(re.compile("[" + chr(0xC0) + "-" + chr(0x24F) + "]"))
        self.double_marks = self._having(
            re.compile("[" + "".join(_DOUBLE_MARKS) + "]"), _DOUBLE_MARKS
        )
        self.hyphenated = self._having(_HYPHEN)
        self.vowels = self._having(_VOWEL)
        self.digits = self._having(_DIGIT)
        self.spaced = self._having(_INNER_SPACE)
        self.records = snapshot.records
        self._queues: dict[int, list[tuple[Record, Passage]]] = {}
        self._turns: dict[str, int] = {}
        self._marked: dict[str, list[tuple[Record, Passage]]] = {}

    def turn(self, category: str, options: tuple[str, ...]) -> str:
        """The next of `options`, in turn, so a category uses every option evenly."""
        index = self._turns.get(category, 0)
        self._turns[category] = index + 1
        return options[index % len(options)]

    def pick(self, pool: list[tuple[Record, Passage]]) -> tuple[Record, Passage]:
        """The next passage of a shuffled pass through `pool`: no passage repeats until every
        passage of the pool has been used, so small pools are spread evenly."""
        queue = self._queues.get(id(pool))
        if not queue:
            queue = self._queues[id(pool)] = self.rng.sample(pool, len(pool))
        return queue.pop()

    def window(self, text: str, *, digits: bool = False) -> str:
        options = [w for w in _windows(text) if _has_digit(w) or not digits]
        return options[self.rng.randrange(len(options))]

    def _having(
        self, pattern: re.Pattern[str], allowed: dict[str, str] | None = None
    ) -> list[tuple[Record, Passage]]:
        """Clean passages with at least one quotable window that `pattern` matches."""
        marks = frozenset(allowed or ())
        return [
            (r, p) for r, p in self.clean if any(pattern.search(w) for w in _windows(p.text, marks))
        ]

    def matching_window(
        self, text: str, pattern: re.Pattern[str], allowed: dict[str, str] | None = None
    ) -> str:
        options = [w for w in _windows(text, frozenset(allowed or ())) if pattern.search(w)]
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

    def _variant_ok(
        self,
        category: str,
        pool: list[tuple[Record, Passage]],
        pattern: re.Pattern[str],
        rewrite: Callable[[str], str],
        allowed: dict[str, str] | None = None,
        modes: tuple[Mode, ...] = MODES,
    ) -> Case:
        """A verbatim quote written with a variant the comparison must fold back."""

        def attempt() -> Case | None:
            _, passage = self.pick(pool)
            window = self.matching_window(passage.text, pattern, allowed)
            quote = rewrite(window)
            if quote == window:  # nothing to fold: this would be a plain verbatim quote
                return None
            if not oracle.quote_matches(quote, passage.text):  # the variant must still match
                return None
            return Case(category, (Planted(self.handle(passage), quote, "ok"),), modes=modes)

        return self._draw(category, attempt)

    def ok_quote_apostrophe(self) -> Case:
        pattern = re.compile("[" + "".join(_APOSTROPHES) + "]")
        return self._variant_ok(
            "ok_quote_apostrophe",
            self.apostrophe,
            pattern,
            lambda q: "".join(_APOSTROPHES.get(c, c) for c in q),
            _APOSTROPHES,
        )

    def ok_quote_decomposed(self) -> Case:
        pattern = re.compile("[" + chr(0xC0) + "-" + chr(0x24F) + "]")
        return self._variant_ok(
            "ok_quote_decomposed",
            self.decomposable,
            pattern,
            lambda q: unicodedata.normalize("NFD", q),
        )

    def ok_quote_double_marks(self) -> Case:
        # Answer text cannot carry a quote that contains double quote marks (CONTRACT section
        # 4), so this category is audited as structured claims only.
        pattern = re.compile("[" + "".join(_DOUBLE_MARKS) + "]")
        return self._variant_ok(
            "ok_quote_double_marks",
            self.double_marks,
            pattern,
            lambda q: "".join(_DOUBLE_MARKS.get(c, c) for c in q),
            _DOUBLE_MARKS,
            modes=("claims",),
        )

    def _replace_one(self, quote: str, pattern: re.Pattern[str], new: Callable[[str], str]) -> str:
        """`quote` with one match of `pattern`, chosen at random, replaced by `new(match)`."""
        match = self.rng.choice(list(pattern.finditer(quote)))
        return quote[: match.start()] + new(match.group(0)) + quote[match.end() :]

    def ok_quote_folded(self) -> Case:
        dash = self.turn("ok_quote_folded", _DASH_FOLDS)
        return self._variant_ok(
            "ok_quote_folded",
            self.hyphenated,
            _HYPHEN,
            lambda q: self._replace_one(q, _HYPHEN, lambda _: dash),
        )

    def ok_quote_folded_marks(self) -> Case:
        # A quote mark inside a quote cannot be carried by answer text (CONTRACT section 4), so
        # this category is audited as structured claims only.
        mark = self.turn("ok_quote_folded_marks", _SINGLE_FOLDS + _DOUBLE_FOLDS)
        single = mark in _SINGLE_FOLDS
        family = "'" + chr(0x2019) if single else '"' + chr(0x201C) + chr(0x201D)
        allowed = _APOSTROPHES if single else _DOUBLE_MARKS
        # The mark replaces one of its family that differs from it, so the quote always changes.
        others = family.replace(mark, "")
        pattern = re.compile("[" + others + "]")
        if others not in self._marked:  # one pool per pattern keeps draws without replacement
            self._marked[others] = self._having(pattern, allowed)
        pool = self._marked[others]
        return self._variant_ok(
            "ok_quote_folded_marks",
            pool,
            pattern,
            lambda q: self._replace_one(q, pattern, lambda _: mark),
            allowed,
            modes=("claims",),
        )

    def ok_quote_invisible(self) -> Case:
        # A soft hyphen or zero-width character in the quote, where the passage has none: the
        # comparison removes it on both sides, so the quote is still verbatim.
        char = self.turn("ok_quote_invisible", _INVISIBLE)
        return self._variant_ok(
            "ok_quote_invisible",
            self.quotable,
            _INSIDE_WORD,
            lambda q: self._replace_one(q, _INSIDE_WORD, lambda letter: letter + char),
        )

    def quote_accent_added(self) -> Case:
        return self._edit_mismatch(
            "quote_accent_added",
            self.vowels,
            _VOWEL,
            lambda q: self._replace_one(q, _VOWEL, lambda v: _ACCENTS[v]),
        )

    def quote_compatibility_form(self) -> Case:
        # A superscript digit: NFKC would read it as the plain digit, NFC keeps it apart.
        return self._edit_mismatch(
            "quote_compatibility_form",
            self.digits,
            _DIGIT,
            lambda q: self._replace_one(q, _DIGIT, lambda d: _SUPERSCRIPTS[d]),
        )

    def quote_space_removed(self) -> Case:
        return self._edit_mismatch(
            "quote_space_removed",
            self.spaced,
            _INNER_SPACE,
            lambda q: self._replace_one(q, _INNER_SPACE, lambda _: ""),
        )

    def _edit_mismatch(
        self,
        category: str,
        pool: list[tuple[Record, Passage]],
        pattern: re.Pattern[str],
        edit: Callable[[str], str],
    ) -> Case:
        def attempt() -> Case | None:
            _, passage = self.pick(pool)
            quote = edit(self.matching_window(passage.text, pattern))
            if oracle.quote_matches(quote, passage.text):  # the edit recreated real text
                return None
            return Case(category, (Planted(self.handle(passage), quote, "quote_mismatch"),))

        return self._draw(category, attempt)

    def quote_decimal_moved(self) -> Case:
        def edit(quote: str) -> str:
            match = self.rng.choice(list(_DECIMAL.finditer(quote)))
            whole, frac = match.group(0).split(".")
            if len(whole) > 1:  # 97.6 -> 9.76
                moved = whole[:-1] + "." + whole[-1] + frac
            else:  # 0.93 -> 9.3, and 1.5 -> 15
                digits = (whole + frac[0]).lstrip("0") or "0"
                moved = digits + ("." + frac[1:] if len(frac) > 1 else "")
            return quote[: match.start()] + moved + quote[match.end() :]

        return self._edit_mismatch("quote_decimal_moved", self.decimal, _DECIMAL, edit)

    def quote_comparison_flipped(self) -> Case:
        def edit(quote: str) -> str:
            match = self.rng.choice(list(_COMPARISON.finditer(quote)))
            return quote[: match.start()] + _FLIPS[match.group(0)] + quote[match.end() :]

        return self._edit_mismatch("quote_comparison_flipped", self.comparison, _COMPARISON, edit)

    def quote_unit_changed(self) -> Case:
        def edit(quote: str) -> str:
            match = self.rng.choice(list(_UNIT.finditer(quote)))
            unit = match.group(1)
            start = match.start(1)
            return quote[:start] + _UNIT_SWAPS[unit] + quote[start + len(unit) :]

        return self._edit_mismatch("quote_unit_changed", self.unit, _UNIT, edit)

    def quote_sign_changed(self) -> Case:
        def edit(quote: str) -> str:
            match = self.rng.choice(list(_UNSIGNED_NUMBER.finditer(quote)))
            return quote[: match.start()] + "-" + quote[match.start() :]

        return self._edit_mismatch("quote_sign_changed", self.unsigned, _UNSIGNED_NUMBER, edit)

    def quote_case_changed(self) -> Case:
        def edit(quote: str) -> str:
            match = self.rng.choice(list(_WORD.finditer(quote)))
            word = match.group(0)
            flipped = word[0].lower() if word[0].isupper() else word[0].upper()
            return quote[: match.start()] + flipped + word[1:] + quote[match.end() :]

        return self._edit_mismatch("quote_case_changed", self.quotable, _WORD, edit)

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

    def malformed_with_note(self) -> Case:
        # A note written inside the citation entry, not as a quote: the entry is not a handle.
        _, passage = self.pick(self.clean)
        note = self.rng.choice(["(Table 2)", "(abstract)", "p. 4", "(see Methods)"])
        entry = f"{self.handle(passage)} {note}"
        if oracle.well_formed(entry):
            raise RuntimeError(f"malformed_with_note: {entry} is well formed")
        return Case("malformed_with_note", (Planted(entry, None, "malformed"),))

    def forged_bare(self) -> Case:
        # A forged handle written outside any brackets still counts (docs/CONTRACT.md
        # section 4), so it cannot slip past the audit.
        _, passage = self.pick(self.clean)
        forged = self.forge_code(self.handle(passage))
        return Case("forged_bare", (Planted(forged, None, "forged"),), modes=("text",))

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
    "ok_quote_apostrophe",
    "ok_quote_decomposed",
    "ok_quote_double_marks",
    "ok_quote_folded",
    "ok_quote_folded_marks",
    "ok_quote_invisible",
    "quote_number_changed",
    "quote_decimal_moved",
    "quote_comparison_flipped",
    "quote_unit_changed",
    "quote_sign_changed",
    "quote_case_changed",
    "quote_accent_added",
    "quote_compatibility_form",
    "quote_space_removed",
    "quote_negated",
    "quote_word_swapped",
    "forged_check_code",
    "forged_path_swap",
    "forged_record_id",
    "forged_bare",
    "malformed",
    "malformed_with_note",
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


def render_group(case: Case, rng: random.Random) -> str:
    """The case's citation group, in one of the styles the contract accepts (docs/CONTRACT.md
    section 4)."""
    if case.category == "hidden_in_quote":
        visible, hidden = case.citations
        return f"[{visible.handle} 'see; {hidden.handle} 'here']"
    (planted,) = case.citations
    if case.category == "forged_bare":
        return f"(see {planted.handle})"
    if case.category == "malformed_with_note":
        return f"[{planted.handle}]"  # the whole entry is read as the handle, wrapping included
    return _group(planted, rng)


def after_full_stop(group: str, rng: random.Random) -> bool:
    """Whether to write the group after the sentence's full stop. Only a bracket group moves
    to the sentence before it; a bare handle stays inside its sentence."""
    return group.startswith("[") and rng.random() < 0.5


def render_text(case: Case, rng: random.Random) -> str:
    """The case as an answer a model might write."""
    group = render_group(case, rng)
    sentence = "The cited source reports this finding"
    if after_full_stop(group, rng):
        return f"{sentence}. {group}"
    return f"{sentence} {group}."


def _group(planted: Planted, rng: random.Random) -> str:
    handle, quote = planted.handle, planted.quote
    if quote is None:
        style = rng.choice(["[{h}]", "[`{h}`]", "[**{h}**]", "[{h}.]"])
        return style.format(h=handle)
    styles = ['[{h} "{q}"]', "[{h} " + chr(0x201C) + "{q}" + chr(0x201D) + "]", '[{h}"{q}"]']
    if not set(quote) & set(_APOSTROPHES):  # an apostrophe could close a single-quoted quote
        styles.append("[{h} '{q}']")
    return rng.choice(styles).format(h=handle, q=quote)


def _windows(text: str, allowed: frozenset[str] = frozenset()) -> list[str]:
    """Runs of 4, 7 or 10 words with no quote mark other than `allowed`: the quotes a case may
    plant."""
    banned = _QUOTE_MARKS - allowed
    words = text.split(" ")
    found: list[str] = []
    for start in range(0, max(0, len(words) - 3), 3):
        run = words[start : start + 4 + start % 9]
        if len(run) >= 4 and not any(ch in banned for w in run for ch in w):
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
