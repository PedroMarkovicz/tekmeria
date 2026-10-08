import pytest

from tekmerion.sources.errors import InvalidInputError
from tekmerion.sources.service import SearchState


def test_search_state_round_trip_and_validation() -> None:
    state = SearchState(pubmed=10, europepmc_mark="AoE", europepmc_skip=3, pubmed_done=True)
    assert SearchState.from_json(state.to_json()) == state
    for bad in [None, {"p": 1}, {**state.to_json(), "p": -1}, {**state.to_json(), "s": "x"}]:
        with pytest.raises(InvalidInputError):
            SearchState.from_json(bad)
