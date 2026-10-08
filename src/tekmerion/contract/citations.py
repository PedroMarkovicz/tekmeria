"""Turn free answer text into claims with citations (spec sections 7.1-7.2)."""

import re
import warnings
from typing import cast

from tekmerion.contract.audit_models import CitationIn, ClaimIn

with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)  # pysbd ships invalid escape sequences
    import pysbd  # pyright: ignore[reportMissingTypeStubs]

_OPEN, _CLOSE = "\u27e6", "\u27e7"
# A bracket group may contain quoted spans, and a quote may contain brackets: "(range [3-10])".
# A single-quoted span opens after a space and closes before a separator or the bracket.
_GROUP_RE = re.compile(
    r"\[((?:(?<=\s)['\u2018](?:(?!['\u2019]\s*[;,\]]).)*['\u2019](?=\s*[;,\]])"
    r'|["“][^"“”]*["”]'
    r'|[^\[\]"“”])*)\]'
)
# Entries are separated by ";" or "," outside quotes (models often use commas). A single
# quote mark opens a quote only after a space and closes it only before a separator or the
# end, so an apostrophe ("children's") never does.
_ENTRY_SPLIT_RE = re.compile(
    r"(?:(?<=\s)['\u2018].*?['\u2019](?=\s*(?:[;,]|$))"
    r'|["\u201c][^"\u201c\u201d]*["\u201d]'
    r'|[^;,"\u201c\u201d])+'
)
_WRAPPING = "`*_"
_QUOTE_OPENERS, _QUOTE_CLOSERS = "\"\u201c'\u2018", "\"\u201d'\u2019"
# A handle ends at whitespace or an opening quote mark; it never contains either.
_HANDLE_THEN_REST_RE = re.compile(r"([^\s\"\u201c\u201d'\u2018\u2019]+)\s*(.*)", re.DOTALL)
# A handle never ends in sentence punctuation (its check code comes last).
_TRAILING_PUNCT = ".,;:!?"
_MAX_HANDLE = 300
# Backstop: a handle-shaped token outside any bracket group is still a citation.
_BARE_HANDLE_RE = re.compile(
    r"(?<![\w:/~])(?:pmid|pmcid|ppr|nct|setid):[A-Za-z0-9@./_-]+~[0-9a-hjkmnp-tv-z]{5}(?![0-9a-z])"
)
_CITE_TOKEN_RE = re.compile(r"^[a-z]+:(?!//)\S+")  # scheme:id, but not a URL
_PLACEHOLDER_RE = re.compile(_OPEN + r"(\d+)" + _CLOSE)
_LEADING_PLACEHOLDERS_RE = re.compile(r"^(?:\s*" + _OPEN + r"\d+" + _CLOSE + r")+")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,;:!?)])")
_WORD_RE = re.compile(r"\w")
_SEGMENTER = pysbd.Segmenter(language="en", clean=False)
# Markdown layout: heading lines, and a bold run opening a line
# ("**Results.** The study..." or "**Safety**" alone on its line).
_HEADING_LINE_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.*)$", re.MULTILINE)
_BOLD_LEAD_RE = re.compile(r"^([ \t]*)(\*\*|__)(.+?)\2", re.MULTILINE)
_EMPHASIS_RE = re.compile(r"\*\*|__")


def _entries(group_body: str) -> list[str]:
    entries = (entry.strip() for entry in _ENTRY_SPLIT_RE.findall(group_body))
    return [entry for entry in entries if entry]


def _split_entry(entry: str) -> tuple[str, str]:
    """(handle, rest) of an entry. The handle is unwrapped from markdown such as `code` or
    **bold** and loses trailing punctuation; the rest may follow without a space."""
    match = _HANDLE_THEN_REST_RE.match(entry)
    if match is None:  # the entry opens with a quote mark: no handle to unwrap
        return entry, ""
    handle = match.group(1).lstrip(_WRAPPING).rstrip(_WRAPPING + _TRAILING_PUNCT)
    return handle, match.group(2).strip()


def _first_token(entry: str) -> str:
    return _split_entry(entry)[0]


def _citation(entry: str) -> CitationIn:
    """Handle first, then an optional quote running from the first to the last quote mark.

    Never raises: an entry that cannot be read becomes a (malformed) handle.
    """
    handle, rest = _split_entry(entry)
    handle = handle[:_MAX_HANDLE]
    if not rest:
        return CitationIn(handle=handle)
    if len(rest) >= 2 and rest[0] in _QUOTE_OPENERS and rest[-1] in _QUOTE_CLOSERS:
        return CitationIn(handle=handle, quote=rest[1:-1])
    return CitationIn(handle=entry[:_MAX_HANDLE])


def _clean_sentence(text: str) -> str:
    without = _PLACEHOLDER_RE.sub(" ", text)
    collapsed = re.sub(r"\s+", " ", without).strip()
    return _SPACE_BEFORE_PUNCT_RE.sub(r"\1", collapsed)


def _unwrap_markdown(masked: str) -> str:
    """Make Markdown headings and bold lead-ins sentences of their own, and remove the
    markers (#, ** and __). Nothing is dropped: a bold claim without a citation is still
    an uncited sentence, so no text can hide from the audit."""
    text = _HEADING_LINE_RE.sub(lambda m: m.group(1), masked)
    text = _BOLD_LEAD_RE.sub(
        lambda m: f"{m.group(1)}{m.group(3)}\n", text
    )  # line break: own sentence
    return _EMPHASIS_RE.sub("", text)


def parse_answer_text(text: str) -> list[ClaimIn]:
    """Split text into sentences and attach each citation group to its sentence.

    A bracket group is a citation group when any of its entries starts like
    `scheme:...`; other brackets such as `[1]` stay as plain text. A handle-shaped
    token outside any group still counts, so no handle can vanish. Placeholders
    that open a sentence (citations written after the full stop) belong to the
    previous sentence. Markdown headings and bold lead-ins are sentences of their own.

    Raises pydantic.ValidationError when a sentence exceeds a limit, including citations
    moved onto it from the start of the next sentence.
    """
    groups: list[list[CitationIn]] = []

    def mask(match: re.Match[str]) -> str:
        entries = _entries(match.group(1))
        if not any(_CITE_TOKEN_RE.match(_first_token(entry)) for entry in entries):
            return match.group(0)
        cited = [_citation(entry) for entry in entries]
        # A handle written inside a quote (say, after an unclosed quote mark) still counts.
        hidden = [
            CitationIn(handle=handle)
            for citation in cited
            if citation.quote
            for handle in _BARE_HANDLE_RE.findall(citation.quote)
        ]
        groups.append(cited + hidden)
        return f"{_OPEN}{len(groups) - 1}{_CLOSE}"

    def mask_bare(match: re.Match[str]) -> str:
        groups.append([CitationIn(handle=match.group(0))])
        return f"{_OPEN}{len(groups) - 1}{_CLOSE}"

    masked = _unwrap_markdown(_BARE_HANDLE_RE.sub(mask_bare, _GROUP_RE.sub(mask, text)))
    drafts: list[tuple[str, list[CitationIn]]] = []
    segments = cast("list[str]", _SEGMENTER.segment(masked))  # pyright: ignore[reportUnknownMemberType]
    for raw in segments:
        sentence = raw
        leading = _LEADING_PLACEHOLDERS_RE.match(sentence)
        if leading and drafts:
            for index in _PLACEHOLDER_RE.findall(leading.group(0)):
                drafts[-1][1].extend(groups[int(index)])
            sentence = sentence[leading.end() :]
        cleaned = _clean_sentence(sentence)
        cited = [c for index in _PLACEHOLDER_RE.findall(sentence) for c in groups[int(index)]]
        if not _WORD_RE.search(cleaned) and drafts:
            drafts[-1][1].extend(cited)
            continue
        if cleaned or cited:
            drafts.append((cleaned, cited))
    # Validated last, so citations moved onto a sentence count towards its limit too.
    return [ClaimIn(sentence=sentence, citations=cited) for sentence, cited in drafts]
