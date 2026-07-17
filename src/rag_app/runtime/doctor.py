"""Connectivity diagnostics for the local host runtime."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str


async def run_doctor(
    *,
    llm_base_url: str,
    llm_model: str,
    redis_url: str,
    qdrant_url: str,
    ingestion_root: str | Path,
    sqlite_path: str | Path,
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    checks.append(
        DoctorCheck(
            "sentence-transformers",
            importlib.util.find_spec("sentence_transformers") is not None,
            "installed" if importlib.util.find_spec("sentence_transformers") else "missing",
        )
    )

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{llm_base_url.rstrip('/')}/models")
            response.raise_for_status()
            model_ids = {
                str(item.get("id"))
                for item in response.json().get("data", [])
                if isinstance(item, dict)
            }
        checks.append(
            DoctorCheck(
                "LM Studio",
                llm_model in model_ids,
                f"model {llm_model!r} available"
                if llm_model in model_ids
                else f"available models: {', '.join(sorted(model_ids)) or '<none>'}",
            )
        )
    except Exception as exc:
        checks.append(DoctorCheck("LM Studio", False, f"{type(exc).__name__}: {exc}"))

    try:
        from redis.asyncio import from_url

        redis = from_url(redis_url, decode_responses=True)
        try:
            pong = await redis.ping()
        finally:
            await redis.aclose()
        checks.append(DoctorCheck("Redis", bool(pong), redis_url))
    except Exception as exc:
        checks.append(DoctorCheck("Redis", False, f"{type(exc).__name__}: {exc}"))

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{qdrant_url.rstrip('/')}/collections")
            response.raise_for_status()
        checks.append(DoctorCheck("Qdrant", True, qdrant_url))
    except Exception as exc:
        checks.append(DoctorCheck("Qdrant", False, f"{type(exc).__name__}: {exc}"))

    root = Path(ingestion_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    checks.append(
        DoctorCheck(
            "ingestion root",
            root.is_dir(),
            str(root) if root.is_dir() else f"directory does not exist: {root}",
        )
    )
    database_parent = Path(sqlite_path).expanduser().resolve().parent
    database_parent.mkdir(parents=True, exist_ok=True)
    checks.append(
        DoctorCheck(
            "SQLite directory",
            database_parent.is_dir(),
            str(database_parent)
            if database_parent.is_dir()
            else f"directory does not exist: {database_parent}",
        )
    )
    return checks


__all__ = ["DoctorCheck", "run_doctor"]
