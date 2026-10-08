"""The GitHub workflows keep least privilege and never cut a deployment short."""

from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


@pytest.mark.parametrize("name", ["ci.yml", "live.yml", "pages.yml"])
def test_every_workflow_declares_its_permissions(name: str) -> None:
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    assert "\npermissions:\n  contents: read\n" in text


def test_a_pages_deployment_is_never_cancelled_mid_way() -> None:
    text = (WORKFLOWS / "pages.yml").read_text(encoding="utf-8")
    assert "cancel-in-progress: false" in text
