"""The offline demo: search, fetch, then audit an answer with six planted cases (spec 12)."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.types import CallToolResult

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


async def find_in_process_pmid(client: Client) -> str:
    """A PMID not yet MEDLINE-indexed, found live while recording."""
    result = await client.call_tool(
        "search_literature", {"query": "autism[tiab] AND inprocess[sb]", "limit": 1}
    )
    hits = _structured(result)["hits"]
    if not hits:
        raise DemoError("no in-process article found")
    return str(hits[0]["ids"]["pmid"])


async def record_scenario(client: Client) -> None:
    """Make every call the demo and the tests replay. Run against the live network."""
    await run_demo(client)
    _structured(await client.call_tool("search_literature", TEST_SEARCH_ARGS))
    page_one = _structured(
        await client.call_tool("search_literature", {"query": DEMO_QUERY, "limit": 2})
    )
    if page_one["next_cursor"]:
        await client.call_tool(
            "search_literature",
            {"query": DEMO_QUERY, "limit": 2, "cursor": page_one["next_cursor"]},
        )
    in_process = await find_in_process_pmid(client)
    await client.call_tool("fetch_passages", {"record_ids": [f"pmid:{in_process}"]})
    await client.call_tool("fetch_passages", {"record_ids": [f"pmid:{COHORT_PMID}"]})
    SCENARIO_FILE.write_text(
        json.dumps({"in_process_pmid": in_process}, indent=1), encoding="utf-8"
    )
