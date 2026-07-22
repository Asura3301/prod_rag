"""Bounded readiness probes shared by every lifecycle driver."""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from rag_app.control_center.models import HealthCheckConfig, HealthResult


async def probe_health(
    check: HealthCheckConfig,
    *,
    process_running: bool | None = None,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
) -> HealthResult:
    if check.type == "process":
        if process_running:
            return HealthResult(ok=True, detail="process is running")
        return HealthResult(ok=False, detail="process is not running")

    if check.type == "http":
        assert check.url is not None
        safe_url = _redacted_url(check.url)
        try:
            async with httpx.AsyncClient(timeout=check.timeout_seconds, follow_redirects=False) as client:
                response = await client.get(check.url)
            accepted = (
                response.status_code in check.expected_statuses
                if check.expected_statuses
                else 200 <= response.status_code < 300
            )
            return HealthResult(
                ok=accepted,
                detail=f"{safe_url} returned HTTP {response.status_code}",
            )
        except Exception as exc:
            return HealthResult(ok=False, detail=f"{safe_url}: {type(exc).__name__}")

    if check.type == "tcp":
        assert check.port is not None
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(check.host, check.port), timeout=check.timeout_seconds
            )
            writer.close()
            await writer.wait_closed()
            return HealthResult(ok=True, detail=f"TCP {check.host}:{check.port} is reachable")
        except Exception as exc:
            return HealthResult(
                ok=False,
                detail=f"TCP {check.host}:{check.port}: {type(exc).__name__}",
            )

    command_environment = os.environ.copy()
    if environment:
        command_environment.update(environment)
    try:
        process = await asyncio.create_subprocess_exec(
            *check.command,
            cwd=str(cwd) if cwd else None,
            env=command_environment,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            return_code = await asyncio.wait_for(process.wait(), timeout=check.timeout_seconds)
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
            return HealthResult(ok=False, detail="health command timed out")
        return HealthResult(
            ok=return_code == 0,
            detail=f"health command exited with code {return_code}",
        )
    except (OSError, ValueError) as exc:
        return HealthResult(ok=False, detail=f"health command failed: {type(exc).__name__}")


async def wait_for_health(
    check: HealthCheckConfig,
    *,
    process_running: bool | None = None,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> HealthResult:
    """Poll until ready, retry budget exhaustion, or the outer timeout expires."""

    deadline = asyncio.get_running_loop().time() + (
        timeout_seconds
        if timeout_seconds is not None
        else check.retries * (check.timeout_seconds + check.interval_seconds)
    )
    last = HealthResult(ok=False, detail="health check was not executed")
    for attempt in range(check.retries):
        last = await probe_health(
            check,
            process_running=process_running,
            cwd=cwd,
            environment=environment,
        )
        if last.ok:
            return last
        if attempt + 1 < check.retries:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(check.interval_seconds, remaining))
    return last


def _redacted_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        if parsed.port:
            hostname = f"{hostname}:{parsed.port}"
        return urlunsplit((parsed.scheme, hostname, parsed.path, "", ""))
    except ValueError:
        return "<invalid URL>"


__all__ = ["probe_health", "wait_for_health"]
