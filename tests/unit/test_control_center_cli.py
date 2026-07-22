from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from rag_app.control_center import ControlCenter, ControlCenterConfigError
from rag_app.control_center.models import (
    DiagnosticCheck,
    DriverType,
    OperationResult,
    ServiceSnapshot,
    ServiceStatus,
)
from rag_app.main import cli


def _snapshot(status: ServiceStatus = ServiceStatus.RUNNING) -> ServiceSnapshot:
    return ServiceSnapshot(
        name="api",
        display_name="Backend API",
        status=status,
        detail="ready" if status is ServiceStatus.RUNNING else "failed",
        pid=321 if status is ServiceStatus.RUNNING else None,
        driver=DriverType.PROCESS,
    )


class _FakeCenter:
    async def status_all(self) -> list[ServiceSnapshot]:
        return [_snapshot()]

    async def status(self, name: str) -> ServiceSnapshot:
        assert name == "api"
        return _snapshot()

    async def system_check(self) -> list[DiagnosticCheck]:
        return [
            DiagnosticCheck(
                name="GPU",
                ok=False,
                severity="optional",
                detail="not detected",
            )
        ]

    async def start_all(self) -> OperationResult:
        now = datetime.now(UTC)
        return OperationResult(
            success=True,
            action="start",
            services=[_snapshot()],
            started_at=now,
            finished_at=now,
        )

    async def tail_logs(self, name: str, lines: int = 200) -> list[str]:
        assert name == "api"
        assert lines == 2
        return ["\x1b]0;forged title\x07INFO ready", "\x1b[31mERROR\x1b[0m safe"]


def _install_fake(monkeypatch: object, center: _FakeCenter) -> None:
    # pytest's MonkeyPatch is intentionally kept out of the runtime type surface here.
    setter = monkeypatch.setattr
    setter(
        ControlCenter,
        "from_path",
        classmethod(lambda cls, path=None, project_root=None: center),
    )


def test_service_status_human_and_json_output(monkeypatch: object, capsys: object) -> None:
    center = _FakeCenter()
    _install_fake(monkeypatch, center)

    assert cli(["services", "status", "api"]) == 0
    human = capsys.readouterr().out
    assert "[OK] Backend API: running (PID 321)" in human

    assert cli(["services", "status", "--all", "--json"]) == 0
    payload = capsys.readouterr().out
    assert '"name": "api"' in payload
    assert '"status": "running"' in payload


def test_optional_preflight_warning_does_not_fail_cli(monkeypatch: object, capsys: object) -> None:
    _install_fake(monkeypatch, _FakeCenter())

    assert cli(["services", "check"]) == 0
    assert "[WARN] GPU: not detected" in capsys.readouterr().out


def test_start_all_uses_shared_control_center_facade(monkeypatch: object, capsys: object) -> None:
    _install_fake(monkeypatch, _FakeCenter())

    assert cli(
        [
            "services",
            "start",
            "--all",
            "--config",
            str(Path("ops/control-center/settings.yaml")),
        ]
    ) == 0
    assert "[OK] Backend API: running" in capsys.readouterr().out


def test_log_output_removes_terminal_control_sequences(monkeypatch: object, capsys: object) -> None:
    _install_fake(monkeypatch, _FakeCenter())

    assert cli(["services", "logs", "api", "--lines", "2"]) == 0
    output = capsys.readouterr().out
    assert "\x1b" not in output
    assert "INFO ready" in output
    assert "ERROR safe" in output


def test_configuration_errors_are_actionable_without_traceback(
    monkeypatch: object, capsys: object
) -> None:
    setter = monkeypatch.setattr

    def fail_to_load(cls: type[ControlCenter], path: str | None = None) -> None:
        del cls, path
        raise ControlCenterConfigError("invalid manifest")

    setter(ControlCenter, "from_path", classmethod(fail_to_load))

    assert cli(["services", "status", "--all"]) == 2
    captured = capsys.readouterr()
    assert "ERROR: invalid manifest" in captured.err
    assert "rag services init" in captured.err
    assert "Traceback" not in captured.err
