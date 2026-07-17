"""Async job queues used by ingestion workers."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, cast
from uuid import uuid4


class JobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class Job:
    id: str
    kind: str
    payload: dict[str, Any]
    idempotency_key: str
    state: JobState = JobState.PENDING
    attempts: int = 0
    max_attempts: int = 3
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        now = datetime.now(UTC).isoformat()
        self.created_at = self.created_at or now
        self.updated_at = self.updated_at or now


class JobQueue(Protocol):
    async def enqueue(
        self, kind: str, payload: dict[str, Any], idempotency_key: str, max_attempts: int = 3
    ) -> Job: ...

    async def claim(self, timeout: float | None = None) -> Job | None: ...
    async def complete(self, job_id: str) -> Job: ...
    async def fail(self, job_id: str, error: str) -> Job: ...
    async def cancel(self, job_id: str) -> Job: ...
    async def get(self, job_id: str) -> Job | None: ...
    async def list(self) -> list[Job]: ...


class InMemoryJobQueue:
    """Concurrency-safe queue suitable for tests and a single API process."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._keys: dict[str, str] = {}
        self._ready: asyncio.Queue[str] = asyncio.Queue()
        self._lock = asyncio.Lock()

    async def enqueue(
        self, kind: str, payload: dict[str, Any], idempotency_key: str, max_attempts: int = 3
    ) -> Job:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        async with self._lock:
            if existing_id := self._keys.get(idempotency_key):
                return self._jobs[existing_id]
            job = Job(
                id=str(uuid4()),
                kind=kind,
                payload=payload,
                idempotency_key=idempotency_key,
                max_attempts=max_attempts,
            )
            self._jobs[job.id] = job
            self._keys[idempotency_key] = job.id
            self._ready.put_nowait(job.id)
            return job

    async def claim(self, timeout: float | None = None) -> Job | None:
        try:
            job_id = (
                await self._ready.get()
                if timeout is None
                else await asyncio.wait_for(self._ready.get(), timeout)
            )
        except TimeoutError:
            return None
        async with self._lock:
            job = self._jobs[job_id]
            if job.state is not JobState.PENDING:
                return None
            job.state = JobState.RUNNING
            job.attempts += 1
            job.updated_at = datetime.now(UTC).isoformat()
            return job

    async def complete(self, job_id: str) -> Job:
        async with self._lock:
            job = self._required(job_id)
            if job.state is JobState.CANCELLED:
                return job
            if job.state is not JobState.RUNNING:
                raise ValueError(f"cannot complete job in {job.state} state")
            job.state = JobState.SUCCEEDED
            job.updated_at = datetime.now(UTC).isoformat()
            return job

    async def fail(self, job_id: str, error: str) -> Job:
        async with self._lock:
            job = self._required(job_id)
            if job.state is JobState.CANCELLED:
                return job
            if job.state is not JobState.RUNNING:
                raise ValueError(f"cannot fail job in {job.state} state")
            job.error = error
            job.updated_at = datetime.now(UTC).isoformat()
            if job.attempts >= job.max_attempts:
                job.state = JobState.DEAD_LETTER
            else:
                job.state = JobState.PENDING
                self._ready.put_nowait(job.id)
            return job

    async def cancel(self, job_id: str) -> Job:
        async with self._lock:
            job = self._required(job_id)
            if job.state not in {JobState.SUCCEEDED, JobState.DEAD_LETTER}:
                job.state = JobState.CANCELLED
                job.updated_at = datetime.now(UTC).isoformat()
            return job

    async def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    async def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    def _required(self, job_id: str) -> Job:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise KeyError(f"unknown job: {job_id}") from exc


class RedisJobQueue:
    """Redis queue adapter; importing this module does not require Redis."""

    def __init__(self, redis_url: str, namespace: str = "rag") -> None:
        try:
            from redis.asyncio import from_url
        except ImportError as exc:
            raise RuntimeError("Install the 'redis' package to use RedisJobQueue") from exc
        self._redis = from_url(redis_url, decode_responses=True)
        self._ns = namespace

    def _job_key(self, job_id: str) -> str:
        return f"{self._ns}:job:{job_id}"

    async def aclose(self) -> None:
        await self._redis.aclose()

    async def enqueue(
        self, kind: str, payload: dict[str, Any], idempotency_key: str, max_attempts: int = 3
    ) -> Job:
        key = f"{self._ns}:idempotency:{idempotency_key}"
        existing = await self._redis.get(key)
        if existing:
            return await self._required(existing)
        job = Job(str(uuid4()), kind, payload, idempotency_key, max_attempts=max_attempts)
        if not await self._redis.set(key, job.id, nx=True):
            return await self._required(await self._redis.get(key))
        await self._save(job)
        await self._redis.rpush(f"{self._ns}:ready", job.id)
        return job

    async def claim(self, timeout: float | None = None) -> Job | None:
        ready_key = f"{self._ns}:ready"
        if timeout is None:
            result = await self._redis.blpop(ready_key, timeout=0)
            job_id = result[1] if result else None
        else:
            deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
            job_id = cast(bytes | str | None, await self._redis.lpop(ready_key))
            while job_id is None:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return None
                await asyncio.sleep(min(0.1, remaining))
                job_id = cast(bytes | str | None, await self._redis.lpop(ready_key))
        if not job_id:
            return None
        job = await self._required(job_id)
        if job.state is not JobState.PENDING:
            return None
        job.state, job.attempts = JobState.RUNNING, job.attempts + 1
        await self._save(job)
        return job

    async def complete(self, job_id: str) -> Job:
        job = await self._required(job_id)
        if job.state is JobState.CANCELLED:
            return job
        if job.state is not JobState.RUNNING:
            raise ValueError(f"cannot complete job in {job.state} state")
        job.state = JobState.SUCCEEDED
        await self._save(job)
        return job

    async def fail(self, job_id: str, error: str) -> Job:
        job = await self._required(job_id)
        if job.state is JobState.CANCELLED:
            return job
        if job.state is not JobState.RUNNING:
            raise ValueError(f"cannot fail job in {job.state} state")
        job.error = error
        job.state = JobState.DEAD_LETTER if job.attempts >= job.max_attempts else JobState.PENDING
        await self._save(job)
        if job.state is JobState.PENDING:
            await self._redis.rpush(f"{self._ns}:ready", job.id)
        else:
            await self._redis.rpush(f"{self._ns}:dead", job.id)
        return job

    async def cancel(self, job_id: str) -> Job:
        job = await self._required(job_id)
        if job.state not in {JobState.SUCCEEDED, JobState.DEAD_LETTER}:
            job.state = JobState.CANCELLED
            await self._save(job)
        return job

    async def get(self, job_id: str) -> Job | None:
        raw = await self._redis.get(self._job_key(job_id))
        return self._decode(raw) if raw else None

    async def list(self) -> list[Job]:
        keys = [key async for key in self._redis.scan_iter(f"{self._ns}:job:*")]
        values = await self._redis.mget(keys) if keys else []
        jobs = (self._decode(value) for value in values if value is not None)
        return sorted(jobs, key=lambda job: job.created_at, reverse=True)

    async def _required(self, job_id: bytes | str | None) -> Job:
        normalized = self._text(job_id) if job_id is not None else None
        if not normalized or not (job := await self.get(normalized)):
            raise KeyError(f"unknown job: {normalized!r}")
        return job

    async def _save(self, job: Job) -> None:
        job.updated_at = datetime.now(UTC).isoformat()
        await self._redis.set(self._job_key(job.id), json.dumps(asdict(job)))

    @staticmethod
    def _decode(raw: bytes | str) -> Job:
        data = json.loads(RedisJobQueue._text(raw))
        data["state"] = JobState(data["state"])
        return Job(**data)

    @staticmethod
    def _text(value: bytes | str) -> str:
        return value.decode("utf-8") if isinstance(value, bytes) else value
