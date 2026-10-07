"""Turn free answer text into claims with citations (spec sections 7.1-7.2)."""

import re
import warnings
from typing import cast

from medcite.contract.audit_models import CitationIn, ClaimIn

with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)  # pysbd ships invalid escape sequences
    import pysbd  # pyright: ignore[reportMissingTypeStubs]

_OPEN, _CLOSE = "\u27e6", "\u27e7"
_GROUP_RE = re.compile(r"\[([^\[\]]*)\]")
# Entries are separated by ";" or "," outside quotes (models often use commas).
_ENTRY_SPLIT_RE = re.compile(r'(?:[^;,"\u201c\u201d]|["\u201c][^"\u201c\u201d]*["\u201d])+')
_WRAPPING = "`*_ "
_ENTRY_RE = re.compile(
    r'^(?P<handle>[^\s"\u201c\u201d]+)(?:\s+["\u201c](?P<quote>[^"\u201c\u201d]*)["\u201d])?$'
)
_CITE_TOKEN_RE = re.compile(r"^[a-z]+:\S+")
_PLACEHOLDER_RE = re.compile(_OPEN + r"(\d+)" + _CLOSE)
_LEADING_PLACEHOLDERS_RE = re.compile(r"^(?:\s*" + _OPEN + r"\d+" + _CLOSE + r")+")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,;:!?)])")
_WORD_RE = re.compile(r"\w")
_SEGMENTER = pysbd.Segmenter(language="en", clean=False)


def _entries(group_body: str) -> list[str]:
    """Split a bracket group into entries, unwrapping markdown such as `code` or **bold**."""
    entries = (entry.strip().strip(_WRAPPING) for entry in _ENTRY_SPLIT_RE.findall(group_body))
    return [entry for entry in entries if entry]


def _citation(entry: str) -> CitationIn:
    match = _ENTRY_RE.match(entry)
    if not match:
        return CitationIn(handle=entry)
    return CitationIn(handle=match["handle"], quote=match["quote"])


def _clean_sentence(text: str) -> str:
    without = _PLACEHOLDER_RE.sub(" ", text)
    collapsed = re.sub(r"\s+", " ", without).strip()
    return _SPACE_BEFORE_PUNCT_RE.sub(r"\1", collapsed)


def parse_answer_text(text: str) -> list[ClaimIn]:
    """Split text into sentences and attach each citation group to its sentence.

    A bracket group is a citation group when any of its entries starts like
    `scheme:...`; other brackets such as `[1]` stay as plain text. Placeholders
    that open a sentence (citations written after the full stop) belong to the
    previous sentence.
    """
    groups: list[list[CitationIn]] = []

    def mask(match: re.Match[str]) -> str:
        entries = _entries(match.group(1))
        if not any(_CITE_TOKEN_RE.match(entry) for entry in entries):
            return match.group(0)
        groups.append([_citation(entry) for entry in entries])
        return f"{_OPEN}{len(groups) - 1}{_CLOSE}"

    masked = _GROUP_RE.sub(mask, text)
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
