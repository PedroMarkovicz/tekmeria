"""The recorded fixtures ship with the repository: they must hold only text it may
redistribute (see NOTICE)."""

import json
import re
import xml.etree.ElementTree as ET

import httpx

from tekmerion.demo.scenario import FIXTURES_DIR, fetched_pmids
from tekmerion.infra.fixtures import is_pubmed_fetch, scrub

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


def _lookup(query: str) -> dict[str, str]:
    """The one result of the recorded Europe PMC lookup whose query contains `query`."""
    (record,) = [r for r in _recorded() if query in httpx.URL(r["url"]).params.get("query", "")]
    (result,) = json.loads(record["body"])["resultList"]["result"]
    return result


def test_notice_credits_the_recorded_full_text_and_preprint() -> None:
    # NOTICE attributes the CC BY works by name; a re-recording that picks other works
    # must update it.
    notice = (FIXTURES_DIR.parents[3] / "NOTICE").read_text(encoding="utf-8")
    scenario = json.loads((FIXTURES_DIR / "scenario.json").read_text(encoding="utf-8"))
    article = _lookup(f"PMCID:{scenario['open_access_pmcid']}")
    preprint = _lookup(f"EXT_ID:{scenario['preprint_id']}")
    for expected in (scenario["open_access_pmcid"], article["doi"], preprint["doi"]):
        assert expected in notice, expected


def test_pubmed_responses_keep_abstracts_only_of_fetched_records() -> None:
    # PubMed carries no licence: only the records the demo, the tests and the evaluation fetch
    # keep their abstracts. Read with an XML parser, independently of the recorder's step.
    scenario = json.loads((FIXTURES_DIR / "scenario.json").read_text(encoding="utf-8"))
    keep = fetched_pmids(scenario)
    with_abstract: set[str] = set()
    for record in _recorded():
        if is_pubmed_fetch(record["url"]):
            for article in ET.fromstring(record["body"]).iter("PubmedArticle"):
                if article.find(".//Abstract") is not None:
                    with_abstract.add(article.findtext(".//PMID") or "")
        elif "/pubtator3-api/" in record["url"]:  # annotations carry the abstract's text too
            assert set(httpx.URL(record["url"]).params["pmids"].split(",")) <= keep
    assert with_abstract <= keep, sorted(with_abstract - keep)
