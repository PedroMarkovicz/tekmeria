"""Structured JSON logs on stderr; stdout belongs to the stdio transport (spec 9.3)."""

import logging
import sys
from typing import TextIO, cast

import structlog


class _Stderr:
    """Looks up sys.stderr on every write, so a replaced or closed stream is never cached."""

    def write(self, message: str) -> int:
        return sys.stderr.write(message)

    def flush(self) -> None:
        sys.stderr.flush()


def configure_logging(level: str = "INFO") -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines include query strings
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=cast(TextIO, _Stderr())),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.typing.FilteringBoundLogger:
    return structlog.get_logger(name)
