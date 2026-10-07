"""Command line for medcite.

`medcite` serves stdio, `medcite --http` serves localhost, `medcite demo` runs offline.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import anyio
from mcp import Client
from platformdirs import user_cache_dir

from medcite.demo.scenario import FIXTURES_DIR, record_scenario, run_demo
from medcite.infra.fixtures import FixtureTransport, RecordingTransport
from medcite.infra.keys import DEMO_KEY, load_handle_key
from medcite.infra.logging import configure_logging
from medcite.infra.settings import Settings
from medcite.server.app import create_server
from medcite.server.runtime import RuntimeConfig


async def _no_sleep(_: float) -> None:
    return None


def demo_config() -> RuntimeConfig:
    """Fixtures only, public demo key, throwaway cache: identical output on every machine."""
    return RuntimeConfig(
        handle_key=DEMO_KEY,
        cache_path=":memory:",
        transport=FixtureTransport(FIXTURES_DIR),
        sleep=_no_sleep,
        rate_override=1000.0,
    )


def live_config(settings: Settings) -> RuntimeConfig:
    cache_path = (
        settings.cache_path or Path(user_cache_dir("medcite", appauthor=False)) / "cache.sqlite3"
    )
    return RuntimeConfig(
        handle_key=load_handle_key(settings, demo=False),
        cache_path=cache_path,
        ncbi_api_key=settings.ncbi_api_key.get_secret_value() if settings.ncbi_api_key else None,
        ncbi_email=settings.ncbi_email,
        crossref_mailto=settings.crossref_mailto,
        deadline_s=settings.tool_deadline_s,
    )


def _text(result: object) -> str:
    content = getattr(result, "content", [])
    return content[0].text if content and getattr(content[0], "type", "") == "text" else ""


async def _demo(json_out: Path | None) -> None:
    async with Client(create_server(demo_config())) as client:
        run = await run_demo(client)
    print("== search_literature\n" + _text(run.search))
    print("\n== fetch_passages\n" + _text(run.fetch))
    print("\n== answer under audit\n" + run.answer)
    print("\n== audit_answer\n" + _text(run.audit))
    if json_out is not None:
        json_out.write_text(json.dumps(run.audit.structured_content, indent=2), encoding="utf-8")
        print(f"\nreport written to {json_out}")


async def _record(settings: Settings) -> None:
    config = RuntimeConfig(
        handle_key=DEMO_KEY,
        cache_path=":memory:",
        transport=RecordingTransport(FIXTURES_DIR),
        ncbi_api_key=settings.ncbi_api_key.get_secret_value() if settings.ncbi_api_key else None,
        ncbi_email=settings.ncbi_email,
    )
    async with Client(create_server(config)) as client:
        await record_scenario(client)
    print(f"fixtures written to {FIXTURES_DIR}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="medcite", description=__doc__)
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP on 127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--demo", action="store_true", help="serve fixtures with the public demo key"
    )
    commands = parser.add_subparsers(dest="command")
    demo = commands.add_parser("demo", help="run the offline demo and print the audit")
    demo.add_argument("--json-out", type=Path, default=None)
    commands.add_parser("record-fixtures", help="(maintainers) re-record demo fixtures live")
    args = parser.parse_args(argv)

    settings = Settings()
    configure_logging(settings.log_level)
    if args.command == "demo":
        anyio.run(_demo, args.json_out)
        return
    if args.command == "record-fixtures":
        anyio.run(_record, settings)
        return
    server = create_server(demo_config() if args.demo else live_config(settings))
    if args.http:
        server.run("streamable-http", host="127.0.0.1", port=args.port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    sys.exit(main())
