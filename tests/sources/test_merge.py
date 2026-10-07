from medcite.sources.merge import Candidate, identity_keys, interleave


def c(*keys: str) -> Candidate:
    return Candidate(frozenset(keys), keys[0])


def test_identity_keys_normalize_case() -> None:
    assert identity_keys(pmid="1", pmcid="pmc2", doi="10.1/ABC", ppr="ppr3") == frozenset(
        {"pmid:1", "pmcid:PMC2", "doi:10.1/abc", "ppr:PPR3"}
    )


def test_interleave_alternates_and_skips_duplicates() -> None:
    pubmed = [c("pmid:1"), c("pmid:2"), c("pmid:3")]
    europepmc = [c("pmid:2", "doi:x"), c("ppr:9"), c("pmid:1")]
    chosen, consumed = interleave([pubmed, europepmc], limit=4)
    assert [x.payload for x in chosen] == ["pmid:1", "pmid:2", "pmid:3", "ppr:9"]
    # europepmc's pmid:2 was skipped as a duplicate but still counts as consumed
    assert consumed == [3, 2]


def test_interleave_stops_at_limit_and_reports_exact_positions() -> None:
    chosen, consumed = interleave([[c("a"), c("b")], [c("x"), c("y")]], limit=3)
    assert [x.payload for x in chosen] == ["a", "x", "b"]
    assert consumed == [2, 1]


def test_interleave_with_one_page_or_none() -> None:
    assert interleave([[c("a")]], limit=5) == ([c("a")], [1])
    assert interleave([], limit=5) == ([], [])
