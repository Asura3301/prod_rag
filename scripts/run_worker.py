"""Run a Redis-backed ingestion worker."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from rag_app.core import setup_logging
from rag_app.ingestion import IngestionPipeline
from rag_app.workers import RedisJobQueue, Worker

logger = logging.getLogger(__name__)


async def ingest(payload: dict[str, Any]) -> None:
    """Load and chunk a source; deployments can inject persistent registry/index adapters."""
    source = payload.get("source")
    if not isinstance(source, str) or not source:
        raise ValueError("ingestion payload requires a source")
    result = await asyncio.to_thread(IngestionPipeline().ingest, Path(source))
    if result.failed_count:
        errors = [item.error for item in result.items if item.error]
        raise RuntimeError("; ".join(errors))
    logger.info(
        "Ingested source=%s documents=%d chunks=%d unchanged=%d",
        source,
        result.ingested_count,
        result.chunk_count,
        result.unchanged_count,
    )


async def main() -> None:
    setup_logging(os.getenv("RAG_LOG_LEVEL", "INFO"), json_output=True)
    queue = RedisJobQueue(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    await Worker(queue, {"ingest": ingest}).run_forever()


if __name__ == "__main__":
    asyncio.run(main())
