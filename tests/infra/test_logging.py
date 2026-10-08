import io
import json
import sys

import pytest
import structlog

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


def test_a_logged_exception_keeps_its_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_logging("INFO")
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    try:
        raise ValueError("boom")
    except ValueError:
        get_logger("t").exception("failed")
    assert "ValueError: boom" in json.loads(stream.getvalue())["exception"]


def test_logs_go_to_stderr_even_before_configuration(capsys: pytest.CaptureFixture[str]) -> None:
    structlog.reset_defaults()
    try:
        get_logger("t").info("early")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert json.loads(captured.err)["event"] == "early"
    finally:
        configure_logging("INFO")
