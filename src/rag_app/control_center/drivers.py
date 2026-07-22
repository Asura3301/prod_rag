"""Lifecycle drivers for processes, Docker Compose, external dependencies, and LM Studio."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rag_app.control_center.models import (
    DriverResult,
    DriverType,
    ServiceConfig,
    ServiceStatus,
)
from rag_app.control_center.state import ManagedServiceState, StateStore


@dataclass(frozen=True, slots=True)
class DriverContext:
    project_root: Path
    logs_dir: Path
    state_store: StateStore
    shutdown_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class CommandResult:
    return_code: int
    stdout: str = ""
    stderr: str = ""
    failure: str | None = None


class ServiceDriver(ABC):
    def __init__(self, context: DriverContext) -> None:
        self.context = context

    @abstractmethod
    async def start(self, service: ServiceConfig) -> DriverResult: ...

    @abstractmethod
    async def stop(self, service: ServiceConfig) -> DriverResult: ...

    @abstractmethod
    async def status(self, service: ServiceConfig) -> DriverResult: ...

    async def logs(self, service: ServiceConfig, lines: int) -> list[str] | None:
        return None


class ProcessDriver(ServiceDriver):
    async def start(self, service: ServiceConfig) -> DriverResult:
        current = await self.status(service)
        if current.status in {ServiceStatus.RUNNING, ServiceStatus.STARTING}:
            return current
        existing = self.context.state_store.get(service.name)
        if existing is not None and existing.pid and _pid_matches(existing):
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail="a managed process is alive but its configuration changed",
                pid=existing.pid,
            )

        executable = _resolve_executable(service.command[0], service.cwd or self.context.project_root)
        if executable is None:
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail=f"executable not found: {service.command[0]}",
            )

        self.context.logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = service.log_file or self.context.logs_dir / f"{service.name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_log(log_path)
        environment = os.environ.copy()
        environment.update(service.environment)
        command = (executable, *service.command[1:])
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        try:
            with log_path.open("ab", buffering=0) as log_handle:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(service.cwd or self.context.project_root),
                    env=environment,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=asyncio.subprocess.STDOUT,
                    **kwargs,
                )
        except (OSError, ValueError) as exc:
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail=f"could not start process: {type(exc).__name__}",
            )

        _track_child(process)
        create_time = _process_create_time(process.pid)
        if create_time is None:
            process.terminate()
            await process.wait()
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail="could not establish a safe process identity; install/enable psutil",
            )
        state = ManagedServiceState(
            pid=process.pid,
            process_create_time=create_time,
            command_fingerprint=_command_fingerprint(service.command),
        )
        try:
            self.context.state_store.set(service.name, state)
        except Exception as exc:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail=f"could not persist process ownership: {type(exc).__name__}",
            )
        await asyncio.sleep(0)
        if process.returncode is not None:
            self.context.state_store.remove(service.name)
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail=f"process exited immediately with code {process.returncode}",
            )
        return DriverResult(
            status=ServiceStatus.STARTING,
            detail="process started; waiting for readiness",
            pid=process.pid,
            uptime_seconds=0.0,
        )

    async def stop(self, service: ServiceConfig) -> DriverResult:
        state = self.context.state_store.get(service.name)
        if state is None or state.pid is None:
            return DriverResult(status=ServiceStatus.STOPPED, detail="not managed")
        if not _pid_matches(state):
            self.context.state_store.remove(service.name)
            return DriverResult(status=ServiceStatus.STOPPED, detail="process is no longer running")

        if service.stop_command:
            await run_command(
                service.stop_command,
                cwd=service.cwd or self.context.project_root,
                environment=service.environment,
                timeout_seconds=self.context.shutdown_timeout_seconds,
            )
            if await _wait_for_exit(state, min(3.0, self.context.shutdown_timeout_seconds)):
                self.context.state_store.remove(service.name)
                return DriverResult(status=ServiceStatus.STOPPED, detail="stopped gracefully")

        terminated = await asyncio.to_thread(
            _terminate_process_tree,
            state,
            self.context.shutdown_timeout_seconds,
        )
        if terminated:
            self.context.state_store.remove(service.name)
            return DriverResult(status=ServiceStatus.STOPPED, detail="stopped gracefully")
        return DriverResult(
            status=ServiceStatus.ERROR,
            detail="process did not stop before the shutdown timeout",
            pid=state.pid,
        )

    async def status(self, service: ServiceConfig) -> DriverResult:
        state = self.context.state_store.get(service.name)
        if state is None or state.pid is None:
            return DriverResult(status=ServiceStatus.STOPPED, detail="not running")
        if not _pid_matches(state):
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail="managed process exited unexpectedly",
                pid=state.pid,
            )
        fingerprint = _command_fingerprint(service.command)
        uptime = max(0.0, (datetime.now(UTC) - state.started_at).total_seconds())
        if state.command_fingerprint != fingerprint:
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail="managed process does not match the current command",
                pid=state.pid,
                uptime_seconds=uptime,
            )
        return DriverResult(
            status=ServiceStatus.RUNNING,
            detail="process is running",
            pid=state.pid,
            uptime_seconds=uptime,
        )


class DockerComposeDriver(ServiceDriver):
    async def start(self, service: ServiceConfig) -> DriverResult:
        if shutil.which("docker") is None:
            return DriverResult(status=ServiceStatus.ERROR, detail="Docker CLI is not installed")
        inspection, before = await self._inspect(service)
        if inspection.return_code != 0:
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail=inspection.failure
                or f"docker compose exited with code {inspection.return_code}",
            )
        if before is not None and str(before.get("State", "")).lower() in {
            "running",
            "restarting",
            "paused",
        }:
            result = self._record_result(before)
            stored = self.context.state_store.get(service.name)
            current_id = str(before.get("ID", ""))
            managed = bool(
                stored
                and current_id
                and stored.metadata.get("container_id") == current_id
                and stored.metadata.get("config_fingerprint")
                == _docker_config_fingerprint(service)
            )
            detail = result.detail if managed else f"{result.detail}; not claimed (pre-existing)"
            return result.model_copy(update={"detail": detail})

        command = [*self._prefix(service), "up", "-d"]
        assert service.compose_service is not None
        command.append(service.compose_service)
        up_result = await run_command(
            command,
            cwd=self.context.project_root,
            timeout_seconds=180,
        )
        if up_result.return_code != 0:
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail=up_result.failure
                or f"docker compose exited with code {up_result.return_code}",
            )
        inspection, after = await self._inspect(service)
        if inspection.return_code != 0 or after is None:
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail="container started but its identity could not be verified",
            )
        container_id = str(after.get("ID", ""))
        if not container_id:
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail="container started but Docker did not report a stable identity",
            )
        try:
            self.context.state_store.set(
                service.name,
                ManagedServiceState(
                    metadata={
                        "compose_service": service.compose_service,
                        "container_id": container_id,
                        "config_fingerprint": _docker_config_fingerprint(service),
                    }
                ),
            )
        except Exception as exc:
            _inspection, rollback_record = await self._inspect(service)
            compensation_ok = bool(
                rollback_record and rollback_record.get("ID") == container_id
            )
            if compensation_ok:
                compensation = await self._stop_container(container_id)
                compensation_ok = compensation.return_code == 0
            suffix = "" if compensation_ok else "; compensation stop was unsafe or failed"
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail=f"could not persist container ownership: {type(exc).__name__}{suffix}",
            )
        return self._record_result(after).model_copy(
            update={"status": ServiceStatus.STARTING, "detail": "container is starting"}
        )

    async def stop(self, service: ServiceConfig) -> DriverResult:
        if shutil.which("docker") is None:
            return DriverResult(status=ServiceStatus.ERROR, detail="Docker CLI is not installed")
        managed = self.context.state_store.get(service.name)
        if managed is None:
            current = await self.status(service)
            if current.status in {ServiceStatus.RUNNING, ServiceStatus.STARTING}:
                return current.model_copy(
                    update={
                        "detail": "container is running but was not started by this control center",
                        "metadata": {**current.metadata, "unmanaged": True},
                    }
                )
            return current
        if managed.metadata.get("config_fingerprint") != _docker_config_fingerprint(service):
            self.context.state_store.remove(service.name)
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail="Docker service configuration changed; refusing to stop stale ownership",
            )
        inspection, record = await self._inspect(service)
        if inspection.return_code != 0:
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail=inspection.failure
                or f"docker compose exited with code {inspection.return_code}",
            )
        if record is None:
            self.context.state_store.remove(service.name)
            return DriverResult(status=ServiceStatus.STOPPED, detail="container no longer exists")
        expected_id = str(managed.metadata.get("container_id") or "")
        current_id = str(record.get("ID") or "")
        if not expected_id or current_id != expected_id:
            self.context.state_store.remove(service.name)
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail="container identity changed; refusing to stop an unowned container",
            )
        result = await self._stop_container(expected_id)
        if result.return_code != 0:
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail=result.failure or f"docker stop exited with code {result.return_code}",
            )
        self.context.state_store.remove(service.name)
        return DriverResult(status=ServiceStatus.STOPPED, detail="container stopped")

    async def _stop_container(self, container_id: str) -> CommandResult:
        """Gracefully stop exactly the container whose immutable ID was verified."""
        command = (
            "docker",
            "container",
            "stop",
            "--time",
            str(max(1, round(self.context.shutdown_timeout_seconds))),
            "--",
            container_id,
        )
        return await run_command(
            command,
            cwd=self.context.project_root,
            timeout_seconds=self.context.shutdown_timeout_seconds + 30,
        )

    async def status(self, service: ServiceConfig) -> DriverResult:
        if shutil.which("docker") is None:
            return DriverResult(status=ServiceStatus.ERROR, detail="Docker CLI is not installed")
        result, record = await self._inspect(service)
        if result.return_code != 0:
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail=result.failure or f"docker compose exited with code {result.return_code}",
            )
        if record is None:
            return DriverResult(status=ServiceStatus.STOPPED, detail="container does not exist")
        managed = self.context.state_store.get(service.name)
        if managed is not None and managed.metadata.get(
            "config_fingerprint"
        ) != _docker_config_fingerprint(service):
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail="Docker service configuration changed since this container was started",
                metadata=self._record_result(record).metadata,
            )
        snapshot_result = self._record_result(record)
        if managed is not None:
            expected_id = managed.metadata.get("container_id")
            current_id = record.get("ID")
            if not expected_id or expected_id != current_id:
                return DriverResult(
                    status=ServiceStatus.ERROR,
                    detail="managed container identity changed unexpectedly",
                    metadata=snapshot_result.metadata,
                )
            if snapshot_result.status is ServiceStatus.STOPPED:
                return snapshot_result.model_copy(
                    update={
                        "status": ServiceStatus.ERROR,
                        "detail": "managed container stopped unexpectedly",
                    }
                )
        return snapshot_result

    def _record_result(self, record: dict[str, Any]) -> DriverResult:
        state = str(record.get("State", "")).lower()
        health = str(record.get("Health", "")).lower()
        status_text = str(record.get("Status", "")).strip()[:300]
        metadata = {
            key: value
            for key, value in {
                "container": record.get("Name"),
                "container_id": record.get("ID"),
                "health": health or None,
                "status_text": record.get("Status"),
            }.items()
            if value is not None
        }
        if state == "running" and health not in {"unhealthy"}:
            return DriverResult(
                status=ServiceStatus.RUNNING,
                detail=status_text or "container is running",
                metadata=metadata,
            )
        if state == "running" and health == "unhealthy":
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail=status_text or "container is unhealthy",
                metadata=metadata,
            )
        if state in {"created", "restarting", "paused"}:
            return DriverResult(
                status=ServiceStatus.STARTING,
                detail=status_text or f"container state: {state}",
                metadata=metadata,
            )
        if state in {"exited", "dead"}:
            exit_code = record.get("ExitCode")
            if exit_code in {0, "0", None}:
                return DriverResult(
                    status=ServiceStatus.STOPPED,
                    detail="container is stopped",
                    metadata=metadata,
                )
            return DriverResult(
                status=ServiceStatus.ERROR,
                detail=f"container exited with code {exit_code}",
                metadata=metadata,
            )
        return DriverResult(
            status=ServiceStatus.STOPPED,
            detail=f"container state: {state or 'unknown'}",
            metadata=metadata,
        )

    async def _inspect(
        self, service: ServiceConfig
    ) -> tuple[CommandResult, dict[str, Any] | None]:
        assert service.compose_service is not None
        command = [
            *self._prefix(service),
            "ps",
            "--all",
            "--format",
            "json",
            service.compose_service,
        ]
        result = await run_command(command, cwd=self.context.project_root, timeout_seconds=20)
        return result, _parse_compose_ps(result.stdout) if result.return_code == 0 else None

    async def logs(self, service: ServiceConfig, lines: int) -> list[str] | None:
        assert service.compose_service is not None
        command = [
            *self._prefix(service),
            "logs",
            "--no-color",
            "--tail",
            str(lines),
            service.compose_service,
        ]
        result = await run_command(command, cwd=self.context.project_root, timeout_seconds=20)
        if result.return_code != 0:
            return [result.failure or f"docker compose logs exited with code {result.return_code}"]
        return result.stdout.splitlines()[-lines:]

    @staticmethod
    def _prefix(service: ServiceConfig) -> list[str]:
        assert service.compose_file is not None
        command = ["docker", "compose", "-f", str(service.compose_file)]
        for profile in service.profiles:
            command.extend(("--profile", profile))
        return command


class ExternalDriver(ServiceDriver):
    async def start(self, service: ServiceConfig) -> DriverResult:
        return DriverResult(
            status=ServiceStatus.STARTING,
            detail="external service is not lifecycle-managed; checking readiness",
        )

    async def stop(self, service: ServiceConfig) -> DriverResult:
        return DriverResult(
            status=ServiceStatus.STOPPED,
            detail="external service is not lifecycle-managed",
        )

    async def status(self, service: ServiceConfig) -> DriverResult:
        return DriverResult(
            status=ServiceStatus.STARTING,
            detail="external service readiness has not been checked",
        )


class LMStudioDriver(ServiceDriver):
    """Launch the visible LM Studio Desktop app without managing its runtime."""

    async def start(self, service: ServiceConfig) -> DriverResult:
        current = await self.status(service)
        if current.status is ServiceStatus.RUNNING:
            return current

        script = _lm_studio_start_script(self.context.project_root)
        if not script.is_file():
            detail = f"LM Studio launcher script was not found: {script.name}"
            await self._log(service, "ERROR", detail)
            return DriverResult(status=ServiceStatus.ERROR, detail=detail)

        command = _script_command(script)
        if command is None:
            detail = "a shell for the LM Studio launcher script was not found"
            await self._log(service, "ERROR", detail)
            return DriverResult(status=ServiceStatus.ERROR, detail=detail)

        await self._log(service, "INFO", "LM Studio Desktop launch requested")
        result = await run_command(
            command,
            cwd=self.context.project_root,
            timeout_seconds=30,
        )
        if result.return_code != 0:
            detail = result.failure or (
                f"LM Studio Desktop launcher exited with code {result.return_code}"
            )
            tail = _sanitized_cli_tail(result.stderr or result.stdout)
            if tail:
                detail = f"{detail}: {tail}"
            await self._log(service, "ERROR", detail)
            return DriverResult(status=ServiceStatus.ERROR, detail=detail)

        desktop = await asyncio.to_thread(_find_lm_studio_desktop_process)
        if desktop is not None:
            pid, create_time = desktop
            await self._log(service, "INFO", "LM Studio Desktop is running")
            return DriverResult(
                status=ServiceStatus.RUNNING,
                detail="LM Studio Desktop is running",
                pid=pid,
                uptime_seconds=max(0.0, time.time() - create_time),
                metadata={"desktop": True, "model": service.model_identifier},
            )
        return DriverResult(
            status=ServiceStatus.STARTING,
            detail="LM Studio Desktop launch requested; waiting for the app process",
            metadata={"desktop": True, "model": service.model_identifier},
        )

    async def stop(self, service: ServiceConfig) -> DriverResult:
        # LM Studio Desktop is intentionally user-owned. A project stop must never
        # close the app, stop its server, or unload a model.
        self.context.state_store.remove(service.name)
        desktop = await asyncio.to_thread(_find_lm_studio_desktop_process)
        await self._log(
            service,
            "INFO",
            "project stop requested; LM Studio Desktop was left running",
        )
        return DriverResult(
            status=ServiceStatus.STOPPED,
            detail="LM Studio Desktop was left running; close it manually when desired",
            pid=desktop[0] if desktop is not None else None,
            metadata={
                "desktop_left_running": desktop is not None,
                "model": service.model_identifier,
            },
        )

    async def status(self, service: ServiceConfig) -> DriverResult:
        desktop = await asyncio.to_thread(_find_lm_studio_desktop_process)
        if desktop is None:
            return DriverResult(
                status=ServiceStatus.STOPPED,
                detail="LM Studio Desktop is not running",
                metadata={"desktop": True, "model": service.model_identifier},
            )
        pid, create_time = desktop
        return DriverResult(
            status=ServiceStatus.RUNNING,
            detail="LM Studio Desktop is running",
            pid=pid,
            uptime_seconds=max(0.0, time.time() - create_time),
            metadata={"desktop": True, "model": service.model_identifier},
        )

    async def _log(self, service: ServiceConfig, level: str, message: str) -> None:
        log_path = service.log_file or self.context.logs_dir / f"{service.name}.log"
        await asyncio.to_thread(_append_lifecycle_log, log_path, level, message)


def build_drivers(context: DriverContext) -> dict[DriverType, ServiceDriver]:
    return {
        DriverType.PROCESS: ProcessDriver(context),
        DriverType.DOCKER_COMPOSE: DockerComposeDriver(context),
        DriverType.EXTERNAL: ExternalDriver(context),
        DriverType.LM_STUDIO: LMStudioDriver(context),
    }


async def run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    environment: dict[str, str] | None = None,
) -> CommandResult:
    if not command:
        return CommandResult(return_code=-1, failure="empty command")
    process_environment = os.environ.copy()
    if environment:
        process_environment.update(environment)
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            env=process_environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        return CommandResult(
            return_code=-1,
            failure=f"could not execute {command[0]!r}: {type(exc).__name__}",
        )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_seconds)
    except asyncio.CancelledError:
        with suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        raise
    except TimeoutError:
        process.kill()
        await process.wait()
        return CommandResult(
            return_code=-1,
            failure=f"{command[0]!r} timed out after {timeout_seconds:g}s",
        )
    return CommandResult(
        return_code=process.returncode or 0,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


def _resolve_executable(command: str, cwd: Path) -> str | None:
    candidate = Path(command).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        resolved = candidate if candidate.is_absolute() else cwd / candidate
        return str(resolved.resolve()) if resolved.is_file() else None
    return shutil.which(command)


def _command_fingerprint(command: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for part in command:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _docker_config_fingerprint(service: ServiceConfig) -> str:
    assert service.compose_file is not None
    assert service.compose_service is not None
    return _command_fingerprint(
        (
            str(service.compose_file.resolve()),
            service.compose_service,
            *service.profiles,
        )
    )


def _lm_studio_start_script(project_root: Path) -> Path:
    filename = "start_lm_studio.ps1" if os.name == "nt" else "start_lm_studio.sh"
    return project_root / "scripts" / "control-center" / filename


def _script_command(script: Path) -> tuple[str, ...] | None:
    if os.name == "nt":
        shell = shutil.which("powershell.exe") or shutil.which("pwsh")
        if shell is None:
            return None
        return (
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        )
    shell = shutil.which("sh")
    return (shell, str(script)) if shell is not None else None


def _find_lm_studio_desktop_process() -> tuple[int, float] | None:
    """Return one visible LM Studio app identity without waking or mutating it."""

    try:
        import psutil

        for process in psutil.process_iter(("pid", "name", "exe", "create_time")):
            try:
                info = process.info
                candidates = (info.get("name"), info.get("exe"))
                if not any(_is_lm_studio_desktop_name(value) for value in candidates):
                    continue
                pid = int(info["pid"])
                create_time = float(info["create_time"])
                return pid, create_time
            except (KeyError, TypeError, ValueError, psutil.Error):
                continue
    except (ImportError, OSError):
        return None
    return None


def _is_lm_studio_desktop_name(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    basename = Path(value).name
    stem = Path(basename).stem
    return re.sub(r"[^a-z0-9]", "", stem.lower()) == "lmstudio"


def _process_create_time(pid: int) -> float | None:
    try:
        import psutil

        return float(psutil.Process(pid).create_time())
    except (ImportError, OSError):
        return None
    except Exception:
        return None


def _pid_matches(state: ManagedServiceState) -> bool:
    if state.pid is None or state.process_create_time is None:
        return False
    try:
        import psutil

        process = psutil.Process(state.pid)
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return False
        return abs(process.create_time() - state.process_create_time) < 0.01
    except ImportError:
        return False
    except Exception:
        return False


async def _wait_for_exit(state: ManagedServiceState, timeout_seconds: float) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if not _pid_matches(state):
            return True
        await asyncio.sleep(0.1)
    return not _pid_matches(state)


def _terminate_process_tree(state: ManagedServiceState, timeout_seconds: float) -> bool:
    if state.pid is None or not _pid_matches(state):
        return True
    _send_graceful_signal(state)
    graceful_deadline = time.monotonic() + max(0.1, timeout_seconds * 0.6)
    while time.monotonic() < graceful_deadline:
        if not _pid_matches(state):
            return True
        time.sleep(0.1)
    try:
        import psutil

        parent = psutil.Process(state.pid)
        processes = [*parent.children(recursive=True), parent]
        for process in processes:
            with suppress(psutil.Error):
                process.terminate()
        remaining = max(0.1, timeout_seconds - timeout_seconds * 0.6)
        _gone, alive = psutil.wait_procs(processes, timeout=remaining)
        for process in alive:
            with suppress(psutil.Error):
                process.kill()
        if alive:
            psutil.wait_procs(alive, timeout=3)
        return not _pid_matches(state)
    except ImportError:
        return not _pid_matches(state)
    except Exception:
        return False


def _send_graceful_signal(state: ManagedServiceState) -> bool:
    if state.pid is None:
        return False
    try:
        if os.name == "nt":
            ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", None)
            if ctrl_break is None:
                return False
            os.kill(state.pid, ctrl_break)
        else:
            kill_process_group = getattr(os, "killpg", None)
            if kill_process_group is None:
                return False
            kill_process_group(state.pid, signal.SIGTERM)
        return True
    except (OSError, ValueError):
        return False


_CHILD_TASKS: set[asyncio.Task[int]] = set()


def _track_child(process: asyncio.subprocess.Process) -> None:
    task = asyncio.create_task(process.wait())
    _CHILD_TASKS.add(task)
    task.add_done_callback(_CHILD_TASKS.discard)


def _parse_compose_ps(value: str) -> dict[str, Any] | None:
    value = value.strip()
    if not value:
        return None
    try:
        payload = json.loads(value)
        if isinstance(payload, list):
            return payload[0] if payload and isinstance(payload[0], dict) else None
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        for line in value.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
    return None


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|token|authorization|password)(\s*[:=]\s*)\S+"
)
_BEARER_TOKEN = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_OPENAI_STYLE_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


def _sanitized_cli_tail(value: str, limit: int = 500) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()][-4:]
    result = " | ".join(lines)
    result = _ANSI_ESCAPE.sub("", result)
    home = str(Path.home())
    if home:
        result = result.replace(home, "~")
    result = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", result)
    result = _BEARER_TOKEN.sub("Bearer [REDACTED]", result)
    result = _OPENAI_STYLE_KEY.sub("[REDACTED]", result)
    return result[:limit]


def _append_lifecycle_log(path: Path, level: str, message: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_log(path)
        safe_message = _sanitized_cli_tail(message, limit=1000)
        timestamp = datetime.now(UTC).isoformat()
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{timestamp} {level} {safe_message}\n")
    except OSError:
        # A logging failure must not obscure the lifecycle result returned to the caller.
        return


def _rotate_log(path: Path, max_bytes: int = 25 * 1024 * 1024, backups: int = 3) -> None:
    try:
        if not path.is_file() or path.stat().st_size <= max_bytes:
            return
        oldest = path.with_name(f"{path.name}.{backups}")
        oldest.unlink(missing_ok=True)
        for index in range(backups - 1, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            if source.exists():
                source.replace(path.with_name(f"{path.name}.{index + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))
    except OSError:
        return


__all__ = [
    "CommandResult",
    "DockerComposeDriver",
    "DriverContext",
    "ExternalDriver",
    "LMStudioDriver",
    "ProcessDriver",
    "ServiceDriver",
    "build_drivers",
    "run_command",
]
