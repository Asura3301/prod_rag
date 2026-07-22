from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

import rag_app.control_center.diagnostics as diagnostics_module
import rag_app.control_center.drivers as driver_module
import rag_app.control_center.orchestrator as orchestrator_module
from rag_app.control_center import (
    ControlCenter,
    ControlCenterConfig,
    ControlCenterConfigError,
    DiagnosticCheck,
    DriverType,
    ServiceConfig,
    ServiceStatus,
    initialize_control_center,
    load_control_center_config,
)
from rag_app.control_center.drivers import (
    CommandResult,
    DockerComposeDriver,
    DriverContext,
    LMStudioDriver,
    ProcessDriver,
    run_command,
)
from rag_app.control_center.models import DriverResult, HealthCheckConfig, HealthResult
from rag_app.control_center.state import LifecycleLock, ManagedServiceState, StateStore


class FakeDriver:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.statuses: dict[str, ServiceStatus] = {}

    async def start(self, service: ServiceConfig) -> DriverResult:
        self.events.append(("start", service.name))
        self.statuses[service.name] = ServiceStatus.RUNNING
        return DriverResult(status=ServiceStatus.STARTING, detail="started")

    async def stop(self, service: ServiceConfig) -> DriverResult:
        self.events.append(("stop", service.name))
        self.statuses[service.name] = ServiceStatus.STOPPED
        return DriverResult(status=ServiceStatus.STOPPED, detail="stopped")

    async def status(self, service: ServiceConfig) -> DriverResult:
        status = self.statuses.get(service.name, ServiceStatus.STOPPED)
        return DriverResult(status=status, detail=status.value)

    async def logs(self, service: ServiceConfig, lines: int) -> list[str] | None:
        return None


def _service(name: str, dependencies: tuple[str, ...] = ()) -> ServiceConfig:
    return ServiceConfig(
        name=name,
        driver=DriverType.PROCESS,
        command=("python", "-c", "pass"),
        dependencies=dependencies,
    )


def _config(tmp_path: Path, services: tuple[ServiceConfig, ...]) -> ControlCenterConfig:
    return ControlCenterConfig(
        project_root=tmp_path,
        runtime_dir=tmp_path / "runtime",
        logs_dir=tmp_path / "logs",
        services_dir=None,
        services=services,
    )


async def _allow_preflight(_requested: tuple[str, ...]) -> list[str]:
    return []


@pytest.mark.asyncio
async def test_dependency_start_and_reverse_dependent_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    center = ControlCenter(
        _config(
            tmp_path,
            (
                _service("database"),
                _service("api", ("database",)),
                _service("worker", ("api",)),
            ),
        )
    )
    fake = FakeDriver()
    center._drivers[DriverType.PROCESS] = fake  # type: ignore[assignment]
    monkeypatch.setattr(center, "_preflight", _allow_preflight)

    started = await center.start("worker")
    stopped = await center.stop("database")

    assert started.success
    assert stopped.success
    assert fake.events == [
        ("start", "database"),
        ("start", "api"),
        ("start", "worker"),
        ("stop", "worker"),
        ("stop", "api"),
        ("stop", "database"),
    ]


def test_config_expansion_cycle_and_secret_safe_error(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(
        """version: 1
services_dir: null
services:
  - name: one
    driver: process
    command: [python]
    dependencies: [two]
  - name: two
    driver: process
    command: [python]
    dependencies: [one]
""",
        encoding="utf-8",
    )
    with pytest.raises(ControlCenterConfigError, match="cycle"):
        load_control_center_config(config_path, project_root=tmp_path, environment={})

    config_path.write_text(
        """version: 1
services_dir: null
services:
  - name: cache
    driver: external
    health:
      type: tcp
      host: 127.0.0.1
      port: ${PRIVATE_VALUE}
""",
        encoding="utf-8",
    )
    secret = "super-secret-invalid-port"
    with pytest.raises(ControlCenterConfigError) as error:
        load_control_center_config(
            config_path,
            project_root=tmp_path,
            environment={"PRIVATE_VALUE": secret},
        )
    assert secret not in str(error.value)

    config_path.write_text(
        """version: 1
services_dir: null
services:
  - name: api
    driver: process
    command: [python]
    log_file: .env
""",
        encoding="utf-8",
    )
    with pytest.raises(ControlCenterConfigError, match="inside logs_dir"):
        load_control_center_config(config_path, project_root=tmp_path, environment={})


def test_initialize_default_config_is_idempotent_and_loadable(tmp_path: Path) -> None:
    path = tmp_path / "ops" / "control-center" / "settings.yaml"
    created = initialize_control_center(path, project_root=tmp_path)
    config = load_control_center_config(path, project_root=tmp_path, environment={})

    assert len(created) == 6
    assert initialize_control_center(path, project_root=tmp_path) == []
    assert config.startup_timeout_seconds == 600
    assert [service.name for service in config.services] == [
        "api",
        "lm_studio",
        "qdrant",
        "redis",
        "worker",
    ]
    lm_studio = next(service for service in config.services if service.name == "lm_studio")
    assert lm_studio.model_identifier == "gpt-oss-20b"

    repository_root = Path(__file__).resolve().parents[2]
    checked_in = load_control_center_config(project_root=repository_root, environment={})
    assert checked_in.startup_timeout_seconds == config.startup_timeout_seconds
    for expected in checked_in.services:
        actual = next(service for service in config.services if service.name == expected.name)
        assert _portable_service(expected, repository_root) == _portable_service(actual, tmp_path)


def _portable_service(service: ServiceConfig, root: Path) -> dict[str, Any]:
    value = service.model_dump(mode="json")
    for field in ("cwd", "compose_file", "log_file"):
        path = value.get(field)
        if path:
            value[field] = Path(path).relative_to(root).as_posix()
    return value


@pytest.mark.asyncio
async def test_process_crash_preserves_ownership_and_recovery_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service("api")
    config = _config(tmp_path, (service,))
    state = StateStore(config.runtime_dir / "process-state.json")
    state.set("api", ManagedServiceState(pid=999_999_999, process_create_time=1.0))
    driver = ProcessDriver(
        DriverContext(
            project_root=tmp_path,
            logs_dir=config.logs_dir,
            state_store=state,
            shutdown_timeout_seconds=1,
        )
    )
    snapshot = await driver.status(service)
    assert snapshot.status is ServiceStatus.ERROR
    assert state.get("api") is not None

    center = ControlCenter(config)
    fake = FakeDriver()
    fake.statuses["api"] = ServiceStatus.ERROR
    center._drivers[DriverType.PROCESS] = fake  # type: ignore[assignment]
    center._state.set("api", ManagedServiceState(pid=999_999_998))
    monkeypatch.setattr(center, "_preflight", _allow_preflight)

    recovered = await center.restart_failed()

    assert recovered.success
    assert fake.events == [("stop", "api"), ("start", "api")]
    assert recovered.services[-1].status is ServiceStatus.RUNNING


@pytest.mark.asyncio
async def test_stop_all_includes_disabled_but_configured_services(tmp_path: Path) -> None:
    service = _service("api").model_copy(update={"enabled": False})
    center = ControlCenter(_config(tmp_path, (service,)))
    fake = FakeDriver()
    fake.statuses["api"] = ServiceStatus.RUNNING
    center._drivers[DriverType.PROCESS] = fake  # type: ignore[assignment]

    result = await center.stop_all()

    assert result.success
    assert fake.events == [("stop", "api")]


def test_pid_identity_rejects_reused_pid() -> None:
    state = ManagedServiceState(pid=os.getpid(), process_create_time=0.0)
    assert driver_module._pid_matches(state) is False


def test_lifecycle_lock_reports_contention(tmp_path: Path) -> None:
    first = LifecycleLock(tmp_path / "lifecycle.lock")
    second = LifecycleLock(tmp_path / "lifecycle.lock")
    assert first.acquire(0.1)
    try:
        assert second.acquire(0.05) is False
    finally:
        first.release()
    assert second.acquire(0.1)
    second.release()


def test_graceful_signal_targets_the_managed_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, Any]] = []
    state = ManagedServiceState(pid=12345, process_create_time=1.0)
    if os.name == "nt":
        monkeypatch.setattr(driver_module.os, "kill", lambda pid, event: calls.append((pid, event)))
        expected = driver_module.signal.CTRL_BREAK_EVENT
    else:
        monkeypatch.setattr(
            driver_module.os,
            "killpg",
            lambda pid, event: calls.append((pid, event)),
        )
        expected = driver_module.signal.SIGTERM
    assert driver_module._send_graceful_signal(state)
    assert calls == [(12345, expected)]


@pytest.mark.asyncio
async def test_lm_studio_start_runs_only_desktop_launcher_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = StateStore(tmp_path / "state.json")
    driver = LMStudioDriver(DriverContext(tmp_path, tmp_path / "logs", state, 1))
    service = ServiceConfig(
        name="lm_studio",
        driver=DriverType.LM_STUDIO,
        model_identifier="configured-model",
    )
    script = tmp_path / "scripts" / "control-center" / "start_lm_studio.ps1"
    script.parent.mkdir(parents=True)
    script.write_text("# launcher fixture", encoding="utf-8")
    detected = iter((None, (4321, 100.0)))
    commands: list[tuple[str, ...]] = []

    monkeypatch.setattr(driver_module, "_lm_studio_start_script", lambda _root: script)
    monkeypatch.setattr(
        driver_module,
        "_script_command",
        lambda _script: ("desktop-launcher", str(_script)),
    )
    monkeypatch.setattr(
        driver_module,
        "_find_lm_studio_desktop_process",
        lambda: next(detected),
    )

    async def fake_run(
        command: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: float,
        environment: dict[str, str] | None = None,
    ) -> CommandResult:
        del cwd, timeout_seconds, environment
        commands.append(command)
        return CommandResult(return_code=0)

    monkeypatch.setattr(driver_module, "run_command", fake_run)
    result = await driver.start(service)

    assert commands == [("desktop-launcher", str(script))]
    assert result.status is ServiceStatus.RUNNING
    assert result.pid == 4321
    assert result.metadata == {"desktop": True, "model": "configured-model"}
    assert state.get(service.name) is None


@pytest.mark.asyncio
async def test_lm_studio_status_is_read_only_process_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = LMStudioDriver(
        DriverContext(tmp_path, tmp_path / "logs", StateStore(tmp_path / "state.json"), 1)
    )
    service = ServiceConfig(
        name="lm_studio",
        driver=DriverType.LM_STUDIO,
        model_identifier="configured-model",
    )
    monkeypatch.setattr(
        driver_module,
        "_find_lm_studio_desktop_process",
        lambda: (7654, 100.0),
    )

    async def forbidden_run(*_args: Any, **_kwargs: Any) -> CommandResult:
        raise AssertionError("status must not run a command")

    monkeypatch.setattr(driver_module, "run_command", forbidden_run)
    result = await driver.status(service)

    assert result.status is ServiceStatus.RUNNING
    assert result.pid == 7654
    assert result.metadata["model"] == "configured-model"
    assert "Desktop" in result.detail


@pytest.mark.asyncio
async def test_lm_studio_stop_never_closes_app_or_manages_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = StateStore(tmp_path / "state.json")
    state.set(
        "lm_studio",
        ManagedServiceState(metadata={"legacy_owned_model": True}),
    )
    driver = LMStudioDriver(DriverContext(tmp_path, tmp_path / "logs", state, 1))
    service = ServiceConfig(
        name="lm_studio",
        driver=DriverType.LM_STUDIO,
        model_identifier="configured-model",
    )
    monkeypatch.setattr(
        driver_module,
        "_find_lm_studio_desktop_process",
        lambda: (7654, 100.0),
    )

    async def forbidden_run(*_args: Any, **_kwargs: Any) -> CommandResult:
        raise AssertionError("stop must not run a command")

    monkeypatch.setattr(driver_module, "run_command", forbidden_run)
    result = await driver.stop(service)

    assert result.status is ServiceStatus.STOPPED
    assert result.pid == 7654
    assert result.metadata == {
        "desktop_left_running": True,
        "model": "configured-model",
    }
    assert "left running" in result.detail
    assert state.get(service.name) is None



def test_docker_compose_status_parses_json_and_ndjson(tmp_path: Path) -> None:
    context = DriverContext(
        project_root=tmp_path,
        logs_dir=tmp_path / "logs",
        state_store=StateStore(tmp_path / "state.json"),
        shutdown_timeout_seconds=1,
    )
    assert isinstance(DockerComposeDriver(context), DockerComposeDriver)
    assert driver_module._parse_compose_ps('[{"State":"running","Name":"db"}]') == {
        "State": "running",
        "Name": "db",
    }
    assert driver_module._parse_compose_ps('{"State":"exited"}\n{"State":"running"}') == {
        "State": "exited"
    }


@pytest.mark.asyncio
async def test_docker_does_not_claim_or_stop_preexisting_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = StateStore(tmp_path / "state.json")
    driver = DockerComposeDriver(DriverContext(tmp_path, tmp_path / "logs", state, 1))
    service = ServiceConfig(
        name="redis",
        driver=DriverType.DOCKER_COMPOSE,
        compose_file=tmp_path / "compose.yaml",
        compose_service="redis",
    )
    record = {"State": "running", "ID": "existing-id", "Name": "redis"}

    async def inspect(
        _service: ServiceConfig,
    ) -> tuple[CommandResult, dict[str, Any]]:
        return CommandResult(return_code=0), record

    monkeypatch.setattr(driver_module.shutil, "which", lambda _name: "docker")
    monkeypatch.setattr(driver, "_inspect", inspect)

    started = await driver.start(service)
    stopped = await driver.stop(service)

    assert started.status is ServiceStatus.RUNNING
    assert "not claimed" in started.detail
    assert stopped.status is ServiceStatus.RUNNING
    assert "not started by this control center" in stopped.detail
    assert state.get("redis") is None

    center = ControlCenter(_config(tmp_path, (service,)))
    managed_driver = center._drivers[DriverType.DOCKER_COMPOSE]
    monkeypatch.setattr(managed_driver, "_inspect", inspect)
    operation = await center.stop("redis")
    assert operation.success is False
    assert operation.services[0].status is ServiceStatus.RUNNING


@pytest.mark.asyncio
async def test_docker_refuses_stale_config_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = StateStore(tmp_path / "state.json")
    driver = DockerComposeDriver(DriverContext(tmp_path, tmp_path / "logs", state, 1))
    service = ServiceConfig(
        name="redis",
        driver=DriverType.DOCKER_COMPOSE,
        compose_file=tmp_path / "new-compose.yaml",
        compose_service="redis",
    )
    state.set(
        "redis",
        ManagedServiceState(
            metadata={"container_id": "same-id", "config_fingerprint": "old-config"}
        ),
    )
    monkeypatch.setattr(driver_module.shutil, "which", lambda _name: "docker")

    result = await driver.stop(service)

    assert result.status is ServiceStatus.ERROR
    assert "refusing" in result.detail


@pytest.mark.asyncio
async def test_docker_stop_targets_verified_container_id_not_compose_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = StateStore(tmp_path / "state.json")
    driver = DockerComposeDriver(DriverContext(tmp_path, tmp_path / "logs", state, 1.6))
    service = ServiceConfig(
        name="redis",
        driver=DriverType.DOCKER_COMPOSE,
        compose_file=tmp_path / "compose.yaml",
        compose_service="redis",
    )
    container_id = "a" * 64
    state.set(
        "redis",
        ManagedServiceState(
            metadata={
                "container_id": container_id,
                "config_fingerprint": driver_module._docker_config_fingerprint(service),
            }
        ),
    )

    async def inspect(
        _service: ServiceConfig,
    ) -> tuple[CommandResult, dict[str, Any]]:
        return CommandResult(return_code=0), {
            "State": "running",
            "ID": container_id,
            "Name": "project-redis-replacement-prone-name",
        }

    calls: list[tuple[tuple[str, ...], Path, float]] = []

    async def fake_run_command(
        command: tuple[str, ...], *, cwd: Path, timeout_seconds: float
    ) -> CommandResult:
        calls.append((command, cwd, timeout_seconds))
        return CommandResult(return_code=0)

    monkeypatch.setattr(driver_module.shutil, "which", lambda _name: "docker")
    monkeypatch.setattr(driver, "_inspect", inspect)
    monkeypatch.setattr(driver_module, "run_command", fake_run_command)

    result = await driver.stop(service)

    assert result.status is ServiceStatus.STOPPED
    assert calls == [
        (
            ("docker", "container", "stop", "--time", "2", "--", container_id),
            tmp_path,
            31.6,
        )
    ]
    assert service.compose_service not in calls[0][0]
    assert state.get("redis") is None


@pytest.mark.asyncio
async def test_docker_ownership_write_rollback_targets_started_container_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = StateStore(tmp_path / "state.json")
    driver = DockerComposeDriver(DriverContext(tmp_path, tmp_path / "logs", state, 1))
    service = ServiceConfig(
        name="redis",
        driver=DriverType.DOCKER_COMPOSE,
        compose_file=tmp_path / "compose.yaml",
        compose_service="redis",
    )
    container_id = "b" * 64
    records = iter(
        (
            None,
            {"State": "running", "ID": container_id, "Name": "project-redis"},
            {"State": "running", "ID": container_id, "Name": "project-redis-recreated"},
        )
    )

    async def inspect(
        _service: ServiceConfig,
    ) -> tuple[CommandResult, dict[str, Any] | None]:
        return CommandResult(return_code=0), next(records)

    commands: list[tuple[str, ...]] = []

    async def fake_run_command(
        command: tuple[str, ...], *, cwd: Path, timeout_seconds: float
    ) -> CommandResult:
        del cwd, timeout_seconds
        commands.append(command)
        return CommandResult(return_code=0)

    def fail_to_persist(_name: str, _value: ManagedServiceState) -> None:
        raise OSError("simulated state-store failure")

    monkeypatch.setattr(driver_module.shutil, "which", lambda _name: "docker")
    monkeypatch.setattr(driver, "_inspect", inspect)
    monkeypatch.setattr(driver_module, "run_command", fake_run_command)
    monkeypatch.setattr(state, "set", fail_to_persist)

    result = await driver.start(service)

    assert result.status is ServiceStatus.ERROR
    assert "could not persist container ownership" in result.detail
    assert commands[-1] == (
        "docker",
        "container",
        "stop",
        "--time",
        "1",
        "--",
        container_id,
    )
    assert service.compose_service not in commands[-1]


@pytest.mark.asyncio
async def test_run_command_cancellation_terminates_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()

    class FakeProcess:
        returncode = 0

        def __init__(self) -> None:
            self.terminated = False
            self.killed = False

        async def communicate(self) -> tuple[bytes, bytes]:
            started.set()
            await asyncio.Event().wait()
            return b"", b""

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            return 0

    process = FakeProcess()

    async def create_process(*_args: Any, **_kwargs: Any) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    task = asyncio.create_task(run_command(("fake",), cwd=tmp_path, timeout_seconds=60))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminated


@pytest.mark.asyncio
async def test_readiness_cancellation_rolls_back_new_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service("api").model_copy(
        update={
            "health": HealthCheckConfig(
                type="http",
                url="http://127.0.0.1:8000/health/ready",
                retries=10,
            )
        }
    )
    center = ControlCenter(_config(tmp_path, (service,)))
    fake = FakeDriver()
    fake.statuses["api"] = ServiceStatus.RUNNING
    center._drivers[DriverType.PROCESS] = fake  # type: ignore[assignment]
    probing = asyncio.Event()

    async def blocked_probe(*_args: Any, **_kwargs: Any) -> HealthResult:
        probing.set()
        await asyncio.Event().wait()
        return HealthResult(ok=False, detail="unreachable")

    monkeypatch.setattr(orchestrator_module, "probe_health", blocked_probe)
    task = asyncio.create_task(center._wait_until_ready(service, newly_started=True))
    await probing.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.events == [("stop", "api")]


@pytest.mark.asyncio
async def test_system_checks_cover_uv_packages_and_optional_gpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, (_service("api").model_copy(update={"command": ("uv", "run")}),))
    commands: list[tuple[str, ...]] = []

    def which(name: str) -> str | None:
        return "uv" if name == "uv" else None

    def find_spec(name: str) -> object | None:
        return object() if name == "psutil" else None

    async def fake_command(
        command: Any, *, cwd: Path, timeout_seconds: float, environment: Any = None
    ) -> CommandResult:
        del cwd, timeout_seconds, environment
        commands.append(tuple(command))
        return CommandResult(return_code=0)

    monkeypatch.setattr(diagnostics_module.shutil, "which", which)
    monkeypatch.setattr(diagnostics_module.importlib.util, "find_spec", find_spec)
    monkeypatch.setattr(diagnostics_module, "run_command", fake_command)

    checks = await diagnostics_module.run_system_checks(config)
    by_name = {check.name: check for check in checks}

    assert (
        "uv",
        "sync",
        "--check",
        "--extra",
        "qdrant",
        "--extra",
        "local-models",
    ) in commands
    assert by_name["Python package: qdrant_client"].ok is False
    assert by_name["Python package: sentence_transformers"].solution == (
        "Run `uv sync --extra local-models`."
    )
    assert by_name["NVIDIA GPU"].severity == "optional"
    assert by_name["NVIDIA GPU"].ok is False
    assert by_name["CUDA"].severity == "optional"


@pytest.mark.asyncio
async def test_node_package_check_is_conditional_and_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    service = _service("frontend").model_copy(
        update={"command": ("npm", "run", "dev"), "cwd": tmp_path}
    )
    config = _config(tmp_path, (service,))
    commands: list[tuple[str, ...]] = []

    def which(name: str) -> str | None:
        return name if name in {"node", "npm"} else None

    async def fake_command(
        command: Any, *, cwd: Path, timeout_seconds: float, environment: Any = None
    ) -> CommandResult:
        del cwd, timeout_seconds, environment
        commands.append(tuple(command))
        return CommandResult(return_code=1)

    monkeypatch.setattr(diagnostics_module.shutil, "which", which)
    monkeypatch.setattr(diagnostics_module.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(diagnostics_module, "run_command", fake_command)

    checks = await diagnostics_module.run_system_checks(config)
    package_check = next(check for check in checks if check.name == "Node packages: frontend")

    assert ("npm", "ls", "--depth=0", "--silent") in commands
    assert package_check.ok is False
    assert package_check.solution == "Run `npm install` in the configured service directory."


def test_large_log_tail_is_bounded_and_returns_last_lines(tmp_path: Path) -> None:
    log = tmp_path / "large.log"
    with log.open("w", encoding="utf-8") as handle:
        for index in range(200_000):
            handle.write(f"line-{index:06d} payload payload payload\n")

    assert orchestrator_module._tail_file(log, 3) == [
        "line-199997 payload payload payload",
        "line-199998 payload payload payload",
        "line-199999 payload payload payload",
    ]


def test_zero_query_prometheus_metrics_hide_latency() -> None:
    values = diagnostics_module._parse_prometheus_values(
        """rag_query_requests_total 0
rag_query_requests_per_minute 0
rag_query_latency_last_ms 0
rag_query_latency_average_ms 0
""",
        {
            "rag_query_requests_total",
            "rag_query_requests_per_minute",
            "rag_query_latency_last_ms",
            "rag_query_latency_average_ms",
        },
    )
    assert diagnostics_module._latency_value(values, "rag_query_latency_last_ms") is None


@pytest.mark.asyncio
async def test_preflight_blocks_required_failures_but_not_optional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    center = ControlCenter(_config(tmp_path, (_service("api"),)))
    fake = FakeDriver()
    center._drivers[DriverType.PROCESS] = fake  # type: ignore[assignment]

    async def optional_failure(
        _config: ControlCenterConfig, services: Any = None
    ) -> list[DiagnosticCheck]:
        del services
        return [
            DiagnosticCheck(
                name="GPU",
                ok=False,
                detail="not detected",
                severity="optional",
            )
        ]

    monkeypatch.setattr(orchestrator_module, "run_system_checks", optional_failure)
    allowed = await center.start("api")
    assert allowed.success
    assert fake.events == [("start", "api")]

    fake.events.clear()
    fake.statuses["api"] = ServiceStatus.STOPPED

    async def required_failure(
        _config: ControlCenterConfig, services: Any = None
    ) -> list[DiagnosticCheck]:
        del services
        return [DiagnosticCheck(name="Docker", ok=False, detail="daemon unavailable")]

    monkeypatch.setattr(orchestrator_module, "run_system_checks", required_failure)
    blocked = await center.start("api")
    assert blocked.success is False
    assert fake.events == []
    assert blocked.errors == ["preflight Docker: daemon unavailable"]
