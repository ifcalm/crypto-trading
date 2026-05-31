"""Structured logging with structlog."""

from __future__ import annotations

import logging
from typing import Any, cast

import structlog


def setup_logging(level: str = "INFO", json_output: bool = False) -> None:
    """Configure structlog for structured logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR).
        json_output: If True, output JSON for log aggregation. Default: pretty console.
    """
    shared_processors: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="ISO"),
        structlog.dev.set_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, level.upper()))

    # Silence noisy third-party loggers
    for name in ("websockets", "httpx", "urllib3", "aiosqlite", "ccxt"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name or __name__))
