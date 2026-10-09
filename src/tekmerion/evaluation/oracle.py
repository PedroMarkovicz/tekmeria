"""The reference oracle for planted cases (spec section 16.10).

Written from the spec (4.1 grammar, 4.4 check code, 4.5 normalization) and sharing no code
with the audit under test: the generator decides what it plants with this module, so a bug in
the audit's own comparator or codec cannot quietly drop or reshape the cases that would expose
it. A misreading of the spec itself would of course be shared by both.
"""

import hashlib
import hmac
import re
import unicodedata

# Spec 4.1: five characters of lowercase Crockford base32.
ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
# Spec 4.1: scheme ":" id ["@" version] "/" path "~" check.
_HANDLE = re.compile(
    r"^(?P<locator>(?:pmid|pmcid|ppr|nct|setid):[A-Za-z0-9-]+(?:@[A-Za-z0-9.-]+)?"
    r"/[a-z0-9_]+(?:\.[a-z0-9_]+)*)~(?P<check>[0-9a-hjkmnp-tv-z]{5})$"
)
# Spec 4.5 step 3 (docs/CONTRACT.md section 3): typographic variants, folded for comparison.
FOLDS = {
    **dict.fromkeys(map(chr, (0x2018, 0x2019, 0x201A, 0x201B, 0x02BC, 0x2032)), "'"),
    **dict.fromkeys(map(chr, (0x201C, 0x201D, 0x201E, 0x201F)), '"'),
    **dict.fromkeys(
        map(chr, (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xFF0D)), "-"
    ),
    chr(0x00A0): " ",
    chr(0x2026): "...",
}
# Spec 4.5 step 3: invisible characters, removed for comparison.
REMOVED = frozenset(map(chr, (0x00AD, 0x200B, 0x2060, 0xFEFF)))


def check_code(key: bytes, locator: str) -> str:
    """Spec 4.4: the first 25 bits of HMAC-SHA256(key, utf8(locator)) in Crockford base32."""
    digest = hmac.new(key, locator.encode("utf-8"), hashlib.sha256).digest()
    bits = int.from_bytes(digest[:4], "big") >> 7
    return "".join(ALPHABET[(bits >> shift) & 0b11111] for shift in (20, 15, 10, 5, 0))


def issue(key: bytes, locator: str) -> str:
    """The genuine handle for a locator such as 'pmid:30831578/abstract.7'."""
    return f"{locator}~{check_code(key, locator)}"


def well_formed(handle: str) -> bool:
    return _HANDLE.match(handle) is not None


def is_genuine(key: bytes, handle: str) -> bool:
    match = _HANDLE.match(handle)
    return match is not None and hmac.compare_digest(
        match["check"], check_code(key, match["locator"])
    )


def fold(text: str) -> str:
    """Spec 4.5: NFC, whitespace runs to one space, ends trimmed, then the typographic folds
    and the removal of invisible characters."""
    spaced = " ".join(unicodedata.normalize("NFC", text).split())
    kept = (char for char in spaced if char not in REMOVED)
    return " ".join("".join(FOLDS.get(char, char) for char in kept).split())


def quote_matches(quote: str, passage: str) -> bool:
    """True when the folded quote is a non-empty part of the folded passage."""
    needle = fold(quote)
    return bool(needle) and needle in fold(passage)


# docs/CONTRACT.md sections 7 to 9: claim status, metrics and the overall verdict, written
# from the contract, for scoring whole answers.
DEFAULT_FAIL_ON = ("forged", "malformed")


def claim_status(verdicts: list[str]) -> str:
    if not verdicts:
        return "uncited"
    return "cited" if "ok" in verdicts else "invalid_only"


def overall_verdict(statuses: list[str], verdicts: list[str], retracted_ok: int) -> str:
    """Section 9 with the default fail_on (forged and malformed)."""
    if set(verdicts) & set(DEFAULT_FAIL_ON):
        return "fail"
    if "unavailable" in verdicts:
        return "incomplete"
    if "uncited" in statuses or "invalid_only" in statuses or retracted_ok:
        return "warn"
    return "pass"


def metrics(
    statuses: list[str], citations: list[tuple[str, bool, bool]]
) -> dict[str, float | int | None]:
    """Section 8. `citations` holds (verdict, has_quote, retracted) for every citation."""
    verdicts = [verdict for verdict, _, _ in citations]
    checkable = len(verdicts) - verdicts.count("unavailable")
    quoted = [v for v, has_quote, _ in citations if has_quote and v in ("ok", "quote_mismatch")]
    return {
        "citation_validity": verdicts.count("ok") / checkable if checkable else None,
        "sentence_coverage": statuses.count("cited") / len(statuses) if statuses else None,
        "quote_accuracy": quoted.count("ok") / len(quoted) if quoted else None,
        "fabricated": verdicts.count("forged"),
        "retracted_cited": sum(1 for v, _, retracted in citations if v == "ok" and retracted),
    }
