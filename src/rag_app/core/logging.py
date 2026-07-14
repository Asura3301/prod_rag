"""Structured logging configured only when explicitly requested."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

_STANDARD_ATTRIBUTES = frozenset(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
}


class JsonFormatter(logging.Formatter):
    """Format standard records and user-supplied extras as one JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRIBUTES and not key.startswith("_"):
                payload[key] = _json_safe(value)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def setup_logging(
    level: str | int = "INFO",
    *,
    json_output: bool = True,
    stream: TextIO | None = None,
) -> None:
    """Configure the root logger idempotently without import-time side effects."""

    resolved_level = _resolve_level(level)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if json_output
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolved_level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def _resolve_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    resolved = logging.getLevelName(level.upper())
    if not isinstance(resolved, int):
        raise ValueError(f"invalid logging level: {level}")
    return resolved


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return value
