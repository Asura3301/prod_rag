"""Dependency-aware, ownership-safe service orchestration facade."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from rag_app.control_center.config import load_control_center_config
from rag_app.control_center.diagnostics import (
    collect_ai_metrics,
    collect_system_metrics,
    run_system_checks,
)
from rag_app.control_center.drivers import DriverContext, ServiceDriver, build_drivers
from rag_app.control_center.health import probe_health
from rag_app.control_center.models import (
    AIMetrics,
    ControlCenterConfig,
    DiagnosticCheck,
    DriverResult,
    DriverType,
    OperationResult,
    ServiceConfig,
    ServiceSnapshot,
    ServiceStatus,
    SystemMetrics,
)
from rag_app.control_center.state import LifecycleLock, StateStore


class ControlCenterError(RuntimeError):
    pass


class ControlCenter:
    """Stable public API shared by the headless CLI and Textual application."""

    def __init__(self, config: ControlCenterConfig) -> None:
        self.config = config
        self._services = {service.name: service for service in config.services}
        self._order = _topological_order(config.services)
        self._state = StateStore(config.runtime_dir / "state.json")
        self._lifecycle_lock = LifecycleLock(config.runtime_dir / "lifecycle.lock")
        context = DriverContext(
            project_root=config.project_root,
            logs_dir=config.logs_dir,
            state_store=self._state,
            shutdown_timeout_seconds=config.shutdown_timeout_seconds,
        )
        self._drivers = build_drivers(context)
        self._operation_lock = asyncio.Lock()

    @classmethod
    def from_path(
        cls,
        path: str | Path | None = None,
        *,
        project_root: str | Path | None = None,
    ) -> ControlCenter:
        return cls(load_control_center_config(path, project_root=project_root))

    async def start_all(self) -> OperationResult:
        return await self.start(tuple(service.name for service in self.config.services if service.enabled))

    async def stop_all(self) -> OperationResult:
        return await self.stop(tuple(service.name for service in self.config.services))

    async def restart_all(self) -> OperationResult:
        return await self.restart(
            tuple(service.name for service in self.config.services if service.enabled)
        )

    async def start(self, services: str | Sequence[str]) -> OperationResult:
        names = self._normalize_selection(services)
        async with self._mutation_guard() as acquired:
            started_at = datetime.now(UTC)
            if not acquired:
                return _operation_result(
                    "start", [], ["control center is busy in another process"], started_at
                )
            preflight_errors = await self._preflight(names)
            if preflight_errors:
                selected = self._dependency_closure(names)
                snapshots = [
                    await self._snapshot(self._services[name])
                    for name in self._order
                    if name in selected
                ]
                return _operation_result("start", snapshots, preflight_errors, started_at)
            snapshots, errors = await self._start_selected(names)
            return _operation_result("start", snapshots, errors, started_at)

    async def stop(self, services: str | Sequence[str]) -> OperationResult:
        names = self._normalize_selection(services)
        async with self._mutation_guard() as acquired:
            started_at = datetime.now(UTC)
            if not acquired:
                return _operation_result(
                    "stop", [], ["control center is busy in another process"], started_at
                )
            snapshots, errors = await self._stop_selected(names)
            return _operation_result("stop", snapshots, errors, started_at)

    async def restart(self, services: str | Sequence[str]) -> OperationResult:
        names = self._normalize_selection(services)
        async with self._mutation_guard() as acquired:
            started_at = datetime.now(UTC)
            if not acquired:
                return _operation_result(
                    "restart", [], ["control center is busy in another process"], started_at
                )
            restart_set = self._dependent_closure(names)
            restart_names = tuple(name for name in self._order if name in restart_set)
            preflight_errors = await self._preflight(restart_names)
            if preflight_errors:
                snapshots = [await self._snapshot(self._services[name]) for name in restart_names]
                return _operation_result("restart", snapshots, preflight_errors, started_at)
            stopped, stop_errors = await self._stop_selected(restart_names)
            if stop_errors and _has_blocking_stop_failure(stopped):
                return _operation_result("restart", stopped, stop_errors, started_at)
            started, start_errors = await self._start_selected(restart_names)
            return _operation_result("restart", started, start_errors, started_at)

    async def restart_failed(self) -> OperationResult:
        """Perform one bounded recovery pass; never starts an automatic restart loop."""

        async with self._mutation_guard() as acquired:
            started_at = datetime.now(UTC)
            if not acquired:
                return _operation_result(
                    "restart_failed",
                    [],
                    ["control center is busy in another process"],
                    started_at,
                )
            snapshots = await self.status_all()
            failed = tuple(
                snapshot.name
                for snapshot in snapshots
                if snapshot.status is ServiceStatus.ERROR
                and self._services[snapshot.name].enabled
                and self._services[snapshot.name].driver is not DriverType.EXTERNAL
                and self._state.get(snapshot.name) is not None
            )
            if not failed:
                return _operation_result("restart_failed", [], [], started_at)
            restart_set = self._dependent_closure(failed)
            restart_names = tuple(name for name in self._order if name in restart_set)
            preflight_errors = await self._preflight(restart_names)
            if preflight_errors:
                current = [await self._snapshot(self._services[name]) for name in restart_names]
                return _operation_result(
                    "restart_failed", current, preflight_errors, started_at
                )
            stopped, stop_errors = await self._stop_selected(restart_names)
            if stop_errors and _has_blocking_stop_failure(stopped):
                return _operation_result("restart_failed", stopped, stop_errors, started_at)
            started, start_errors = await self._start_selected(restart_names)
            return _operation_result("restart_failed", started, start_errors, started_at)

    async def status(self, service: str) -> ServiceSnapshot:
        spec = self._get_service(service)
        return await self._snapshot(spec)

    async def status_all(self) -> list[ServiceSnapshot]:
        return list(
            await asyncio.gather(
                *(self._snapshot(self._services[name]) for name in self._order)
            )
        )

    async def system_check(self) -> list[DiagnosticCheck]:
        return await run_system_checks(self.config)

    async def system_metrics(self) -> SystemMetrics:
        return await collect_system_metrics(self.config)

    async def ai_metrics(self) -> AIMetrics:
        lm_service = next(
            (
                service
                for service in self.config.services
                if service.enabled and service.driver is DriverType.LM_STUDIO
            ),
            None,
        )
        lm_snapshot = await self._snapshot(lm_service) if lm_service is not None else None
        return await collect_ai_metrics(self.config, lm_snapshot)

    async def tail_logs(self, service: str, lines: int = 200) -> list[str]:
        if not 1 <= lines <= 10_000:
            raise ValueError("lines must be between 1 and 10000")
        spec = self._get_service(service)
        driver_lines = await self._driver(spec).logs(spec, lines)
        if driver_lines is not None:
            return driver_lines
        log_path = spec.log_file or self.config.logs_dir / f"{spec.name}.log"
        return await asyncio.to_thread(_tail_file, log_path, lines)

    async def _start_selected(
        self, requested: tuple[str, ...]
    ) -> tuple[list[ServiceSnapshot], list[str]]:
        selected = self._dependency_closure(requested)
        snapshots: dict[str, ServiceSnapshot] = {}
        errors: list[str] = []
        for name in self._order:
            if name not in selected:
                continue
            service = self._services[name]
            if not service.enabled:
                detail = f"service {name!r} is disabled"
                errors.append(detail)
                snapshots[name] = self._from_driver_result(
                    service, DriverResult(status=ServiceStatus.ERROR, detail=detail)
                )
                continue
            failed_dependencies = [
                dependency
                for dependency in service.dependencies
                if dependency in snapshots
                and snapshots[dependency].status is not ServiceStatus.RUNNING
            ]
            if failed_dependencies:
                detail = f"dependencies failed: {', '.join(failed_dependencies)}"
                errors.append(f"{name}: {detail}")
                snapshots[name] = self._from_driver_result(
                    service, DriverResult(status=ServiceStatus.ERROR, detail=detail)
                )
                continue

            result = await self._driver(service).start(service)
            if result.status is ServiceStatus.ERROR:
                errors.append(f"{name}: {result.detail}")
                snapshots[name] = self._from_driver_result(service, result)
                continue
            snapshot = await self._wait_until_ready(service, newly_started=result.status is ServiceStatus.STARTING)
            snapshots[name] = snapshot
            if snapshot.status is not ServiceStatus.RUNNING:
                errors.append(f"{name}: {snapshot.detail}")
        return [snapshots[name] for name in self._order if name in snapshots], errors

    @asynccontextmanager
    async def _mutation_guard(self) -> AsyncIterator[bool]:
        async with self._operation_lock:
            acquired = await asyncio.to_thread(
                self._lifecycle_lock.acquire,
                self.config.lifecycle_lock_timeout_seconds,
            )
            try:
                yield acquired
            finally:
                if acquired:
                    await asyncio.to_thread(self._lifecycle_lock.release)

    async def _preflight(self, requested: tuple[str, ...]) -> list[str]:
        selected = self._dependency_closure(requested)
        checks = await run_system_checks(
            self.config,
            tuple(self._services[name] for name in self._order if name in selected),
        )
        return [
            f"preflight {check.name}: {check.detail}"
            for check in checks
            if not check.ok and check.severity == "required"
        ]

    async def _stop_selected(
        self, requested: tuple[str, ...]
    ) -> tuple[list[ServiceSnapshot], list[str]]:
        selected = self._dependent_closure(requested)
        snapshots: dict[str, ServiceSnapshot] = {}
        errors: list[str] = []
        for name in reversed(self._order):
            if name not in selected:
                continue
            service = self._services[name]
            result = await self._driver(service).stop(service)
            snapshots[name] = self._from_driver_result(service, result)
            if result.status is not ServiceStatus.STOPPED:
                errors.append(f"{name}: {result.detail}")
        return [snapshots[name] for name in reversed(self._order) if name in snapshots], errors

    async def _wait_until_ready(
        self,
        service: ServiceConfig,
        *,
        newly_started: bool,
    ) -> ServiceSnapshot:
        deadline = asyncio.get_running_loop().time() + self.config.startup_timeout_seconds
        attempts = service.health.retries if service.health is not None else 30
        interval = service.health.interval_seconds if service.health is not None else 1.0
        last_detail = "service did not become ready"
        try:
            for attempt in range(attempts):
                driver_result = await self._driver(service).status(service)
                if driver_result.status in {ServiceStatus.ERROR, ServiceStatus.STOPPED}:
                    if newly_started:
                        try:
                            cleanup = await asyncio.shield(self._driver(service).stop(service))
                        except Exception as exc:
                            driver_result = driver_result.model_copy(
                                update={
                                    "detail": (
                                        f"{driver_result.detail}; cleanup failed: {type(exc).__name__}"
                                    )
                                }
                            )
                        else:
                            if cleanup.status is not ServiceStatus.STOPPED:
                                driver_result = driver_result.model_copy(
                                    update={
                                        "detail": (
                                            f"{driver_result.detail}; cleanup incomplete: "
                                            f"{cleanup.detail}"
                                        )
                                    }
                                )
                    return self._from_driver_result(service, driver_result)
                if service.health is None:
                    if driver_result.status is ServiceStatus.RUNNING:
                        return self._from_driver_result(service, driver_result, health=True)
                    last_detail = driver_result.detail
                else:
                    health = await probe_health(
                        service.health,
                        process_running=driver_result.status
                        in {ServiceStatus.RUNNING, ServiceStatus.STARTING},
                        cwd=service.cwd or self.config.project_root,
                        environment=service.environment,
                    )
                    last_detail = health.detail
                    exact_driver_ready = (
                        service.driver is not DriverType.LM_STUDIO
                        or driver_result.status is ServiceStatus.RUNNING
                    )
                    if health.ok and exact_driver_ready:
                        return self._from_driver_result(
                            service,
                            driver_result.model_copy(
                                update={"status": ServiceStatus.RUNNING, "detail": health.detail}
                            ),
                            health=True,
                        )
                remaining = deadline - asyncio.get_running_loop().time()
                if attempt + 1 >= attempts or remaining <= 0:
                    break
                await asyncio.sleep(min(interval, remaining))
        except asyncio.CancelledError:
            if newly_started:
                await asyncio.shield(self._driver(service).stop(service))
            raise

        if newly_started:
            await self._driver(service).stop(service)
        return self._from_driver_result(
            service,
            DriverResult(
                status=ServiceStatus.ERROR,
                detail=f"readiness check failed: {last_detail}",
            ),
            health=False,
        )

    async def _snapshot(self, service: ServiceConfig) -> ServiceSnapshot:
        result = await self._driver(service).status(service)
        if service.health is None:
            return self._from_driver_result(service, result)
        health = await probe_health(
            service.health,
            process_running=result.status in {ServiceStatus.RUNNING, ServiceStatus.STARTING},
            cwd=service.cwd or self.config.project_root,
            environment=service.environment,
        )
        if service.driver is DriverType.LM_STUDIO and result.status is not ServiceStatus.RUNNING:
            return self._from_driver_result(service, result, health=health.ok)
        if service.driver is DriverType.EXTERNAL:
            mapped = result.model_copy(
                update={
                    "status": ServiceStatus.RUNNING if health.ok else ServiceStatus.STOPPED,
                    "detail": health.detail,
                }
            )
            return self._from_driver_result(service, mapped, health=health.ok)
        if result.status in {ServiceStatus.RUNNING, ServiceStatus.STARTING} and not health.ok:
            mapped = result.model_copy(
                update={"status": ServiceStatus.ERROR, "detail": health.detail}
            )
            return self._from_driver_result(service, mapped, health=False)
        if health.ok and result.status in {ServiceStatus.RUNNING, ServiceStatus.STARTING}:
            mapped = result.model_copy(
                update={"status": ServiceStatus.RUNNING, "detail": health.detail}
            )
            return self._from_driver_result(service, mapped, health=True)
        return self._from_driver_result(service, result, health=health.ok)

    def _from_driver_result(
        self,
        service: ServiceConfig,
        result: DriverResult,
        *,
        health: bool | None = None,
    ) -> ServiceSnapshot:
        return ServiceSnapshot(
            name=service.name,
            display_name=service.label,
            status=result.status,
            detail=result.detail,
            pid=result.pid,
            uptime_seconds=result.uptime_seconds,
            driver=service.driver,
            health=health,
            metadata=result.metadata,
        )

    def _driver(self, service: ServiceConfig) -> ServiceDriver:
        return self._drivers[service.driver]

    def _get_service(self, name: str) -> ServiceConfig:
        try:
            return self._services[name]
        except KeyError as exc:
            raise ControlCenterError(f"unknown service: {name}") from exc

    def _normalize_selection(self, services: str | Sequence[str]) -> tuple[str, ...]:
        names = (services,) if isinstance(services, str) else tuple(services)
        if not names:
            raise ControlCenterError("at least one service must be selected")
        for name in names:
            self._get_service(name)
        return tuple(dict.fromkeys(names))

    def _dependency_closure(self, names: tuple[str, ...]) -> set[str]:
        result = set(names)
        pending = list(names)
        while pending:
            name = pending.pop()
            for dependency in self._services[name].dependencies:
                if dependency not in result:
                    result.add(dependency)
                    pending.append(dependency)
        return result

    def _dependent_closure(self, names: tuple[str, ...]) -> set[str]:
        result = set(names)
        changed = True
        while changed:
            changed = False
            for service in self.config.services:
                if service.name not in result and result.intersection(service.dependencies):
                    result.add(service.name)
                    changed = True
        return result


def _operation_result(
    action: str,
    snapshots: list[ServiceSnapshot],
    errors: list[str],
    started_at: datetime,
) -> OperationResult:
    return OperationResult(
        success=not errors,
        action=action,
        services=snapshots,
        errors=errors,
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )


def _has_blocking_stop_failure(snapshots: list[ServiceSnapshot]) -> bool:
    return any(
        snapshot.status is not ServiceStatus.STOPPED
        and not (
            snapshot.status is ServiceStatus.RUNNING
            and snapshot.metadata.get("unmanaged") is True
        )
        for snapshot in snapshots
    )


def _topological_order(services: tuple[ServiceConfig, ...]) -> tuple[str, ...]:
    dependencies = {service.name: set(service.dependencies) for service in services}
    order: list[str] = []
    ready = sorted(name for name, dependency in dependencies.items() if not dependency)
    while ready:
        name = ready.pop(0)
        order.append(name)
        for candidate in sorted(dependencies):
            if name in dependencies[candidate]:
                dependencies[candidate].remove(name)
                if not dependencies[candidate] and candidate not in order and candidate not in ready:
                    ready.append(candidate)
                    ready.sort()
    if len(order) != len(services):  # Config validation normally catches this first.
        raise ControlCenterError("service dependency graph contains a cycle")
    return tuple(order)


def _tail_file(path: Path, lines: int) -> list[str]:
    if not path.is_file():
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            position = handle.tell()
            data = b""
            newline_count = 0
            max_bytes = 8 * 1024 * 1024
            while position > 0 and newline_count <= lines and len(data) < max_bytes:
                block_size = min(64 * 1024, position, max_bytes - len(data))
                if block_size <= 0:
                    break
                position -= block_size
                handle.seek(position)
                block = handle.read(block_size)
                newline_count += block.count(b"\n")
                data = block + data
            decoded = data.decode("utf-8", errors="replace")
            return decoded.splitlines()[-lines:]
    except OSError as exc:
        return [f"Could not read log: {type(exc).__name__}"]


__all__ = ["ControlCenter", "ControlCenterError"]
