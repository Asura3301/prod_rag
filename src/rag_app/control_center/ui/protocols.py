"""Narrow interface consumed by the terminal dashboard.

Keeping the UI behind this protocol makes it straightforward to exercise with a fake
control center and prevents widgets from learning process-management details.
"""

from __future__ import annotations

from typing import Any, Protocol


class ControlCenterFacade(Protocol):
    """Async application facade required by :class:`ControlCenterApp`."""

    config: Any

    async def start_all(self) -> Any:
        """Start every enabled service in dependency order."""

    async def stop_all(self) -> Any:
        """Stop every managed service in reverse dependency order."""

    async def restart_all(self) -> Any:
        """Restart every enabled service."""

    async def restart_failed(self) -> Any:
        """Perform one bounded recovery pass for failed managed services."""

    async def status_all(self) -> Any:
        """Return the current snapshot for each configured service."""

    async def start(self, name: str) -> Any:
        """Start one service."""

    async def stop(self, name: str) -> Any:
        """Stop one service."""

    async def restart(self, name: str) -> Any:
        """Restart one service."""

    async def status(self, name: str) -> Any:
        """Return one service snapshot."""

    async def system_check(self) -> Any:
        """Run configured host and dependency diagnostics."""

    async def system_metrics(self) -> Any:
        """Sample CPU, memory, and optional GPU metrics."""

    async def tail_logs(self, name: str, lines: int = 200) -> Any:
        """Return the latest log lines for a service."""
