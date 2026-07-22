"""Durable ownership records for processes managed by the control center."""

from __future__ import annotations

import importlib
import json
import os
import threading
import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ManagedServiceState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pid: int | None = Field(default=None, ge=1)
    process_create_time: float | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    command_fingerprint: str | None = None
    managed: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class _StateDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    services: dict[str, ManagedServiceState] = Field(default_factory=dict)


class StateStoreError(RuntimeError):
    pass


class LifecycleLock:
    """Cross-platform advisory lock for lifecycle mutations across CLI/TUI processes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any | None = None

    def acquire(self, timeout_seconds: float) -> bool:
        if self._handle is not None:
            raise RuntimeError("lifecycle lock is already held by this instance")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                handle.seek(0)
                _lock_file(handle)
                self._handle = handle
                return True
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    handle.close()
                    return False
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            handle.seek(0)
            _unlock_file(handle)
        finally:
            handle.close()
            self._handle = None


class StateStore:
    """Small atomic JSON store; never persists commands or environment values."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def get(self, service_name: str) -> ManagedServiceState | None:
        with self._lock:
            return self._load().services.get(service_name)

    def set(self, service_name: str, value: ManagedServiceState) -> None:
        with self._lock:
            document = self._load()
            document.services[service_name] = value
            self._save(document)

    def remove(self, service_name: str) -> None:
        with self._lock:
            document = self._load()
            if document.services.pop(service_name, None) is not None:
                self._save(document)

    def all(self) -> dict[str, ManagedServiceState]:
        with self._lock:
            return dict(self._load().services)

    def _load(self) -> _StateDocument:
        if not self.path.exists():
            return _StateDocument()
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                return _StateDocument.model_validate(json.load(handle))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise StateStoreError(f"invalid control-center state file: {self.path}") from exc

    def _save(self, document: _StateDocument) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(document.model_dump(mode="json"), handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.path)
        except OSError as exc:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
            raise StateStoreError(f"cannot update control-center state: {self.path}") from exc


def _lock_file(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl: Any = importlib.import_module("fcntl")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl: Any = importlib.import_module("fcntl")
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = ["LifecycleLock", "ManagedServiceState", "StateStore", "StateStoreError"]
