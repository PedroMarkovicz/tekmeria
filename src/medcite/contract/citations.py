"""Turn free answer text into claims with citations (spec sections 7.1-7.2)."""

import re
import warnings
from typing import cast

from medcite.contract.audit_models import CitationIn, ClaimIn

with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)  # pysbd ships invalid escape sequences
    import pysbd  # pyright: ignore[reportMissingTypeStubs]

_OPEN, _CLOSE = "\u27e6", "\u27e7"
# A bracket group may contain quoted spans, and a quote may contain brackets: "(range [3-10])".
_GROUP_RE = re.compile(r'\[((?:[^\[\]"“”]|["“][^"“”]*["”])*)\]')
# Entries are separated by ";" or "," outside quotes (models often use commas).
_ENTRY_SPLIT_RE = re.compile(r'(?:[^;,"\u201c\u201d]|["\u201c][^"\u201c\u201d]*["\u201d])+')
_WRAPPING = "`*_"
_QUOTE_OPENERS, _QUOTE_CLOSERS = '"\u201c', '"\u201d'
_MAX_HANDLE = 300
# Backstop: a handle-shaped token outside any bracket group is still a citation.
_BARE_HANDLE_RE = re.compile(
    r"(?<![\w:/~])(?:pmid|pmcid|ppr|nct|setid):[A-Za-z0-9@./_-]+~[0-9a-hjkmnp-tv-z]{5}(?![0-9a-z])"
)
_CITE_TOKEN_RE = re.compile(r"^[a-z]+:\S+")
_PLACEHOLDER_RE = re.compile(_OPEN + r"(\d+)" + _CLOSE)
_LEADING_PLACEHOLDERS_RE = re.compile(r"^(?:\s*" + _OPEN + r"\d+" + _CLOSE + r")+")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,;:!?)])")
_WORD_RE = re.compile(r"\w")
_SEGMENTER = pysbd.Segmenter(language="en", clean=False)
# Markdown that is layout, not claims: heading lines, and a bold run opening a line
# ("**Results.** The study..." or "**Safety**" alone on its line).
_HEADING_LINE_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.*)$", re.MULTILINE)
_BOLD_LEAD_RE = re.compile(r"^([ \t]*)(\*\*|__)(.+?)\2", re.MULTILINE)
_EMPHASIS_RE = re.compile(r"\*\*|__")


def _entries(group_body: str) -> list[str]:
    entries = (entry.strip() for entry in _ENTRY_SPLIT_RE.findall(group_body))
    return [entry for entry in entries if entry]


def _first_token(entry: str) -> str:
    """The handle part of an entry, unwrapped from markdown such as `code` or **bold**."""
    return entry.split(maxsplit=1)[0].strip(_WRAPPING)


def _citation(entry: str) -> CitationIn:
    """Handle first, then an optional quote running from the first to the last quote mark.

    Never raises: an entry that cannot be read becomes a (malformed) handle.
    """
    parts = entry.split(maxsplit=1)
    handle = _first_token(entry)[:_MAX_HANDLE]
    rest = parts[1].strip() if len(parts) > 1 else ""
    if not rest:
        return CitationIn(handle=handle)
    if len(rest) >= 2 and rest[0] in _QUOTE_OPENERS and rest[-1] in _QUOTE_CLOSERS:
        return CitationIn(handle=handle, quote=rest[1:-1])
    return CitationIn(handle=entry[:_MAX_HANDLE])


def _clean_sentence(text: str) -> str:
    without = _PLACEHOLDER_RE.sub(" ", text)
    collapsed = re.sub(r"\s+", " ", without).strip()
    return _SPACE_BEFORE_PUNCT_RE.sub(r"\1", collapsed)


def _drop_labels(masked: str) -> str:
    """Drop Markdown headings and bold lead-in labels, which are not claims, unless they
    carry a citation (then only their markers go). Remaining ** and __ are removed."""

    def heading(match: re.Match[str]) -> str:
        return match.group(1) if _PLACEHOLDER_RE.search(match.group(1)) else ""

    def lead(match: re.Match[str]) -> str:
        label = match.group(3)
        return match.group(1) + (label if _PLACEHOLDER_RE.search(label) else "")

    unlabelled = _BOLD_LEAD_RE.sub(lead, _HEADING_LINE_RE.sub(heading, masked))
    return _EMPHASIS_RE.sub("", unlabelled)


def parse_answer_text(text: str) -> list[ClaimIn]:
    """Split text into sentences and attach each citation group to its sentence.

    A bracket group is a citation group when any of its entries starts like
    `scheme:...`; other brackets such as `[1]` stay as plain text. A handle-shaped
    token outside any group still counts, so no handle can vanish. Placeholders
    that open a sentence (citations written after the full stop) belong to the
    previous sentence. Markdown headings and bold lead-in labels are not claims.
    """
    groups: list[list[CitationIn]] = []

    def mask(match: re.Match[str]) -> str:
        entries = _entries(match.group(1))
        if not any(_CITE_TOKEN_RE.match(_first_token(entry)) for entry in entries):
            return match.group(0)
        groups.append([_citation(entry) for entry in entries])
        return f"{_OPEN}{len(groups) - 1}{_CLOSE}"

    def mask_bare(match: re.Match[str]) -> str:
        groups.append([CitationIn(handle=match.group(0))])
        return f"{_OPEN}{len(groups) - 1}{_CLOSE}"

    masked = _drop_labels(_BARE_HANDLE_RE.sub(mask_bare, _GROUP_RE.sub(mask, text)))
    claims: list[ClaimIn] = []
    segments = cast("list[str]", _SEGMENTER.segment(masked))  # pyright: ignore[reportUnknownMemberType]
    for raw in segments:
        sentence = raw
        leading = _LEADING_PLACEHOLDERS_RE.match(sentence)
        if leading and claims:
            for index in _PLACEHOLDER_RE.findall(leading.group(0)):
                claims[-1].citations.extend(groups[int(index)])
            sentence = sentence[leading.end() :]
        cleaned = _clean_sentence(sentence)
        cited = [c for index in _PLACEHOLDER_RE.findall(sentence) for c in groups[int(index)]]
        if not _WORD_RE.search(cleaned) and claims:
            claims[-1].citations.extend(cited)
            continue
        if cleaned or cited:
            claims.append(ClaimIn(sentence=cleaned, citations=cited))
    return claims
