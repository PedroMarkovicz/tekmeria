"""The offline demo: search, fetch, then audit an answer with six planted cases (spec 12)."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.types import CallToolResult

from tekmerion.infra.fixtures import strip_abstracts

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SCENARIO_FILE = FIXTURES_DIR / "scenario.json"

DEMO_QUERY = "measles mumps rubella vaccine autism cohort"
COHORT_PMID = "30831578"  # Hviid 2019, Ann Intern Med: nationwide Danish cohort
RETRACTED_PMID = "9500320"  # Wakefield 1998, Lancet: retracted in 2010
MALFORMED_HANDLE = "pmid:30831578-abstract"
MISQUOTE = "MMR vaccination strongly increases the risk of autism"
UNCITED_SENTENCE = "Parents should discuss the vaccination schedule with their paediatrician."

# Extra calls recorded for the tests (filters, paging, a record not yet MEDLINE-indexed).
TEST_SEARCH_ARGS: dict[str, Any] = {
    "query": "autism",
    "study_designs": ["meta_analysis", "systematic_review"],
    "date_from": "2020",
    "date_to": "2024-12-31",
    "exclude_retracted": True,
    "limit": 3,
}


# A one-hit PubMed page, replayed by the test that takes Europe PMC offline.
PUBMED_ONLY_SEARCH: dict[str, Any] = {"query": DEMO_QUERY, "sources": ["pubmed"], "limit": 1}

# Searches that find records which change over time; results go to scenario.json.
IN_PROCESS_SEARCH: dict[str, Any] = {
    "query": "autism[tiab] AND inprocess[sb]",
    "sources": ["pubmed"],
    "limit": 1,
}
PREPRINT_SEARCH: dict[str, Any] = {
    "query": "SRC:PPR AND HAS_PUBLISHED_VERSION:Y AND vaccine",
    "sources": ["europepmc"],
    "limit": 5,
}
# Only a CC BY article: its full text is redistributed with the fixtures (see NOTICE). Its
# publication years end in 2025, so PubTator has annotated it and the record has settled.
OPEN_ACCESS_SEARCH: dict[str, Any] = {
    "query": '"measles vaccine" AND SRC:MED AND LICENSE:"cc by" NOT PUB_TYPE:"Published Erratum"',
    "date_from": "2020",
    "date_to": "2025-12-31",
    "sources": ["europepmc"],
    "open_access_only": True,
    "limit": 5,
}


# Registry calls. The trial and label found live go to scenario.json.
TRIAL_SEARCH: dict[str, Any] = {
    "condition": "heart failure",
    "intervention": "dapagliflozin",
    "has_results": True,
    "limit": 10,
}
LABEL_SEARCH: dict[str, Any] = {"drug": "warfarin", "limit": 3}
NO_LABEL_SEARCH: dict[str, Any] = {"drug": "zzzqqq not a drug"}


def fetched_pmids(scenario: dict[str, str]) -> frozenset[str]:
    """The PubMed records whose text the demo, the tests and the evaluation fetch: the only
    ones whose abstracts the recorded fixtures keep (see NOTICE)."""
    return frozenset(
        {COHORT_PMID, RETRACTED_PMID, scenario["in_process_pmid"], scenario["open_access_pmid"]}
    )


class DemoError(RuntimeError):
    pass


@dataclass(frozen=True)
class DemoRun:
    search: CallToolResult
    fetch: CallToolResult
    answer: str
    audit: CallToolResult


def _structured(result: CallToolResult) -> dict[str, Any]:
    if result.is_error or result.structured_content is None:
        text = result.content[0].text if result.content and result.content[0].type == "text" else ""
        raise DemoError(f"tool call failed: {text}")
    return result.structured_content


def require_complete(data: dict[str, Any]) -> dict[str, Any]:
    """Refuse a result where any source was degraded or unavailable.

    Recording must capture every upstream working; otherwise a transient upstream
    error (e.g. an HTTP 400 with a truncated body) would be saved as a fixture.
    """
    bad = {k: v for k, v in (data.get("source_status") or {}).items() if v != "ok"}
    if bad:
        raise DemoError(f"not recording a degraded result {bad}; re-run record-fixtures")
    return data


def _recorded(result: CallToolResult) -> dict[str, Any]:
    return require_complete(_structured(result))


def _passages_by_label(fetch: dict[str, Any], record_id: str) -> dict[str, dict[str, Any]]:
    for item in fetch["records"]:
        if item["record"]["record_id"] == record_id:
            return {p["section_label"]: p for p in item["passages"] if p["section_label"]}
    raise DemoError(f"{record_id} missing from fetch result")


def forge(handle: str) -> str:
    """Same locator, different check code: what a model inventing a citation produces."""
    last = handle[-1]
    return handle[:-1] + ("0" if last != "0" else "1")


def _verbatim_prefix(text: str, max_chars: int = 90) -> str:
    cut = text[:max_chars].rsplit(" ", 1)[0]
    return cut.replace('"', "").replace("[", "").replace("]", "").strip()


def build_planted_answer(fetch: dict[str, Any]) -> str:
    cohort = _passages_by_label(fetch, f"pmid:{COHORT_PMID}")
    retracted = _passages_by_label(fetch, f"pmid:{RETRACTED_PMID}")
    results, conclusion = cohort["RESULTS"], cohort["CONCLUSION"]
    interpretation = retracted["INTERPRETATION"]
    quote = _verbatim_prefix(results["text"])
    return " ".join(
        [
            "A nationwide Danish cohort found no increased risk of autism after MMR vaccination "
            f'[{results["handle"]} "{quote}"].',
            "The same study concluded that MMR vaccination raises autism risk "
            f'[{conclusion["handle"]} "{MISQUOTE}"].',
            f"A second cohort reported the same null association [{forge(results['handle'])}].",
            f"Earlier reviews reached the same conclusion [{MALFORMED_HANDLE}].",
            "A 1998 case series that proposed a link was later retracted "
            f"[{interpretation['handle']}].",
            UNCITED_SENTENCE,
        ]
    )


async def run_demo(client: Client) -> DemoRun:
    search = await client.call_tool("search_literature", {"query": DEMO_QUERY, "limit": 5})
    _structured(search)
    fetch = await client.call_tool(
        "fetch_passages",
        {"record_ids": [f"pmid:{COHORT_PMID}", f"pmid:{RETRACTED_PMID}"], "sections": ["abstract"]},
    )
    answer = build_planted_answer(_structured(fetch))
    audit = await client.call_tool("audit_answer", {"answer_text": answer})
    _structured(audit)
    return DemoRun(search=search, fetch=fetch, answer=answer, audit=audit)


def wanted(hit: dict[str, Any], want: str) -> bool:
    """Whether a search hit is the kind of record `want` names. An open-access article must
    be CC BY, since its full text is redistributed with the fixtures (see NOTICE)."""
    if want == "preprint":
        return hit["kind"] == "preprint"
    if want == "in_process":
        return bool(hit["ids"]["pmid"])
    oa = hit["open_access_full_text"]["value"] or {}
    return bool(
        hit["ids"]["pmcid"]
        and hit["ids"]["pmid"]
        and oa.get("available")
        and oa.get("license") == "cc by"
    )


async def _first_hit(client: Client, args: dict[str, Any], *, want: str) -> dict[str, Any]:
    hits = _recorded(await client.call_tool("search_literature", args))["hits"]
    for hit in hits:
        if wanted(hit, want):
            return hit
    raise DemoError(f"no {want} record found for {args['query']!r}")


async def _two_pages(client: Client, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Record a search and, when there is one, its second page; return the first page."""
    first = _recorded(await client.call_tool(tool, args))
    if first["next_cursor"]:
        _recorded(await client.call_tool(tool, {**args, "cursor": first["next_cursor"]}))
    return first


def _version(record_id: str) -> int:
    return int(record_id.rsplit("@", 1)[1])


async def _record_registries(client: Client) -> dict[str, str]:
    """A trial with posted results and a results publication, and a label with a boxed
    warning and earlier versions, each searched for and then fetched."""
    trials = await _two_pages(client, "search_trials", TRIAL_SEARCH)
    trial = next((h for h in trials["hits"] if h["has_results"] and h["linked_pmids"]), None)
    if trial is None:
        raise DemoError("no trial with posted results and a linked PMID in the search")
    labels = await _two_pages(client, "search_drug_labels", LABEL_SEARCH)
    label = next(
        (h for h in labels["hits"] if h["has_boxed_warning"] and _version(h["record_id"]) > 1),
        None,
    )
    if label is None:
        raise DemoError("no label with a boxed warning and an earlier version in the search")
    _recorded(await client.call_tool("search_drug_labels", NO_LABEL_SEARCH))
    for record_id in (trial["record_id"], label["record_id"]):
        _recorded(await client.call_tool("fetch_passages", {"record_ids": [record_id]}))
    return {"trial_record_id": trial["record_id"], "label_record_id": label["record_id"]}


async def record_scenario(client: Client) -> None:
    """Make every call the demo and the tests replay. Run against the live network.

    Records that change over time (an article not yet MEDLINE-indexed, a preprint with
    a published version, an open-access article) are found live and saved in
    scenario.json so the tests know which ones were recorded.
    """
    run = await run_demo(client)
    for result in (run.search, run.fetch, run.audit):
        _recorded(result)
    _recorded(await client.call_tool("search_literature", TEST_SEARCH_ARGS))
    _recorded(await client.call_tool("search_literature", PUBMED_ONLY_SEARCH))
    for sources in (None, ["europepmc"]):
        args: dict[str, Any] = {"query": DEMO_QUERY, "limit": 2}
        if sources:
            args["sources"] = sources
        page_one = _recorded(await client.call_tool("search_literature", args))
        if page_one["next_cursor"]:
            _recorded(
                await client.call_tool(
                    "search_literature", {**args, "cursor": page_one["next_cursor"]}
                )
            )
    in_process = await _first_hit(client, IN_PROCESS_SEARCH, want="in_process")
    preprint = await _first_hit(client, PREPRINT_SEARCH, want="preprint")
    open_access = await _first_hit(client, OPEN_ACCESS_SEARCH, want="open_access")
    scenario = {
        "in_process_pmid": str(in_process["ids"]["pmid"]),
        "preprint_id": str(preprint["ids"]["ppr"]),
        "open_access_pmid": str(open_access["ids"]["pmid"]),
        "open_access_pmcid": str(open_access["ids"]["pmcid"]),
    }
    for record_ids, full_text in (
        ([f"pmid:{scenario['in_process_pmid']}"], False),
        ([f"pmid:{COHORT_PMID}"], False),
        ([f"ppr:{scenario['preprint_id']}"], False),
        ([f"pmid:{scenario['open_access_pmid']}"], True),
        ([f"pmcid:{scenario['open_access_pmcid']}"], True),
    ):
        _recorded(
            await client.call_tool(
                "fetch_passages", {"record_ids": record_ids, "full_text": full_text}
            )
        )
    scenario.update(await _record_registries(client))
    SCENARIO_FILE.write_text(json.dumps(scenario, indent=1), encoding="utf-8")
    strip_abstracts(FIXTURES_DIR, fetched_pmids(scenario))
