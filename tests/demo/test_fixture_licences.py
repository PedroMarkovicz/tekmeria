"""The recorded fixtures ship with the repository: they must hold only text it may
redistribute (see NOTICE)."""

import json
import re

import httpx

from tekmerion.demo.scenario import FIXTURES_DIR
from tekmerion.infra.fixtures import scrub

_LICENCE_LINK = re.compile(r"creativecommons\.org/(?:licenses|publicdomain)/([a-z-]+)/")


def _recorded() -> list[dict[str, str]]:
    records = [json.loads(p.read_text(encoding="utf-8")) for p in FIXTURES_DIR.glob("*.json")]
    return [r for r in records if "url" in r]  # scenario.json holds IDs, not a response


def test_every_recorded_full_text_is_cc_by_or_cc0() -> None:
    full_texts = [r for r in _recorded() if r["url"].endswith("/fullTextXML")]
    assert full_texts
    for record in full_texts:
        assert set(_LICENCE_LINK.findall(record["body"])) <= {"by", "zero"}, record["url"]
        assert _LICENCE_LINK.search(record["body"]), record["url"]


def test_no_recorded_search_keeps_an_abstract_it_may_not_redistribute() -> None:
    for record in _recorded():
        assert scrub(httpx.URL(record["url"]), record["body"]) == record["body"], record["url"]
