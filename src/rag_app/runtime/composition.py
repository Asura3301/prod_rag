"""Environment-driven composition root for the end-to-end local runtime."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI

from rag_app.api.app import create_app as create_api_app
from rag_app.core import Settings, get_settings, setup_logging
from rag_app.indexing import (
    BatchedCachedEmbeddingProvider,
    EmbeddingProvider,
    HashEmbeddingProvider,
    HashSparseEmbeddingProvider,
    HuggingFaceEmbeddingProvider,
    InMemoryVectorStore,
    QdrantVectorStore,
    VectorStore,
)
from rag_app.ingestion import StructureAwareChunker, TextChunker
from rag_app.llm import LLMConfig as ProviderLLMConfig
from rag_app.llm import get_llm
from rag_app.retrieval import HybridRetriever
from rag_app.workers import RedisJobQueue, Worker

from .repository import SQLiteDocumentRepository
from .service import LocalRAGService
from .worker import LocalIngestionHandler


def _secret(value: Any) -> str | None:
    return value.get_secret_value() if value is not None else None


def build_local_service(settings: Settings) -> LocalRAGService:
    dimensions = settings.embeddings.dimensions
    if dimensions is None:
        raise ValueError("RAG_EMBEDDINGS__DIMENSIONS is required for the local runtime")

    embedding_provider = settings.embeddings.provider.casefold()
    base_embeddings: EmbeddingProvider
    if embedding_provider in {"local", "huggingface", "sentence_transformers"}:
        base_embeddings = HuggingFaceEmbeddingProvider(settings.embeddings.model)
    elif embedding_provider == "hash":
        base_embeddings = HashEmbeddingProvider(dimensions=dimensions)
    else:
        raise ValueError(
            "Local runtime embeddings provider must be 'local', 'huggingface', or 'hash'"
        )
    embeddings = BatchedCachedEmbeddingProvider(
        base_embeddings,
        batch_size=settings.embeddings.batch_size,
    )
    sparse_embeddings = HashSparseEmbeddingProvider()

    store_provider = settings.vector_store.provider.casefold()
    store: VectorStore
    if store_provider == "qdrant":
        if not settings.vector_store.url:
            raise ValueError("RAG_VECTOR_STORE__URL is required for Qdrant")
        store = QdrantVectorStore(
            settings.vector_store.index_name,
            url=settings.vector_store.url,
            api_key=_secret(settings.vector_store.api_key),
        )
    elif store_provider == "memory":
        store = InMemoryVectorStore()
    else:
        raise ValueError("Local runtime vector store must be 'qdrant' or 'memory'")

    retriever = HybridRetriever(
        embeddings,
        store,
        sparse_embeddings=sparse_embeddings,
    )
    llm = get_llm(ProviderLLMConfig.from_core(settings.llm))
    chunker = StructureAwareChunker(
        fallback=TextChunker(
            chunk_size=settings.chunking.chunk_size,
            overlap=settings.chunking.chunk_overlap,
        )
    )
    return LocalRAGService(
        embeddings=embeddings,
        sparse_embeddings=sparse_embeddings,
        store=store,
        repository=SQLiteDocumentRepository(settings.sqlite_path),
        retriever=retriever,
        llm=llm,
        dimensions=dimensions,
        chunker=chunker,
        score_threshold=settings.retrieval.score_threshold,
    )


def create_runtime_app(settings: Settings | None = None) -> FastAPI:
    """Uvicorn factory used by ``rag serve``."""

    selected = settings or get_settings()
    if selected.runtime_mode != "local":
        raise ValueError("Only RAG_RUNTIME_MODE=local is implemented")
    setup_logging(selected.logging.level, json_output=selected.logging.json_output)
    service = build_local_service(selected)
    queue = RedisJobQueue(selected.redis_url)
    return create_api_app(
        service=service,
        queue=queue,
        api_token=_secret(selected.api_token),
        admin_token=_secret(selected.admin_token),
    )


async def run_local_worker(settings: Settings | None = None) -> None:
    selected = settings or get_settings()
    if selected.runtime_mode != "local":
        raise ValueError("Only RAG_RUNTIME_MODE=local is implemented")
    setup_logging(selected.logging.level, json_output=selected.logging.json_output)
    service = build_local_service(selected)
    await service.startup()
    queue = RedisJobQueue(selected.redis_url)
    try:
        handler = LocalIngestionHandler(
            service,
            ingestion_root=selected.ingestion_root,
        )
        await Worker(queue, {"ingest": handler}).run_forever()
    finally:
        await queue.aclose()
        await service.shutdown()


def worker_main() -> None:
    asyncio.run(run_local_worker())


__all__ = [
    "build_local_service",
    "create_runtime_app",
    "run_local_worker",
    "worker_main",
]
