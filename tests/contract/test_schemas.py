from pathlib import Path

from tekmeria.contract.audit_models import AuditReport
from tekmeria.contract.schemas import SCHEMAS, export, schema_text

REPO = Path(__file__).resolve().parents[2]


def test_committed_schemas_match_the_models() -> None:
    for name, model in SCHEMAS.items():
        committed = (REPO / "schemas" / name).read_text(encoding="utf-8")
        assert committed == schema_text(model), (
            f"schemas/{name} is stale; run `uv run python -m tekmeria.contract.schemas`"
        )


def test_export_writes_every_schema(tmp_path: Path) -> None:
    written = export(tmp_path / "out")
    assert sorted(p.name for p in written) == sorted(SCHEMAS)
    assert '"title": "AuditReport"' in written[0].read_text(encoding="utf-8")


def test_the_audit_view_test_fixtures_are_valid_reports() -> None:
    fixtures = sorted((REPO / "web" / "src" / "test" / "fixtures").glob("*.json"))
    assert fixtures, "web/src/test/fixtures has no reports"
    for path in fixtures:
        AuditReport.model_validate_json(path.read_text(encoding="utf-8"))
