"""YAML configuration loading for the control center.

The default loader applies ``.env`` without overriding the real process environment,
matching the application runtime. Substituted values are never included in errors.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from rag_app.control_center.models import ControlCenterConfig

DEFAULT_CONFIG_PATH = Path("ops/control-center/settings.yaml")
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


class ControlCenterConfigError(ValueError):
    """Raised when the dashboard configuration cannot be safely loaded."""


def initialize_control_center(
    path: str | Path | None = None,
    *,
    project_root: str | Path | None = None,
    overwrite: bool = False,
) -> list[Path]:
    """Create a usable local-RAG configuration without overwriting user files.

    Returns the paths created. Existing settings make the default operation a
    no-op; callers must explicitly opt into replacing an existing template.
    """

    root = Path(project_root or Path.cwd()).expanduser().resolve()
    settings_path = Path(path or DEFAULT_CONFIG_PATH).expanduser()
    if not settings_path.is_absolute():
        settings_path = (root / settings_path).resolve()
    else:
        settings_path = settings_path.resolve()
    try:
        settings_relative = settings_path.relative_to(root)
    except ValueError as exc:
        raise ControlCenterConfigError("configuration path must be inside project_root") from exc
    if settings_path.exists() and not overwrite:
        return []

    services_dir = settings_path.parent / "services"
    services_relative = services_dir.relative_to(root).as_posix()
    settings = f"""version: 1
project_name: prod-rag
runtime_dir: .data/control-center/runtime
logs_dir: .data/control-center/logs
services_dir: {services_relative}
startup_timeout_seconds: 600
shutdown_timeout_seconds: 30
required_environment: []
required_python_packages: [qdrant_client, sentence_transformers]
required_uv_extras: [qdrant, local-models]
require_gpu: false
require_cuda: false
ai_metrics_url: http://127.0.0.1:8000/metrics
ai_metrics_token_env: RAG_ADMIN_TOKEN
"""
    manifests = _default_service_manifests()
    created: list[Path] = []
    services_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in manifests.items():
        target = services_dir / filename
        if target.exists() and not overwrite:
            continue
        _write_text(target, content, overwrite=overwrite)
        created.append(target)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    _write_text(settings_path, settings, overwrite=overwrite)
    created.append(settings_path)
    # Keep the relative-path validation above visible to static tools and future edits.
    del settings_relative
    return created


def load_control_center_config(
    path: str | Path | None = None,
    *,
    project_root: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> ControlCenterConfig:
    """Load settings and service manifests into one validated immutable contract."""

    root = Path(project_root or Path.cwd()).expanduser().resolve()
    config_path = Path(path or DEFAULT_CONFIG_PATH).expanduser()
    if not config_path.is_absolute():
        config_path = root / config_path
    config_path = config_path.resolve()
    if not config_path.is_file():
        raise ControlCenterConfigError(f"control-center config not found: {config_path}")

    if environment is None:
        # Match the main runtime's precedence: real environment wins over .env.
        # Values are used for interpolation/child propagation but never emitted.
        from dotenv import load_dotenv

        load_dotenv(root / ".env", override=False)
        env: Mapping[str, str] = os.environ
    else:
        env = environment
    raw = _load_yaml_mapping(config_path)
    raw = _expand_environment(raw, env)

    configured_root = raw.get("project_root")
    if configured_root:
        candidate = Path(str(configured_root)).expanduser()
        root = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    raw["project_root"] = root

    services_dir_value = raw.get("services_dir", "ops/control-center/services")
    services_dir: Path | None
    if services_dir_value is None:
        services_dir = None
    else:
        services_dir = _resolve_path(root, services_dir_value)
        raw["services_dir"] = services_dir

    inline_services = raw.get("services", [])
    if inline_services is None:
        inline_services = []
    if not isinstance(inline_services, list):
        raise ControlCenterConfigError("settings 'services' must be a list")

    service_documents: list[dict[str, Any]] = []
    for entry in inline_services:
        if not isinstance(entry, dict):
            raise ControlCenterConfigError("each inline service must be a mapping")
        service_documents.append(dict(entry))

    if services_dir is not None:
        if not services_dir.exists():
            if not service_documents:
                raise ControlCenterConfigError(f"services directory not found: {services_dir}")
        elif not services_dir.is_dir():
            raise ControlCenterConfigError(f"services path is not a directory: {services_dir}")
        else:
            for manifest in sorted((*services_dir.glob("*.yaml"), *services_dir.glob("*.yml"))):
                loaded = _expand_environment(_load_yaml_mapping(manifest), env)
                nested = loaded.get("service")
                if isinstance(nested, dict) and len(loaded) == 1:
                    loaded = nested
                many = loaded.get("services")
                if isinstance(many, list) and len(loaded) == 1:
                    for item in many:
                        if not isinstance(item, dict):
                            raise ControlCenterConfigError(
                                f"services in {manifest} must contain mappings"
                            )
                        service_documents.append(dict(item))
                else:
                    service_documents.append(dict(loaded))

    raw["runtime_dir"] = _resolve_path(root, raw.get("runtime_dir", ".data/control-center"))
    raw["logs_dir"] = _resolve_path(
        root, raw.get("logs_dir", ".data/control-center/logs")
    )
    raw["services"] = [_resolve_service_paths(item, root) for item in service_documents]

    try:
        return ControlCenterConfig.model_validate(raw)
    except ValidationError as exc:
        problems = []
        for issue in exc.errors(include_url=False, include_context=False, include_input=False):
            location = ".".join(str(part) for part in issue.get("loc", ())) or "configuration"
            problems.append(f"{location}: {issue.get('msg', 'invalid value')}")
        raise ControlCenterConfigError(
            f"invalid control-center configuration: {'; '.join(problems)}"
        ) from exc
    except ValueError as exc:
        raise ControlCenterConfigError(
            f"invalid control-center configuration: {type(exc).__name__}"
        ) from exc


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - depends on installation extras
        raise ControlCenterConfigError(
            "PyYAML is required for the control center; install the dashboard extra"
        ) from exc

    try:
        with path.open("r", encoding="utf-8") as handle:
            value = yaml.safe_load(handle)
    except OSError as exc:
        raise ControlCenterConfigError(f"cannot read control-center config: {path}") from exc
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" at line {mark.line + 1}" if mark is not None else ""
        raise ControlCenterConfigError(f"invalid YAML in {path}{location}") from exc
    if not isinstance(value, dict):
        raise ControlCenterConfigError(f"YAML document must be a mapping: {path}")
    return dict(value)


def _expand_environment(value: Any, environment: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _expand_environment(item, environment) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_environment(item, environment) for item in value]
    if not isinstance(value, str):
        return value

    missing: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        resolved = environment.get(name)
        if resolved is not None:
            return resolved
        if default is not None:
            return default
        missing.add(name)
        return ""

    expanded = _ENV_PATTERN.sub(replace, value)
    if missing:
        raise ControlCenterConfigError(
            f"required environment variables are missing: {', '.join(sorted(missing))}"
        )
    return expanded


def _resolve_path(root: Path, value: object) -> Path:
    path = Path(str(value)).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _resolve_service_paths(service: dict[str, Any], root: Path) -> dict[str, Any]:
    result = dict(service)
    for field in ("cwd", "compose_file", "log_file"):
        value = result.get(field)
        if value is not None:
            result[field] = _resolve_path(root, value)
    return result


def _write_text(path: Path, content: str, *, overwrite: bool) -> None:
    mode = "w" if overwrite else "x"
    try:
        with path.open(mode, encoding="utf-8", newline="\n") as handle:
            handle.write(content.rstrip() + "\n")
    except FileExistsError:
        if overwrite:
            raise


def _default_service_manifests() -> dict[str, str]:
    return {
        "redis.yaml": """name: redis
display_name: Redis
description: Ingestion queue, job state, and idempotency storage
driver: docker_compose
enabled: true
dependencies: []
log_file: .data/control-center/logs/redis.log
compose_file: docker-compose.yml
compose_service: redis
profiles: []
health:
  type: tcp
  host: 127.0.0.1
  port: ${REDIS_PORT:-6379}
  timeout_seconds: 3
  interval_seconds: 1
  retries: 30
""",
        "qdrant.yaml": """name: qdrant
display_name: Qdrant
description: Dense and sparse vector index
driver: docker_compose
enabled: true
dependencies: []
log_file: .data/control-center/logs/qdrant.log
compose_file: docker-compose.yml
compose_service: qdrant
profiles: [qdrant]
health:
  type: http
  url: http://127.0.0.1:${QDRANT_PORT:-6333}/healthz
  timeout_seconds: 3
  interval_seconds: 2
  retries: 30
  expected_statuses: [200]
""",
        "lm_studio.yaml": """name: lm_studio
display_name: LM Studio
description: Launch-only LM Studio desktop application
driver: lm_studio
enabled: true
dependencies: []
log_file: .data/control-center/logs/lm-studio.log
health:
  type: process
  timeout_seconds: 2
  interval_seconds: 1
  retries: 30
model_identifier: ${RAG_LLM__MODEL:-gpt-oss-20b}
""",
        "api.yaml": """name: api
display_name: Backend API
description: Canonical persistent FastAPI RAG runtime
driver: process
enabled: true
dependencies: [lm_studio, redis, qdrant]
log_file: .data/control-center/logs/api.log
command:
  - uv
  - run
  - rag
  - serve
  - --host
  - ${RAG_CONTROL_API_HOST:-127.0.0.1}
  - --port
  - ${RAG_CONTROL_API_PORT:-8000}
cwd: .
environment:
  PYTHONUNBUFFERED: "1"
health:
  type: http
  url: http://${RAG_CONTROL_API_HOST:-127.0.0.1}:${RAG_CONTROL_API_PORT:-8000}/health/ready
  timeout_seconds: 5
  interval_seconds: 3
  retries: 180
  expected_statuses: [200]
""",
        "worker.yaml": """name: worker
display_name: Ingestion Worker
description: Redis-backed local file ingestion and indexing worker
driver: process
enabled: true
command: [uv, run, rag, worker]
dependencies: [redis, qdrant]
log_file: .data/control-center/logs/worker.log
cwd: .
environment:
  PYTHONUNBUFFERED: "1"
health:
  type: process
  timeout_seconds: 2
  interval_seconds: 1
  retries: 15
""",
    }


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ControlCenterConfigError",
    "initialize_control_center",
    "load_control_center_config",
]
