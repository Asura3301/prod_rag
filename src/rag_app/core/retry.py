"""Small async retry and timeout helper for provider calls."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 3
    timeout_seconds: float = 30.0
    initial_delay_seconds: float = 0.25
    max_delay_seconds: float = 4.0
    backoff_factor: float = 2.0
    jitter_seconds: float = 0.1

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be at least 1")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.initial_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays must be non-negative")
        if self.backoff_factor < 1:
            raise ValueError("backoff_factor must be at least 1")
        if self.jitter_seconds < 0:
            raise ValueError("jitter_seconds must be non-negative")


async def call_with_retry[T](
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy | None = None,
    retry_on: tuple[type[Exception], ...] = (Exception,),
) -> T:
    """Call an async operation with a per-attempt timeout and exponential backoff."""

    policy = policy or RetryPolicy()
    delay = policy.initial_delay_seconds

    for attempt in range(1, policy.attempts + 1):
        try:
            async with asyncio.timeout(policy.timeout_seconds):
                return await operation()
        except asyncio.CancelledError:
            raise
        except retry_on:
            if attempt == policy.attempts:
                raise
            jitter = random.uniform(0, policy.jitter_seconds)
            await asyncio.sleep(min(delay, policy.max_delay_seconds) + jitter)
            delay *= policy.backoff_factor

    raise RuntimeError("retry loop terminated unexpectedly")
