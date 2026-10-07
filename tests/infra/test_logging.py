import io
import json
import sys

import pytest

from medcite.infra.logging import configure_logging, get_logger


def test_logs_are_json_on_the_current_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_logging("INFO")
    first, second = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stderr", first)
    get_logger("t").info("one", n=1)
    monkeypatch.setattr(sys, "stderr", second)  # e.g. a test runner swapping streams
    get_logger("t").info("two")
    assert json.loads(first.getvalue())["event"] == "one"
    assert json.loads(second.getvalue())["event"] == "two"


def test_level_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_logging("WARNING")
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    get_logger("t").info("hidden")
    get_logger("t").warning("shown")
    assert [json.loads(line)["event"] for line in stream.getvalue().splitlines()] == ["shown"]
    configure_logging("INFO")
