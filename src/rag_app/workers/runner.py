"""Generic asynchronous worker loop."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from .queue import Job, JobQueue

Handler = Callable[[dict[str, Any]], Awaitable[None] | None]


class Worker:
    def __init__(self, queue: JobQueue, handlers: dict[str, Handler]) -> None:
        self.queue = queue
        self.handlers = handlers

    async def run_once(self, timeout: float = 1.0) -> bool:
        job = await self.queue.claim(timeout)
        if job is None:
            return False
        await self._execute(job)
        return True

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        while not stop.is_set():
            await self.run_once()

    async def _execute(self, job: Job) -> None:
        try:
            handler = self.handlers[job.kind]
            result = handler(job.payload)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            await self.queue.fail(job.id, f"{type(exc).__name__}: {exc}")
        else:
            await self.queue.complete(job.id)
