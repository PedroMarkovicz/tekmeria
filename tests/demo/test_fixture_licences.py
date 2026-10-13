"""The recorded fixtures ship with the repository: they must hold only text it may
redistribute (see NOTICE)."""

import json
import re
import xml.etree.ElementTree as ET

import httpx

from tekmeria.demo.scenario import FIXTURES_DIR, fetched_pmids
from tekmeria.infra.fixtures import is_pubmed_fetch, scrub

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


def _open_search_results() -> dict[str, bool]:
    """PMID -> whether its Europe PMC search result carries an abstract, for every recorded
    result listed as CC BY or CC0. Read here without the recorder's own helpers."""
    found: dict[str, bool] = {}
    for record in _recorded():
        if httpx.URL(record["url"]).path == "/europepmc/webservices/rest/search":
            for result in json.loads(record["body"])["resultList"]["result"]:
                if result.get("pmid") and result.get("license") in ("cc by", "cc0"):
                    pmid = str(result["pmid"])
                    found[pmid] = found.get(pmid, False) or "abstractText" in result
    return found


def test_pubmed_responses_keep_abstracts_only_of_fetched_or_open_records() -> None:
    # PubMed carries no licence: abstracts stay only for the records the demo, the tests and
    # the evaluation fetch, and for those Europe PMC lists as CC BY or CC0, whose search
    # previews then keep a text. Read with an XML parser, independently of the recorder.
    scenario = json.loads((FIXTURES_DIR / "scenario.json").read_text(encoding="utf-8"))
    fetched = fetched_pmids(scenario)
    open_results = _open_search_results()
    keep = fetched | set(open_results)
    in_fetches: set[str] = set()
    with_abstract: set[str] = set()
    for record in _recorded():
        if is_pubmed_fetch(record["url"]):
            root = ET.fromstring(record["body"])
            for kind in ("PubmedArticle", "PubmedBookArticle"):
                for article in root.iter(kind):
                    in_fetches.add(article.findtext(".//PMID") or "")
                    if (
                        article.find(".//Abstract") is not None
                        or article.find(".//OtherAbstract") is not None
                    ):
                        with_abstract.add(article.findtext(".//PMID") or "")
        elif "/pubtator3-api/" in record["url"]:  # annotations carry the abstract's text too
            assert set(httpx.URL(record["url"]).params["pmids"].split(",")) <= fetched
    assert with_abstract <= keep, sorted(with_abstract - keep)
    restored = {p for p, has_abstract in open_results.items() if has_abstract} & in_fetches
    assert restored <= with_abstract, sorted(restored - with_abstract)
