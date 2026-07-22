from __future__ import annotations

from pathlib import Path

from rag_app.control_center.config import initialize_control_center, load_control_center_config
from rag_app.control_center.models import DriverType

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "ops" / "control-center" / "settings.yaml"


def test_default_control_center_topology_matches_canonical_runtime() -> None:
    config = load_control_center_config(
        CONFIG_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )
    services = {service.name: service for service in config.services}

    assert set(services) == {"api", "worker", "redis", "qdrant", "lm_studio"}
    assert services["api"].dependencies == ("lm_studio", "redis", "qdrant")
    assert services["worker"].dependencies == ("redis", "qdrant")

    assert services["api"].command == (
        "uv",
        "run",
        "rag",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    )
    assert services["worker"].command == ("uv", "run", "rag", "worker")
    assert services["redis"].driver is DriverType.DOCKER_COMPOSE
    assert services["qdrant"].profiles == ("qdrant",)


def test_default_lm_studio_contract_is_launch_only() -> None:
    config = load_control_center_config(
        CONFIG_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )
    lm_studio = next(service for service in config.services if service.name == "lm_studio")

    assert lm_studio.driver is DriverType.LM_STUDIO
    assert lm_studio.model_identifier == "gpt-oss-20b"
    assert lm_studio.health is not None
    assert lm_studio.health.type == "process"
    assert lm_studio.health.url is None


def test_default_logs_are_kept_under_ignored_runtime_directory() -> None:
    config = load_control_center_config(
        CONFIG_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )

    assert all(
        service.log_file is not None and service.log_file.is_relative_to(config.logs_dir)
        for service in config.services
    )


def test_cross_platform_wrappers_cover_main_lifecycle_actions() -> None:
    scripts = PROJECT_ROOT / "scripts" / "control-center"
    for action in ("start_all", "stop_all", "restart_all", "status", "dashboard"):
        assert (scripts / f"{action}.ps1").is_file()
        assert (scripts / f"{action}.sh").is_file()
    assert (scripts / "start_lm_studio.ps1").is_file()
    assert (scripts / "start_lm_studio.sh").is_file()


def test_generated_configuration_keeps_the_canonical_service_contract(tmp_path: Path) -> None:
    generated_path = tmp_path / "ops" / "control-center" / "settings.yaml"
    created = initialize_control_center(generated_path, project_root=tmp_path)
    generated = load_control_center_config(
        generated_path,
        project_root=tmp_path,
        environment={},
    )
    checked_in = load_control_center_config(
        CONFIG_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )

    assert created
    assert initialize_control_center(generated_path, project_root=tmp_path) == []
    assert {service.name for service in generated.services} == {
        service.name for service in checked_in.services
    }
    assert {
        service.name: (service.driver, set(service.dependencies), service.command)
        for service in generated.services
    } == {
        service.name: (service.driver, set(service.dependencies), service.command)
        for service in checked_in.services
    }
    generated_lm = next(service for service in generated.services if service.name == "lm_studio")
    checked_in_lm = next(
        service for service in checked_in.services if service.name == "lm_studio"
    )
    assert generated_lm.model_identifier == checked_in_lm.model_identifier
    assert generated_lm.health == checked_in_lm.health
    assert generated.startup_timeout_seconds == checked_in.startup_timeout_seconds
    assert generated.shutdown_timeout_seconds == checked_in.shutdown_timeout_seconds
