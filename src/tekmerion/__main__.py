"""Command line for tekmerion.

`tekmerion` serves stdio, `tekmerion --http` serves localhost, `tekmerion demo` runs offline.
"""

import argparse
import json
import sys
import tempfile
import webbrowser
from collections.abc import Sequence
from pathlib import Path

import anyio
from mcp import Client
from platformdirs import user_cache_dir
from pydantic import SecretStr

from tekmerion.demo.scenario import FIXTURES_DIR, record_scenario, run_demo
from tekmerion.evaluation.run import evaluate_snapshot, render_report
from tekmerion.evaluation.snapshot import load_snapshot
from tekmerion.infra.fixtures import FixtureTransport, RecordingTransport
from tekmerion.infra.keys import DEMO_KEY, load_handle_key
from tekmerion.infra.logging import configure_logging
from tekmerion.infra.settings import Settings
from tekmerion.server.app import create_server
from tekmerion.server.runtime import RuntimeConfig, open_runtime
from tekmerion.ui import standalone_page


async def _no_sleep(_: float) -> None:
    return None


def _secret(value: SecretStr | None) -> str | None:
    return value.get_secret_value() if value else None


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
        settings.cache_path or Path(user_cache_dir("tekmerion", appauthor=False)) / "cache.sqlite3"
    )
    return RuntimeConfig(
        handle_key=load_handle_key(settings, demo=False),
        cache_path=cache_path,
        ncbi_api_key=settings.ncbi_api_key.get_secret_value() if settings.ncbi_api_key else None,
        ncbi_email=settings.ncbi_email,
        crossref_mailto=settings.crossref_mailto,
        openfda_api_key=_secret(settings.openfda_api_key),
        deadline_s=settings.tool_deadline_s,
    )


def _text(result: object) -> str:
    content = getattr(result, "content", [])
    return content[0].text if content and getattr(content[0], "type", "") == "text" else ""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def _demo(json_out: Path | None, html_out: Path | None, open_page: bool) -> None:
    async with Client(create_server(demo_config())) as client:
        run = await run_demo(client)
    print("== search_literature\n" + _text(run.search))
    print("\n== fetch_passages\n" + _text(run.fetch))
    print("\n== answer under audit\n" + run.answer)
    print("\n== audit_answer\n" + _text(run.audit))
    report = json.dumps(run.audit.structured_content, indent=2)
    if json_out is not None:
        _write(json_out, report)
        print(f"\nreport written to {json_out}")
    if html_out is not None or open_page:
        page = html_out or Path(tempfile.mkdtemp(prefix="tekmerion-")) / "demo-report.html"
        _write(page, standalone_page(report))
        print(f"audit view written to {page}")
        if open_page and not webbrowser.open(page.resolve().as_uri()):
            print(f"could not open a browser; open {page} yourself")


async def _evaluate(seed: int, per_category: int, json_out: Path | None) -> bool:
    """Audit the planted cases and whole answers offline and print the report; True when
    every one came out as planted."""
    config = demo_config()
    async with open_runtime(config) as app:
        snapshot = await load_snapshot(app.service)
    report = await evaluate_snapshot(
        snapshot, config.handle_key, seed=seed, per_category=per_category
    )
    print(render_report(report))
    if json_out is not None:
        _write(json_out, report.model_dump_json(indent=2))
        print(f"\nreport written to {json_out}")
    return report.passed()


def _at_least_one(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


async def _record(settings: Settings) -> None:
    config = RuntimeConfig(
        handle_key=DEMO_KEY,
        cache_path=":memory:",
        transport=RecordingTransport(FIXTURES_DIR),
        ncbi_api_key=settings.ncbi_api_key.get_secret_value() if settings.ncbi_api_key else None,
        ncbi_email=settings.ncbi_email,
        openfda_api_key=_secret(settings.openfda_api_key),
    )
    async with Client(create_server(config)) as client:
        await record_scenario(client)
    print(f"fixtures written to {FIXTURES_DIR}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tekmerion", description=__doc__)
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP on 127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--demo", action="store_true", help="serve fixtures with the public demo key"
    )
    commands = parser.add_subparsers(dest="command")
    demo = commands.add_parser("demo", help="run the offline demo and print the audit")
    demo.add_argument("--json-out", type=Path, default=None, help="write the report as JSON")
    demo.add_argument(
        "--html-out", type=Path, default=None, help="write the audit view with the report"
    )
    demo.add_argument("--open", action="store_true", help="open the audit view in a browser")
    evaluate = commands.add_parser(
        "evaluate", help="audit planted citation errors offline and score the verdicts"
    )
    evaluate.add_argument("--seed", type=int, default=0, help="seed for the planted cases")
    evaluate.add_argument(
        "--per-category", type=_at_least_one, default=25, help="cases per category"
    )
    evaluate.add_argument("--json-out", type=Path, default=None, help="write the report as JSON")
    commands.add_parser("record-fixtures", help="(maintainers) re-record demo fixtures live")
    args = parser.parse_args(argv)

    settings = Settings()
    configure_logging(settings.log_level)
    if args.command == "demo":
        anyio.run(_demo, args.json_out, args.html_out, args.open)
        return
    if args.command == "evaluate":
        if not anyio.run(_evaluate, args.seed, args.per_category, args.json_out):
            sys.exit(1)
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
