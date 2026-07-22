"""Dependency-light presentation helpers for the terminal dashboard."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from enum import Enum
from typing import Any

STATUS_ORDER = {"error": 0, "starting": 1, "running": 2, "stopped": 3, "unknown": 4}
_TERMINAL_ESCAPE = re.compile(r"\x1b(?:\][^\x07]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|.)")
_TERMINAL_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1a\x1c-\x1f\x7f-\x9f]")


def as_mapping(value: Any) -> dict[str, Any]:
    """Convert Pydantic models and mapping-shaped values to a plain mapping."""

    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return {str(key): item for key, item in dumped.items()}
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, Mapping):
        return {
            str(key): _enum_value(item)
            for key, item in attributes.items()
            if not str(key).startswith("_")
        }
    return {}


def service_snapshots(value: Any) -> list[dict[str, Any]]:
    """Normalize a status or operation result into service snapshot dictionaries."""

    if value is None:
        return []
    root = as_mapping(value)
    services = root.get("services") if root else None
    if services is not None:
        value = services
    if isinstance(value, Mapping):
        value = list(value.values())
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return [as_mapping(item) for item in value]
    snapshot = as_mapping(value)
    return [snapshot] if snapshot else []


def normalized_status(value: Any) -> str:
    """Return one of the dashboard's stable status names."""

    raw = str(_enum_value(value) or "unknown").lower()
    if "." in raw:
        raw = raw.rsplit(".", 1)[-1]
    return raw if raw in STATUS_ORDER else "unknown"


def human_bytes(value: Any) -> str:
    """Format a byte count without raising on missing telemetry."""

    try:
        size = float(value)
    except (TypeError, ValueError):
        return "—"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if abs(size) < 1024.0 or unit == units[-1]:
            precision = 0 if unit in {"B", "KiB", "MiB"} else 1
            return f"{size:.{precision}f} {unit}"
        size /= 1024.0
    return "—"


def human_duration(value: Any) -> str:
    """Format a duration in seconds for compact service table display."""

    try:
        seconds = max(0, int(float(value)))
    except (TypeError, ValueError):
        return "—"
    days, seconds = divmod(seconds, 86_400)
    hours, seconds = divmod(seconds, 3_600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def percent(value: Any) -> str:
    """Format an optional percentage."""

    try:
        return f"{float(value):.0f}%"
    except (TypeError, ValueError):
        return "—"


def compact_text(value: Any, limit: int = 90) -> str:
    """Flatten and bound status text so rows remain readable."""

    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"


def safe_log_line(value: Any) -> str:
    """Remove terminal controls from untrusted service log output."""

    text = str(value).replace("\r", " ").replace("\n", " ")
    text = _TERMINAL_ESCAPE.sub("", text)
    return _TERMINAL_CONTROL.sub("", text)


def overall_status(snapshots: Iterable[Mapping[str, Any]]) -> tuple[str, str]:
    """Return a status name and concise summary for the project header."""

    states = [normalized_status(item.get("status")) for item in snapshots]
    if not states or all(state == "stopped" for state in states):
        return "stopped", "All services stopped"
    if "error" in states:
        return "error", f"{states.count('error')} service(s) need attention"
    if "starting" in states:
        return "starting", f"{states.count('starting')} service(s) starting"
    running = states.count("running")
    if running == len(states):
        return "running", f"All {running} services operational"
    return "starting", f"{running}/{len(states)} services running"


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value
