"""Read-only host diagnostics and resource/AI telemetry."""

from __future__ import annotations

import asyncio
import csv
import importlib.util
import os
import shutil
import sys
from collections.abc import Sequence
from io import StringIO
from pathlib import Path

import httpx

from rag_app.control_center.drivers import run_command
from rag_app.control_center.models import (
    AIMetrics,
    ControlCenterConfig,
    DiagnosticCheck,
    DriverType,
    GpuMetrics,
    ServiceConfig,
    ServiceSnapshot,
    ServiceStatus,
    SystemMetrics,
)


async def collect_system_metrics(config: ControlCenterConfig) -> SystemMetrics:
    cpu: float | None = None
    memory_percent: float | None = None
    memory_used: int | None = None
    memory_total: int | None = None
    try:
        import psutil

        cpu, virtual_memory = await asyncio.to_thread(
            lambda: (float(psutil.cpu_percent(interval=0.1)), psutil.virtual_memory())
        )
        memory_percent = float(virtual_memory.percent)
        memory_used = int(virtual_memory.used)
        memory_total = int(virtual_memory.total)
    except (ImportError, OSError):
        pass
    return SystemMetrics(
        cpu_percent=cpu,
        memory_percent=memory_percent,
        memory_used_bytes=memory_used,
        memory_total_bytes=memory_total,
        gpu=await _gpu_metrics(config.project_root),
    )


async def run_system_checks(
    config: ControlCenterConfig,
    services: Sequence[ServiceConfig] | None = None,
) -> list[DiagnosticCheck]:
    checks: list[DiagnosticCheck] = [
        DiagnosticCheck(
            name="Python",
            ok=sys.version_info >= (3, 12),
            detail=f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            solution="Install Python 3.12 or newer." if sys.version_info < (3, 12) else None,
        ),
        DiagnosticCheck(
            name="Project root",
            ok=config.project_root.is_dir(),
            detail=str(config.project_root),
            solution="Correct project_root in control-center settings."
            if not config.project_root.is_dir()
            else None,
        ),
        DiagnosticCheck(
            name="psutil",
            ok=importlib.util.find_spec("psutil") is not None,
            detail="installed"
            if importlib.util.find_spec("psutil") is not None
            else "not installed",
            solution=(
                None
                if importlib.util.find_spec("psutil") is not None
                else "Install the project dashboard dependencies."
            ),
        ),
    ]
    for label, path in (("Runtime directory", config.runtime_dir), ("Logs directory", config.logs_dir)):
        writable = _path_is_writable(path)
        checks.append(
            DiagnosticCheck(
                name=label,
                ok=writable,
                detail=str(path),
                solution="Choose a writable path in control-center settings." if not writable else None,
            )
        )

    for variable in config.required_environment:
        present = os.environ.get(variable) not in {None, ""}
        checks.append(
            DiagnosticCheck(
                name=f"Environment: {variable}",
                ok=present,
                detail="set" if present else "missing",
                solution=f"Set {variable} in the process environment or project .env.",
            )
        )

    executables: dict[str, tuple[str, bool]] = {}
    docker_required = False
    uv_required = False
    lm_desktop_required = False
    node_services: list[ServiceConfig] = []
    selected_services = config.services if services is None else services
    for service in selected_services:
        if not service.enabled:
            continue
        if service.driver is DriverType.PROCESS and service.command:
            executables.setdefault(service.command[0], (service.command[0], True))
            executable_name = Path(service.command[0]).stem.lower()
            uv_required = uv_required or executable_name == "uv"
            if executable_name in {"node", "npm", "npx"}:
                node_services.append(service)
        elif service.driver is DriverType.DOCKER_COMPOSE:
            executables.setdefault("docker", ("docker", True))
            docker_required = True
        elif service.driver is DriverType.LM_STUDIO:
            lm_desktop_required = True
            if os.name == "nt":
                shell = "powershell" if shutil.which("powershell") else "pwsh"
            else:
                shell = "sh"
            executables.setdefault(shell, (shell, True))

    if lm_desktop_required:
        launcher_name = "start_lm_studio.ps1" if os.name == "nt" else "start_lm_studio.sh"
        launcher = config.project_root / "scripts" / "control-center" / launcher_name
        checks.append(
            DiagnosticCheck(
                name="LM Studio Desktop launcher",
                ok=launcher.is_file(),
                detail=str(launcher),
                solution="Restore the LM Studio launcher script." if not launcher.is_file() else None,
            )
        )

    for label, (executable, required) in sorted(executables.items()):
        available = _executable_available(executable, config.project_root)
        checks.append(
            DiagnosticCheck(
                name=f"Executable: {label}",
                ok=available,
                detail="available" if available else "not found",
                solution=f"Install {label} or correct the service command." if not available else None,
                severity="required" if required else "optional",
            )
        )

    if uv_required and shutil.which("uv") is not None:
        sync_command = ["uv", "sync", "--check"]
        for extra in config.required_uv_extras:
            sync_command.extend(("--extra", extra))
        synchronized = await run_command(
            tuple(sync_command),
            cwd=config.project_root,
            timeout_seconds=60,
        )
        sync_solution = "uv sync" + "".join(
            f" --extra {extra}" for extra in config.required_uv_extras
        )
        checks.append(
            DiagnosticCheck(
                name="Python environment",
                ok=synchronized.return_code == 0,
                detail=(
                    "dependencies are synchronized"
                    if synchronized.return_code == 0
                    else "environment is missing or out of sync"
                ),
                solution=(
                    f"Run `{sync_solution}` before starting services."
                    if synchronized.return_code != 0
                    else None
                ),
            )
        )
        for package in config.required_python_packages:
            installed = importlib.util.find_spec(package) is not None
            solution = "Run `uv sync --all-extras`."
            if package == "qdrant_client":
                solution = "Run `uv sync --extra qdrant`."
            elif package == "sentence_transformers":
                solution = "Run `uv sync --extra local-models`."
            checks.append(
                DiagnosticCheck(
                    name=f"Python package: {package}",
                    ok=installed,
                    detail="installed" if installed else "missing",
                    solution=None if installed else solution,
                )
            )

    if node_services:
        for executable in ("node", "npm"):
            available = shutil.which(executable) is not None
            checks.append(
                DiagnosticCheck(
                    name=f"Executable: {executable}",
                    ok=available,
                    detail="available" if available else "not found",
                    solution=(
                        f"Install Node.js so `{executable}` is available."
                        if not available
                        else None
                    ),
                )
            )
        if shutil.which("npm") is not None:
            checked_directories: set[Path] = set()
            for service in node_services:
                executable_name = Path(service.command[0]).stem.lower()
                if executable_name not in {"npm", "npx"}:
                    continue
                cwd = service.cwd or config.project_root
                if cwd in checked_directories:
                    continue
                checked_directories.add(cwd)
                package_json = cwd / "package.json"
                if not package_json.is_file():
                    checks.append(
                        DiagnosticCheck(
                            name=f"Node project: {service.name}",
                            ok=False,
                            detail=f"package.json not found in {cwd}",
                            solution="Correct the service cwd or add package.json.",
                        )
                    )
                    continue
                packages = await run_command(
                    ("npm", "ls", "--depth=0", "--silent"),
                    cwd=cwd,
                    timeout_seconds=30,
                )
                checks.append(
                    DiagnosticCheck(
                        name=f"Node packages: {service.name}",
                        ok=packages.return_code == 0,
                        detail=(
                            "installed"
                            if packages.return_code == 0
                            else "missing or invalid dependencies"
                        ),
                        solution=(
                            "Run `npm install` in the configured service directory."
                            if packages.return_code != 0
                            else None
                        ),
                    )
                )

    if docker_required and shutil.which("docker") is not None:
        compose = await run_command(
            ("docker", "compose", "version"),
            cwd=config.project_root,
            timeout_seconds=15,
        )
        checks.append(
            DiagnosticCheck(
                name="Docker Compose",
                ok=compose.return_code == 0,
                detail="available" if compose.return_code == 0 else "plugin unavailable",
                solution=(
                    "Install the Docker Compose v2 plugin."
                    if compose.return_code != 0
                    else None
                ),
            )
        )
        docker = await run_command(
            ("docker", "info", "--format", "{{.ServerVersion}}"),
            cwd=config.project_root,
            timeout_seconds=15,
        )
        checks.append(
            DiagnosticCheck(
                name="Docker daemon",
                ok=docker.return_code == 0,
                detail="reachable" if docker.return_code == 0 else "unavailable",
                solution="Start Docker Desktop or the Docker daemon."
                if docker.return_code != 0
                else None,
            )
        )

    gpu = await _gpu_metrics(config.project_root)
    gpu_required = config.require_gpu or config.require_cuda
    checks.append(
        DiagnosticCheck(
            name="NVIDIA GPU",
            ok=bool(gpu),
            detail=(", ".join(item.name for item in gpu) if gpu else "not detected"),
            solution=(
                "Install a compatible NVIDIA driver, or configure LM Studio for CPU mode."
                if not gpu
                else None
            ),
            severity="required" if gpu_required else "optional",
        )
    )

    cuda_available = shutil.which("nvcc") is not None or bool(gpu)
    checks.append(
        DiagnosticCheck(
            name="CUDA",
            ok=cuda_available,
            detail="available" if cuda_available else "not detected",
            solution=(
                None
                if cuda_available
                else "Install the NVIDIA driver/CUDA toolkit, or run in CPU mode."
            ),
            severity="required" if config.require_cuda else "optional",
        )
    )
    return checks


async def collect_ai_metrics(
    config: ControlCenterConfig,
    lm_snapshot: ServiceSnapshot | None,
) -> AIMetrics:
    model = None
    loaded = False
    detail_parts: list[str] = []
    if lm_snapshot is not None:
        value = lm_snapshot.metadata.get("model")
        model = str(value) if value is not None else None
        loaded = lm_snapshot.status is ServiceStatus.RUNNING
        if lm_snapshot.detail:
            detail_parts.append(lm_snapshot.detail)

    values: dict[str, float] = {}
    if config.ai_metrics_url:
        headers: dict[str, str] = {}
        token = os.environ.get(config.ai_metrics_token_env)
        if not token and config.ai_metrics_token_env == "RAG_ADMIN_TOKEN":
            try:
                from rag_app.core.config import get_settings

                secret = get_settings().admin_token
                token = secret.get_secret_value() if secret is not None else None
            except Exception:
                token = None
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(config.ai_metrics_url, headers=headers)
                response.raise_for_status()
            values = _parse_prometheus_values(
                response.text,
                {
                    "rag_query_requests_total",
                    "rag_query_requests_per_minute",
                    "rag_query_latency_last_ms",
                    "rag_query_latency_average_ms",
                },
            )
        except Exception as exc:
            detail_parts.append(f"API metrics unavailable: {type(exc).__name__}")

    return AIMetrics(
        model=model,
        loaded=loaded,
        gpu=await _gpu_metrics(config.project_root),
        requests_total=values.get("rag_query_requests_total"),
        requests_per_minute=values.get("rag_query_requests_per_minute"),
        latency_last_ms=_latency_value(values, "rag_query_latency_last_ms"),
        latency_average_ms=_latency_value(values, "rag_query_latency_average_ms"),
        detail="; ".join(detail_parts),
    )


async def _gpu_metrics(project_root: Path) -> list[GpuMetrics]:
    if shutil.which("nvidia-smi") is None:
        return []
    result = await run_command(
        (
            "nvidia-smi",
            "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits",
        ),
        cwd=project_root,
        timeout_seconds=10,
    )
    if result.return_code != 0:
        return []
    rows = csv.reader(StringIO(result.stdout), skipinitialspace=True)
    metrics: list[GpuMetrics] = []
    for row in rows:
        if len(row) < 6:
            continue
        try:
            metrics.append(
                GpuMetrics(
                    index=int(row[0].strip()),
                    name=row[1].strip(),
                    utilization_percent=_optional_float(row[2]),
                    memory_used_bytes=_mib_to_bytes(row[3]),
                    memory_total_bytes=_mib_to_bytes(row[4]),
                    temperature_c=_optional_float(row[5]),
                )
            )
        except ValueError:
            continue
    return metrics


def _executable_available(executable: str, root: Path) -> bool:
    candidate = Path(executable).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        candidate = candidate if candidate.is_absolute() else root / candidate
        return candidate.is_file()
    return shutil.which(executable) is not None


def _path_is_writable(path: Path) -> bool:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate.is_dir() and os.access(candidate, os.W_OK)


def _optional_float(value: str) -> float | None:
    normalized = value.strip()
    if normalized.lower() in {"n/a", "na", "[not supported]", ""}:
        return None
    return float(normalized)


def _mib_to_bytes(value: str) -> int | None:
    parsed = _optional_float(value)
    return None if parsed is None else int(parsed * 1024 * 1024)


def _parse_prometheus_values(text: str, names: set[str]) -> dict[str, float]:
    values: dict[str, float] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        metric, separator, raw_value = line.partition(" ")
        base_name = metric.partition("{")[0]
        if separator and base_name in names and "{" not in metric:
            try:
                values[base_name] = float(raw_value.strip().split()[0])
            except (ValueError, IndexError):
                continue
    return values


def _latency_value(values: dict[str, float], name: str) -> float | None:
    total = values.get("rag_query_requests_total")
    if total is not None and total <= 0:
        return None
    return values.get(name)


__all__ = ["collect_ai_metrics", "collect_system_metrics", "run_system_checks"]
