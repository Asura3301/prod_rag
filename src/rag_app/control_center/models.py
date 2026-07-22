"""Typed configuration and result models for the project control center."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServiceStatus(StrEnum):
    RUNNING = "running"
    STARTING = "starting"
    ERROR = "error"
    STOPPED = "stopped"


class DriverType(StrEnum):
    PROCESS = "process"
    DOCKER_COMPOSE = "docker_compose"
    EXTERNAL = "external"
    LM_STUDIO = "lm_studio"


class HealthCheckConfig(_Model):
    """One readiness check. Commands are always executed without a shell."""

    type: Literal["process", "http", "tcp", "command"] = "process"
    url: str | None = None
    host: str = "127.0.0.1"
    port: int | None = Field(default=None, ge=1, le=65535)
    command: tuple[str, ...] = ()
    timeout_seconds: float = Field(default=5.0, gt=0, le=300)
    interval_seconds: float = Field(default=1.0, gt=0, le=60)
    retries: int = Field(default=30, ge=1, le=600)
    expected_statuses: tuple[int, ...] = (200,)

    @model_validator(mode="after")
    def validate_target(self) -> HealthCheckConfig:
        if self.type == "http" and not self.url:
            raise ValueError("an HTTP health check requires url")
        if self.type == "tcp" and self.port is None:
            raise ValueError("a TCP health check requires port")
        if self.type == "command" and not self.command:
            raise ValueError("a command health check requires command")
        return self


class ServiceConfig(_Model):
    """Declarative lifecycle definition for one service."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    display_name: str | None = None
    description: str = ""
    driver: DriverType
    enabled: bool = True
    dependencies: tuple[str, ...] = ()
    health: HealthCheckConfig | None = None
    log_file: Path | None = None

    # Process driver
    command: tuple[str, ...] = ()
    stop_command: tuple[str, ...] = ()
    cwd: Path | None = None
    environment: dict[str, str] = Field(default_factory=dict)

    # Docker Compose driver
    compose_file: Path | None = None
    compose_service: str | None = None
    profiles: tuple[str, ...] = ()

    # LM Studio Desktop launcher (display metadata only)
    model_identifier: str = "gpt-oss-20b"

    @model_validator(mode="after")
    def validate_driver_fields(self) -> ServiceConfig:
        if self.driver is DriverType.PROCESS and not self.command:
            raise ValueError(f"process service {self.name!r} requires command")
        if self.driver is DriverType.DOCKER_COMPOSE:
            if self.compose_file is None:
                raise ValueError(f"Docker service {self.name!r} requires compose_file")
            if not self.compose_service:
                raise ValueError(f"Docker service {self.name!r} requires compose_service")
        if self.driver is DriverType.EXTERNAL and self.health is None:
            raise ValueError(f"external service {self.name!r} requires a health check")
        if self.name in self.dependencies:
            raise ValueError(f"service {self.name!r} cannot depend on itself")
        return self

    @property
    def label(self) -> str:
        return self.display_name or self.name.replace("_", " ").title()


class ControlCenterConfig(_Model):
    version: Literal[1] = 1
    project_name: str = Field(default="Project Control Center", min_length=1)
    project_root: Path
    runtime_dir: Path = Path(".data/control-center")
    logs_dir: Path = Path(".data/control-center/logs")
    services_dir: Path | None = Path("ops/control-center/services")
    startup_timeout_seconds: float = Field(default=120.0, gt=0, le=3600)
    shutdown_timeout_seconds: float = Field(default=20.0, gt=0, le=300)
    lifecycle_lock_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    required_environment: tuple[str, ...] = ()
    required_python_packages: tuple[str, ...] = ("qdrant_client", "sentence_transformers")
    required_uv_extras: tuple[str, ...] = ("qdrant", "local-models")
    require_gpu: bool = False
    require_cuda: bool = False
    ai_metrics_url: str | None = "http://127.0.0.1:8000/metrics"
    ai_metrics_token_env: str = "RAG_ADMIN_TOKEN"
    services: tuple[ServiceConfig, ...] = ()

    @model_validator(mode="after")
    def validate_services(self) -> ControlCenterConfig:
        root = self.project_root.resolve()
        runtime_dir = self.runtime_dir.resolve()
        logs_dir = self.logs_dir.resolve()
        if not runtime_dir.is_relative_to(root):
            raise ValueError("runtime_dir must be located inside project_root")
        if not logs_dir.is_relative_to(root):
            raise ValueError("logs_dir must be located inside project_root")
        invalid_extras = [
            extra
            for extra in self.required_uv_extras
            if not extra
            or not extra[0].isalnum()
            or not all(character.isalnum() or character in "._-" for character in extra)
        ]
        if invalid_extras:
            raise ValueError("required_uv_extras contains an invalid extra name")
        names = [service.name for service in self.services]
        duplicates = sorted(name for name in set(names) if names.count(name) > 1)
        if duplicates:
            raise ValueError(f"duplicate service names: {', '.join(duplicates)}")
        known = set(names)
        for service in self.services:
            if service.log_file is not None and not service.log_file.resolve().is_relative_to(
                logs_dir
            ):
                raise ValueError(
                    f"log_file for service {service.name!r} must be located inside logs_dir"
                )
            missing = sorted(set(service.dependencies) - known)
            if missing:
                raise ValueError(
                    f"service {service.name!r} has unknown dependencies: {', '.join(missing)}"
                )
        _topological_order(self.services)
        return self


class ServiceSnapshot(_Model):
    name: str
    display_name: str
    status: ServiceStatus
    detail: str = ""
    pid: int | None = None
    uptime_seconds: float | None = None
    driver: DriverType
    health: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperationResult(_Model):
    success: bool
    action: str
    services: list[ServiceSnapshot] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime


class DiagnosticCheck(_Model):
    name: str
    ok: bool
    detail: str
    solution: str | None = None
    severity: Literal["required", "optional"] = "required"


class GpuMetrics(_Model):
    index: int
    name: str
    utilization_percent: float | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    temperature_c: float | None = None


class SystemMetrics(_Model):
    cpu_percent: float | None = None
    memory_percent: float | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    gpu: list[GpuMetrics] = Field(default_factory=list)
    sampled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AIMetrics(_Model):
    model: str | None = None
    loaded: bool = False
    gpu: list[GpuMetrics] = Field(default_factory=list)
    requests_total: float | None = None
    requests_per_minute: float | None = None
    latency_last_ms: float | None = None
    latency_average_ms: float | None = None
    sampled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    detail: str = ""


class DriverResult(_Model):
    status: ServiceStatus
    detail: str = ""
    pid: int | None = None
    uptime_seconds: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HealthResult(_Model):
    ok: bool
    detail: str


def _topological_order(services: tuple[ServiceConfig, ...]) -> tuple[str, ...]:
    dependencies = {service.name: set(service.dependencies) for service in services}
    result: list[str] = []
    ready = sorted(name for name, values in dependencies.items() if not values)
    while ready:
        name = ready.pop(0)
        result.append(name)
        for candidate in sorted(dependencies):
            if name in dependencies[candidate]:
                dependencies[candidate].remove(name)
                if not dependencies[candidate] and candidate not in result and candidate not in ready:
                    ready.append(candidate)
                    ready.sort()
    if len(result) != len(services):
        cyclic = sorted(set(dependencies) - set(result))
        raise ValueError(f"service dependency cycle: {', '.join(cyclic)}")
    return tuple(result)


__all__ = [
    "AIMetrics",
    "ControlCenterConfig",
    "DiagnosticCheck",
    "DriverResult",
    "DriverType",
    "GpuMetrics",
    "HealthCheckConfig",
    "HealthResult",
    "OperationResult",
    "ServiceConfig",
    "ServiceSnapshot",
    "ServiceStatus",
    "SystemMetrics",
]
