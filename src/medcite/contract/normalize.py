"""Text normalization shared by passage issuing and quote checking (spec section 4.5)."""

import re
import unicodedata
from difflib import SequenceMatcher

_WHITESPACE = re.compile(r"\s+")
_WORD_START = re.compile(r"(?<=\s)\S")
_FOLDS = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2010": "-",
        "\u2011": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u00a0": " ",
    }
)


def normalize_text(text: str) -> str:
    """Steps 1-2: Unicode NFC, collapse whitespace runs to one space, trim."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def fold_for_compare(text: str) -> str:
    """Steps 1-3: normalize, then fold typographic variants. Comparison only."""
    folded = normalize_text(text).translate(_FOLDS).replace("\u2026", "...")
    return normalize_text(folded)


def quote_in_passage(quote: str, passage: str) -> bool:
    """True when the folded quote is a non-empty substring of the folded passage."""
    folded_quote = fold_for_compare(quote)
    return bool(folded_quote) and folded_quote in fold_for_compare(passage)


def closest_match(quote: str, passage: str) -> tuple[str, float]:
    """The passage window of quote length most similar to the quote, and its ratio (0-1).

    Windows start at each word boundary. Diagnostic only: never decides a verdict.
    """
    folded_quote = fold_for_compare(quote)
    folded_passage = fold_for_compare(passage)
    if not folded_quote or not folded_passage:
        return "", 0.0
    width = len(folded_quote)
    starts = [0, *(match.start() for match in _WORD_START.finditer(folded_passage))]
    best_window, best_ratio = "", -1.0
    for start in starts:
        window = folded_passage[start : start + width]
        ratio = SequenceMatcher(None, folded_quote, window, autojunk=False).ratio()
        if ratio > best_ratio:
            best_window, best_ratio = window, ratio
    return best_window, round(best_ratio, 4)
