"""Dependency-light tests for dashboard presentation normalization."""

from __future__ import annotations

import asyncio
from enum import StrEnum
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel
from rich.text import Text
from textual.widgets import DataTable, Static, TabbedContent

from rag_app.control_center.ui.dashboard import ControlCenterApp, MetricCard
from rag_app.control_center.ui.presentation import (
    human_bytes,
    human_duration,
    normalized_status,
    overall_status,
    safe_log_line,
    service_snapshots,
)


class _Status(StrEnum):
    RUNNING = "running"


class _Snapshot(BaseModel):
    name: str
    status: _Status


class _Operation(BaseModel):
    success: bool
    services: list[_Snapshot]


class _FakeControlCenter:
    def __init__(self) -> None:
        service = SimpleNamespace(
            name="lm_studio",
            display_name="AI Engine",
            description="Local inference",
            driver="lm_studio",
            enabled=True,
            dependencies=(),
            host="127.0.0.1",
            port=1234,
        )
        self.config = SimpleNamespace(project_name="Test project", services=(service,))
        self.actions: list[str] = []

    async def status_all(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "lm_studio",
                "display_name": "AI Engine",
                "status": "running",
                "detail": "Model loaded",
                "driver": "lm_studio",
                "uptime_seconds": 61,
                "metadata": {"model": "test-model"},
            }
        ]

    async def system_metrics(self) -> dict[str, Any]:
        return {
            "cpu_percent": 12,
            "memory_percent": 34,
            "memory_used_bytes": 4 * 1024**3,
            "memory_total_bytes": 16 * 1024**3,
            "gpu": [],
        }

    async def ai_metrics(self) -> dict[str, Any]:
        return {
            "model": "test-model",
            "loaded": True,
            "requests_per_minute": None,
            "latency_last_ms": None,
            "detail": "Model loaded",
        }

    async def system_check(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "Python",
                "ok": True,
                "severity": "required",
                "detail": "Python available",
                "solution": None,
            },
            {
                "name": "CUDA",
                "ok": False,
                "severity": "optional",
                "detail": "CUDA unavailable; CPU mode is supported",
                "solution": "Install CUDA only when GPU acceleration is required",
            },
        ]

    async def tail_logs(self, name: str, lines: int = 200) -> list[str]:
        del name, lines
        return ["INFO Model loaded"]

    async def start_all(self) -> dict[str, Any]:
        return self._operation("start_all")

    async def stop_all(self) -> dict[str, Any]:
        return self._operation("stop_all")

    async def restart_all(self) -> dict[str, Any]:
        return self._operation("restart_all")

    async def restart_failed(self) -> dict[str, Any]:
        return self._operation("restart_failed")

    async def start(self, name: str) -> dict[str, Any]:
        del name
        return self._operation("start")

    async def stop(self, name: str) -> dict[str, Any]:
        del name
        return self._operation("stop")

    async def restart(self, name: str) -> dict[str, Any]:
        del name
        return self._operation("restart")

    async def status(self, name: str) -> dict[str, Any]:
        del name
        return (await self.status_all())[0]

    def _operation(self, action: str) -> dict[str, Any]:
        self.actions.append(action)
        return {"success": True, "action": action, "services": [], "errors": []}


class _BlockingControlCenter(_FakeControlCenter):
    def __init__(self) -> None:
        super().__init__()
        self.operation_started = asyncio.Event()
        self.release_operation = asyncio.Event()

    async def start_all(self) -> dict[str, Any]:
        self.actions.append("start_all")
        self.operation_started.set()
        await self.release_operation.wait()
        return {"success": True, "action": "start_all", "services": [], "errors": []}


def test_service_snapshots_accept_status_and_operation_models() -> None:
    snapshot = _Snapshot(name="api", status=_Status.RUNNING)

    assert service_snapshots([snapshot]) == [{"name": "api", "status": "running"}]
    assert service_snapshots(_Operation(success=True, services=[snapshot])) == [
        {"name": "api", "status": "running"}
    ]


def test_dashboard_status_summary_is_deterministic() -> None:
    assert normalized_status(_Status.RUNNING) == "running"
    assert overall_status([{"status": "running"}, {"status": "running"}]) == (
        "running",
        "All 2 services operational",
    )
    assert overall_status([{"status": "running"}, {"status": "error"}]) == (
        "error",
        "1 service(s) need attention",
    )


def test_compact_metric_formatting_handles_missing_values() -> None:
    assert human_bytes(24 * 1024**3) == "24.0 GiB"
    assert human_bytes(None) == "—"
    assert human_duration(3_661) == "1h 1m"
    assert human_duration(None) == "—"
    assert safe_log_line("\x1b]0;owned\x07\x1b[31mERROR\x1b[0m\x07") == "ERROR"


@pytest.mark.asyncio
async def test_dashboard_mounts_and_runs_bounded_recovery_action() -> None:
    center = _FakeControlCenter()
    app = ControlCenterApp(center, refresh_interval=60, log_refresh_interval=60)

    async with app.run_test(size=(140, 48)) as pilot:
        await pilot.pause(0.2)
        assert app.query_one("#services-table", DataTable).row_count == 1
        assert app.query_one("#main-tabs", TabbedContent).active == "overview"
        assert "12%" in str(app.query_one("#metric-cpu", MetricCard).render())
        assert "1/1" in str(app.query_one("#metric-services", MetricCard).render())

        app.action_show_diagnostics()
        await pilot.pause(0.2)
        diagnostic_summary = str(app.query_one("#diagnostic-summary", Static).render())
        warning_outcome = app.query_one("#diagnostics-table", DataTable).get_row_at(1)[1]
        assert "All required checks passed; 1 optional warning" in diagnostic_summary
        assert isinstance(warning_outcome, Text)
        assert warning_outcome.plain == "⚠ Warning"
        assert warning_outcome.style == "#F6C85F"

        app.action_restart_failed()
        await pilot.pause(0.2)

    assert center.actions == ["restart_failed"]


@pytest.mark.asyncio
async def test_duplicate_lifecycle_action_does_not_cancel_active_mutation() -> None:
    center = _BlockingControlCenter()
    app = ControlCenterApp(center, refresh_interval=60, log_refresh_interval=60)

    async with app.run_test(size=(140, 48)) as pilot:
        await pilot.pause(0.1)
        app.action_start_all()
        await asyncio.wait_for(center.operation_started.wait(), timeout=1)

        app.action_stop_all()
        await app.action_quit()
        await pilot.pause(0.1)

        assert center.actions == ["start_all"]
        assert app._operation_running is True
        assert app.is_running is True

        center.release_operation.set()
        await pilot.pause(0.2)

    assert center.actions == ["start_all"]
