"""Merging search pages from several sources: interleave, de-duplicate, cut to the limit."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Candidate:
    keys: frozenset[str]  # e.g. {"pmid:1", "pmcid:PMC2", "doi:10.1/x"}
    payload: Any


def identity_keys(
    *, pmid: str | None, pmcid: str | None, doi: str | None, ppr: str | None
) -> frozenset[str]:
    keys = {
        f"pmid:{pmid}" if pmid else "",
        f"pmcid:{pmcid.upper()}" if pmcid else "",
        f"doi:{doi.lower()}" if doi else "",
        f"ppr:{ppr.upper()}" if ppr else "",
    }
    keys.discard("")
    return frozenset(keys)


def interleave(
    pages: Sequence[Sequence[Candidate]], limit: int
) -> tuple[list[Candidate], list[int]]:
    """Take one new item from each page in turn until `limit` items are chosen.

    An item sharing any identity key with an item already chosen is a duplicate and
    is skipped. Returns the chosen items and how many items of each page were consumed
    (skipped duplicates count as consumed), so paging can resume exactly there.
    """
    chosen: list[Candidate] = []
    seen: set[str] = set()
    positions = [0] * len(pages)
    while len(chosen) < limit:
        progressed = False
        for index, page in enumerate(pages):
            if len(chosen) >= limit:
                break
            while positions[index] < len(page):
                candidate = page[positions[index]]
                positions[index] += 1
                if candidate.keys & seen:
                    continue
                seen.update(candidate.keys)
                chosen.append(candidate)
                progressed = True
                break
        if not progressed:
            break
    return chosen, positions
