"""Instructions given to models must not contain anything the audit reads as a handle:
a model that repeats an example would otherwise be counted as fabricating."""

import pytest

from tekmeria.contract.citations import parse_answer_text
from tekmeria.contract.descriptions import CITE_BY_HANDLE_INSTRUCTIONS
from tekmeria.contract.handles import HANDLE_RE
from tekmeria.server.app import INSTRUCTIONS


@pytest.mark.parametrize("text", [CITE_BY_HANDLE_INSTRUCTIONS, INSTRUCTIONS])
def test_instructions_hold_no_handle_shaped_text(text: str) -> None:
    assert not any(HANDLE_RE.match(token.strip("`.,;:()[]")) for token in text.split())
    assert all(not claim.citations for claim in parse_answer_text(text))
