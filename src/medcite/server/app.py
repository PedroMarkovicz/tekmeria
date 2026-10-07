"""The MCP surface: tools, resources and the prompt (spec section 6). Thin by design."""

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

import anyio
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field, ValidationError

from medcite import __version__
from medcite.contract.audit import audit, check_handles
from medcite.contract.audit_models import AuditReport, ClaimIn, FailOn
from medcite.contract.citations import parse_answer_text
from medcite.contract.descriptions import CITE_BY_HANDLE_INSTRUCTIONS, contract_description
from medcite.contract.models import StudyDesign, ToolError
from medcite.contract.tool_models import (
    ContractDescription,
    FetchResult,
    LiteratureSearchResult,
    ResolveResult,
)
from medcite.infra.http import (
    UpstreamNotFoundError,
    UpstreamResponseError,
    UpstreamUnavailableError,
)
from medcite.infra.logging import get_logger
from medcite.server import render
from medcite.server.cursor import decode_cursor, encode_cursor
from medcite.server.runtime import AppContext, RuntimeConfig, open_runtime
from medcite.sources.errors import InvalidInputError
from medcite.sources.pubmed import LiteratureQuery

log = get_logger("medcite.server")

INSTRUCTIONS = (
    "medcite returns biomedical evidence as passages with handles such as "
    "pmid:30831578/abstract.7~7hq3k. Cite only handles you received, in square brackets after "
    "each claim, and check drafts with audit_answer. See the cite_by_handle prompt."
)
DATE = r"^\d{4}(-\d{2}-\d{2})?$"
AUDIT_LIMITS_HINT = (
    "Limits: at most 10 citations per sentence, quotes up to 1000 characters, sentences up "
    "to 4000 characters. Split long sentences or shorten quotes."
)
READ_ONLY = ToolAnnotations(read_only_hint=True)
READ_ONLY_OPEN = ToolAnnotations(read_only_hint=True, open_world_hint=True)
READ_ONLY_IDEMPOTENT = ToolAnnotations(read_only_hint=True, idempotent_hint=True)

Work = Callable[[AppContext], Awaitable[tuple[BaseModel, str]]]


def _app(ctx: Context[Any, Any]) -> AppContext:
    app = ctx.request_context.lifespan_context
    if not isinstance(app, AppContext):
        raise RuntimeError("server lifespan did not provide an AppContext")
    return app


def _error(code: str, message: str, *, retryable: bool, hint: str | None = None) -> ToolError:
    return ToolError.model_validate(
        {"code": code, "message": message, "retryable": retryable, "hint": hint}
    )


async def _run(ctx: Context[Any, Any], tool: str, work: Work) -> CallToolResult:
    """Run a tool body under the deadline and turn failures into ToolError results."""
    app = _app(ctx)
    started = time.perf_counter()
    try:
        with anyio.fail_after(app.deadline_s):
            model, text = await work(app)
    except TimeoutError:
        error = _error(
            "deadline_exceeded", f"{tool} took longer than {app.deadline_s:g}s", retryable=True
        )
    except InvalidInputError as exc:
        error = _error("invalid_input", str(exc), retryable=False, hint=exc.hint)
    except UpstreamUnavailableError as exc:
        error = _error("upstream_unavailable", str(exc), retryable=True, hint="Retry in a moment.")
    except UpstreamNotFoundError as exc:
        error = _error("not_found", str(exc), retryable=False)
    except UpstreamResponseError as exc:
        error = _error("upstream_error", str(exc), retryable=False)
    except Exception as exc:
        log.exception("tool_failed", tool=tool)
        error = _error("internal", f"{type(exc).__name__}: {exc}", retryable=False)
    else:
        log.info("tool_ok", tool=tool, ms=round((time.perf_counter() - started) * 1000))
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content=model.model_dump(mode="json"),
        )
    log.warning("tool_error", tool=tool, code=error.code, message=error.message)
    return CallToolResult(
        content=[TextContent(type="text", text=render.render_error(error))],
        structured_content=error.model_dump(mode="json"),
        is_error=True,
    )


def create_server(config: RuntimeConfig) -> MCPServer[AppContext]:
    @asynccontextmanager
    async def lifespan(_: MCPServer[AppContext]) -> AsyncIterator[AppContext]:
        async with open_runtime(config) as app:
            yield app

    server: MCPServer[AppContext] = MCPServer(
        name="medcite", version=__version__, instructions=INSTRUCTIONS, lifespan=lifespan
    )

    # Registration order is the tools/list order (spec 6.1).
    @server.tool(annotations=READ_ONLY_OPEN)
    async def search_literature(
        query: Annotated[
            str, Field(min_length=1, max_length=500, description="PubMed syntax is accepted")
        ],
        ctx: Context[Any, Any],
        date_from: Annotated[
            str | None, Field(pattern=DATE, description="YYYY or YYYY-MM-DD")
        ] = None,
        date_to: Annotated[
            str | None, Field(pattern=DATE, description="YYYY or YYYY-MM-DD")
        ] = None,
        study_designs: Annotated[
            list[StudyDesign] | None, Field(description="From MEDLINE publication types")
        ] = None,
        peer_reviewed_only: bool = False,
        open_access_only: bool = False,
        exclude_retracted: bool = False,
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
        cursor: str | None = None,
    ) -> Annotated[CallToolResult, LiteratureSearchResult]:
        """Search PubMed. Returns record summaries; each has the title passage's handle."""
        query_args: dict[str, object] = {
            "query": query,
            "date_from": date_from,
            "date_to": date_to,
            "study_designs": study_designs,
            "peer_reviewed_only": peer_reviewed_only,
            "open_access_only": open_access_only,
            "exclude_retracted": exclude_retracted,
        }

        async def work(app: AppContext) -> tuple[BaseModel, str]:
            offset = (
                decode_cursor(app.codec, "search_literature", query_args, cursor) if cursor else 0
            )
            page = await app.service.search_literature(
                LiteratureQuery(
                    query=query,
                    date_from=date_from,
                    date_to=date_to,
                    study_designs=tuple(study_designs or ()),
                    peer_reviewed_only=peer_reviewed_only,
                    open_access_only=open_access_only,
                    exclude_retracted=exclude_retracted,
                ),
                offset=offset,
                limit=limit,
            )
            next_cursor = (
                encode_cursor(app.codec, "search_literature", query_args, page.next_offset)
                if page.next_offset is not None
                else None
            )
            result = LiteratureSearchResult(
                hits=page.hits,
                total=page.total,
                next_cursor=next_cursor,
                source_status={"pubmed": "ok"},
            )
            return result, render.render_search(result)

        return await _run(ctx, "search_literature", work)

    @server.tool(annotations=READ_ONLY_OPEN)
    async def fetch_passages(
        record_ids: Annotated[
            list[str], Field(min_length=1, max_length=20, description="e.g. pmid:30831578")
        ],
        ctx: Context[Any, Any],
        sections: Annotated[
            list[str] | None, Field(description="Path prefixes to keep, e.g. ['abstract']")
        ] = None,
        max_passages: Annotated[int, Field(ge=1, le=200)] = 60,
        cursor: str | None = None,
    ) -> Annotated[CallToolResult, FetchResult]:
        """Fetch records and their passages. Every passage carries a citable handle."""
        query_args: dict[str, object] = {"record_ids": record_ids, "sections": sections}

        async def work(app: AppContext) -> tuple[BaseModel, str]:
            offset = decode_cursor(app.codec, "fetch_passages", query_args, cursor) if cursor else 0
            page = await app.service.fetch_passages(
                record_ids, sections=sections, max_passages=max_passages, offset=offset
            )
            next_cursor = (
                encode_cursor(app.codec, "fetch_passages", query_args, page.next_offset)
                if page.next_offset is not None
                else None
            )
            result = FetchResult(
                records=page.records,
                not_found=page.not_found,
                next_cursor=next_cursor,
                source_status={"pubmed": "ok"},
            )
            return result, render.render_fetch(result)

        return await _run(ctx, "fetch_passages", work)

    @server.tool(annotations=READ_ONLY_IDEMPOTENT)
    async def resolve_handles(
        handles: Annotated[list[str], Field(min_length=1, max_length=50)],
        ctx: Context[Any, Any],
    ) -> Annotated[CallToolResult, ResolveResult]:
        """Look up the exact passage behind each handle, and whether the handle is genuine."""

        async def work(app: AppContext) -> tuple[BaseModel, str]:
            checks = await check_handles(handles, codec=app.codec, resolver=app.service)
            return ResolveResult(results=checks), render.render_checks(checks)

        return await _run(ctx, "resolve_handles", work)

    @server.tool(annotations=READ_ONLY_IDEMPOTENT)
    async def audit_answer(
        ctx: Context[Any, Any],
        answer_text: Annotated[
            str | None, Field(max_length=20000, description="Answer with [handle] citations")
        ] = None,
        claims: Annotated[list[ClaimIn] | None, Field(max_length=100)] = None,
        fail_on: list[FailOn] | None = None,
    ) -> Annotated[CallToolResult, AuditReport]:
        """Audit an answer's citations: genuine handle, record still exists, quotes verbatim."""

        async def work(app: AppContext) -> tuple[BaseModel, str]:
            if (answer_text is None) == (claims is None):
                raise InvalidInputError(
                    "provide exactly one of answer_text or claims",
                    hint="Use answer_text for prose with [handle] citations.",
                )
            try:
                parsed = parse_answer_text(answer_text) if answer_text is not None else claims or []
            except ValidationError as exc:
                raise InvalidInputError(
                    f"answer exceeds a limit: {exc.errors()[0]['msg']}", hint=AUDIT_LIMITS_HINT
                ) from exc
            report = await audit(parsed, codec=app.codec, resolver=app.service, fail_on=fail_on)
            return report, render.render_audit(report)

        return await _run(ctx, "audit_answer", work)

    @server.tool(annotations=READ_ONLY)
    async def describe_contract(
        ctx: Context[Any, Any],
    ) -> Annotated[CallToolResult, ContractDescription]:
        """The citation contract: handle grammar, verdicts, flags, metrics and instructions."""

        async def work(app: AppContext) -> tuple[BaseModel, str]:
            description = contract_description()
            return description, render.render_contract(description)

        return await _run(ctx, "describe_contract", work)

    @server.resource("evidence://record/{+record_id}", mime_type="application/json")
    async def record_resource(record_id: str, ctx: Context[Any, Any]) -> str:
        """A record as JSON."""
        app = _app(ctx)
        try:
            page = await app.service.fetch_passages(
                [record_id], sections=["title"], max_passages=1, offset=0
            )
        except InvalidInputError as exc:
            raise ResourceNotFoundError(str(exc)) from exc
        if not page.records:
            raise ResourceNotFoundError(f"record {record_id} not found")
        return page.records[0].record.model_dump_json()

    @server.resource("evidence://passage/{+handle}", mime_type="application/json")
    async def passage_resource(handle: str, ctx: Context[Any, Any]) -> str:
        """The passage behind a handle, as JSON. Unknown or forged handles are not found."""
        app = _app(ctx)
        (check,) = await check_handles([handle], codec=app.codec, resolver=app.service)
        if check.status != "ok" or check.passage is None:
            raise ResourceNotFoundError(f"handle is {check.status}")
        return check.passage.model_dump_json()

    @server.prompt()
    def cite_by_handle(question: str) -> str:
        """Instructions for answering a question with handle citations."""
        return f"{CITE_BY_HANDLE_INSTRUCTIONS}\nQuestion: {question}"

    return server
