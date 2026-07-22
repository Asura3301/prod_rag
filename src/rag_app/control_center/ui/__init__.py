"""Lazy entry point for the optional Textual control center."""

from __future__ import annotations

from pathlib import Path


def run_dashboard(config_path: str | Path | None = None) -> None:
    """Build the project facade and run the interactive terminal dashboard.

    Textual is imported only after this function is called so the regular API, worker,
    and headless CLI commands remain importable in minimal environments.
    """

    try:
        from .dashboard import ControlCenterApp
    except ModuleNotFoundError as exc:
        if exc.name == "textual":
            raise RuntimeError(
                "The terminal dashboard requires Textual. Install the project's dashboard "
                "dependencies and run the command again."
            ) from exc
        raise

    from rag_app.control_center import ControlCenter

    center = ControlCenter.from_path(path=config_path)
    ControlCenterApp(center, config_path=config_path).run()


__all__ = ["run_dashboard"]
