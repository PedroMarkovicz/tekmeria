"""JSON Schemas exported from the contract models; the audit view's types are generated
from them (spec section 10). Run `python -m medcite.contract.schemas [DIR]` after a model
change; a test fails while the committed schemas are stale."""

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from medcite.contract.audit_models import AuditReport

SCHEMAS: dict[str, type[BaseModel]] = {"audit_report.schema.json": AuditReport}
DEFAULT_DIR = Path("schemas")


def schema_text(model: type[BaseModel]) -> str:
    """The model's serialization schema as stable, diff-friendly JSON."""
    schema = model.model_json_schema(mode="serialization")
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def export(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, model in SCHEMAS.items():
        path = directory / name
        path.write_text(schema_text(model), encoding="utf-8", newline="\n")
        written.append(path)
    return written


if __name__ == "__main__":
    for path in export(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DIR):
        print(f"wrote {path}")
