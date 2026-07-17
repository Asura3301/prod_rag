from __future__ import annotations

import os
from uuid import uuid4

import pytest

from rag_app.workers import RedisJobQueue

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_redis_claim_returns_none_when_queue_is_idle() -> None:
    queue = RedisJobQueue(
        os.getenv("RAG_REDIS_URL", "redis://127.0.0.1:6379/0"),
        namespace=f"rag-test-{uuid4().hex}",
    )
    try:
        assert await queue.claim(timeout=0.2) is None
    finally:
        await queue.aclose()
