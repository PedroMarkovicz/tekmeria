"""Server behaviour that needs no recorded upstream data (an empty fixture directory)."""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import anyio
import httpx
import pytest
from jsonschema import Draft202012Validator
from mcp import Client

from tekmerion.contract.handles import HandleCodec, Locator
from tekmerion.infra.fixtures import FixtureTransport
from tekmerion.server.app import create_server
from tekmerion.server.runtime import RuntimeConfig
from tests.conftest import structured

pytestmark = pytest.mark.anyio

KEY = bytes(range(32))
CODEC = HandleCodec(KEY)
TOOL_ORDER = [
    "search_literature",
    "search_trials",
    "search_drug_labels",
    "fetch_passages",
    "resolve_handles",
    "audit_answer",
    "describe_contract",
]


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
async def offline(tmp_path: Path) -> AsyncIterator[Client]:
    config = RuntimeConfig(
        handle_key=KEY,
        cache_path=":memory:",
        transport=FixtureTransport(tmp_path),  # empty: every upstream call fails
        sleep=_no_sleep,
        rate_override=1000.0,
    )
    async with Client(create_server(config)) as client:
        yield client


async def test_tools_listed_in_fixed_order_with_schemas(offline: Client) -> None:
    tools = (await offline.list_tools()).tools
    assert [tool.name for tool in tools] == TOOL_ORDER
    for tool in tools:
        assert tool.output_schema is not None, tool.name
        assert tool.annotations is not None and tool.annotations.read_only_hint is True


async def test_describe_contract_validates_against_its_schema(offline: Client) -> None:
    tools = {tool.name: tool for tool in (await offline.list_tools()).tools}
    data = structured(await offline.call_tool("describe_contract", {}))
    Draft202012Validator(tools["describe_contract"].output_schema or {}).validate(data)
    assert data["verdicts"][0]["name"] == "malformed"


async def test_forged_and_malformed_need_no_network(offline: Client) -> None:
    genuine = CODEC.issue(Locator("pmid", "1", None, "title"))
    forged = genuine[:-1] + ("0" if genuine[-1] != "0" else "1")
    data = structured(await offline.call_tool("resolve_handles", {"handles": [forged, "nope"]}))
    assert [r["status"] for r in data["results"]] == ["forged", "malformed"]


async def test_upstream_down_gives_retryable_error(offline: Client) -> None:
    result = await offline.call_tool("search_literature", {"query": "anything"})
    assert result.is_error and result.structured_content is not None
    assert result.structured_content["code"] == "upstream_unavailable"
    assert result.structured_content["retryable"] is True


async def test_genuine_handle_with_upstream_down_is_unavailable_not_fabricated(
    offline: Client,
) -> None:
    handle = CODEC.issue(Locator("pmid", "1", None, "title"))
    report = structured(
        await offline.call_tool("audit_answer", {"answer_text": f"Claim [{handle}]."})
    )
    assert report["claims"][0]["citations"][0]["verdict"] == "unavailable"
    assert report["summary"]["fabricated"] == 0
    assert report["verdict"] == "incomplete"


@pytest.mark.parametrize(
    "record_id", ["nct:NCT123", "setid:not-a-uuid", "pubmed:1", "pmid:1/title"]
)
async def test_unsupported_or_invalid_record_ids(offline: Client, record_id: str) -> None:
    result = await offline.call_tool("fetch_passages", {"record_ids": [record_id]})
    assert result.is_error and result.structured_content is not None
    assert result.structured_content["code"] == "invalid_input"
    assert result.structured_content["hint"]


async def test_trial_search_needs_a_condition_intervention_or_terms(offline: Client) -> None:
    result = await offline.call_tool("search_trials", {"statuses": ["COMPLETED"]})
    assert result.is_error and result.structured_content is not None
    assert result.structured_content["code"] == "invalid_input"


@pytest.mark.parametrize("args", [{}, {"answer_text": "x", "claims": []}])
async def test_audit_requires_exactly_one_input(offline: Client, args: dict[str, object]) -> None:
    result = await offline.call_tool("audit_answer", args)
    assert result.is_error and result.structured_content is not None
    assert result.structured_content["code"] == "invalid_input"


async def test_prompt_and_resource_templates(offline: Client) -> None:
    prompt = await offline.get_prompt("cite_by_handle", {"question": "Does MMR cause autism?"})
    text = prompt.messages[0].content.text  # type: ignore[union-attr]
    assert "Does MMR cause autism?" in text and "[handle" in text
    templates = (await offline.list_resource_templates()).resource_templates
    assert [t.uri_template for t in templates] == [
        "evidence://record/{+record_id}",
        "evidence://passage/{+handle}",
    ]


async def test_server_identity(offline: Client) -> None:
    assert offline.server_info is not None and offline.server_info.name == "tekmerion"


class Hanging(httpx.AsyncBaseTransport):
    """An upstream that never answers."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await anyio.sleep(3600)
        raise AssertionError("unreachable")


async def test_audit_survives_an_upstream_that_never_answers() -> None:
    config = RuntimeConfig(
        handle_key=KEY,
        cache_path=":memory:",
        transport=Hanging(),
        sleep=_no_sleep,
        rate_override=1000.0,
        deadline_s=2.0,
    )
    genuine = CODEC.issue(Locator("pmid", "1", None, "title"))
    forged = genuine[:-1] + ("0" if genuine[-1] != "0" else "1")
    async with Client(create_server(config)) as client:
        result = await client.call_tool(
            "audit_answer", {"answer_text": f"One [{genuine}]. Two [{forged}]."}
        )
    report = structured(result)
    verdicts = [c["citations"][0]["verdict"] for c in report["claims"]]
    assert verdicts == ["unavailable", "forged"]
    assert report["verdict"] == "fail"


async def test_answer_over_the_limits_is_invalid_input_with_a_hint(offline: Client) -> None:
    group = "; ".join(f"pmid:{i}/title~abcde" for i in range(11))
    result = await offline.call_tool("audit_answer", {"answer_text": f"Claim [{group}]."})
    assert result.is_error and result.structured_content is not None
    assert result.structured_content["code"] == "invalid_input"
    assert "10 citations" in (result.structured_content["hint"] or "")


@pytest.mark.parametrize("args", [{"answer_text": "  "}, {"claims": []}])
async def test_an_answer_with_nothing_to_audit_is_invalid_input(
    offline: Client, args: dict[str, object]
) -> None:
    result = await offline.call_tool("audit_answer", args)
    assert result.is_error and result.structured_content is not None
    assert result.structured_content["code"] == "invalid_input"
    assert "no sentences" in result.structured_content["message"]


async def test_an_internal_error_is_logged_as_an_error_with_its_traceback(
    offline: Client, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken() -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr("tekmerion.server.app.contract_description", broken)
    capsys.readouterr()
    result = await offline.call_tool("describe_contract", {})
    assert result.is_error
    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    (line,) = [line for line in lines if line.get("tool") == "describe_contract"]
    assert line["level"] == "error"
    assert "RuntimeError: boom" in line["exception"]


async def test_an_error_echoing_the_request_keeps_it_out_of_the_log(
    offline: Client, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    result = await offline.call_tool("fetch_passages", {"record_ids": ["pmid:my private query"]})
    assert result.is_error
    err = capsys.readouterr().err
    assert "my private query" not in err
    (line,) = [json.loads(line) for line in err.splitlines() if '"fetch_passages"' in line]
    assert line["code"] == "invalid_input" and "message" not in line
