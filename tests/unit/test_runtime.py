from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from rag_app.indexing import HashEmbeddingProvider, InMemoryVectorStore
from rag_app.retrieval import HybridRetriever
from rag_app.runtime.repository import SQLiteDocumentRepository, StoredDocument
from rag_app.runtime.service import LocalRAGService
from rag_app.runtime.worker import LocalIngestionHandler
from rag_app.workers import InMemoryJobQueue, JobState, Worker


class GroundedLLM:
    async def ainvoke(self, prompt: str) -> str:
        if "returned no usable evidence" in prompt:
            return "refund period"
        return "Refunds are accepted within 30 days [C1]."


@pytest.mark.asyncio
async def test_sqlite_repository_round_trip(tmp_path: Path) -> None:
    repository = SQLiteDocumentRepository(tmp_path / "rag.db")
    await repository.initialize()
    document = StoredDocument(
        id="doc-1",
        source="memory://policy",
        text="Refunds are accepted within 30 days.",
        metadata={"tenant": "demo"},
        created_at=datetime.now(UTC),
        content_hash="hash",
        version=1,
        chunk_ids=("chunk-1",),
    )

    await repository.upsert(document)

    assert await repository.get("doc-1") == document
    assert await repository.get_by_source("memory://policy") == document
    assert await repository.list_documents() == [document]
    assert await repository.delete("doc-1") == document
    assert await repository.get("doc-1") is None


@pytest.mark.asyncio
async def test_local_service_indexes_queries_updates_and_deletes(tmp_path: Path) -> None:
    embeddings = HashEmbeddingProvider(dimensions=64)
    store = InMemoryVectorStore()
    repository = SQLiteDocumentRepository(tmp_path / "rag.db")
    retriever = HybridRetriever(embeddings, store)
    service = LocalRAGService(
        embeddings=embeddings,
        store=store,
        repository=repository,
        retriever=retriever,
        llm=GroundedLLM(),
        dimensions=64,
    )
    await service.startup()

    created = await service.add_document(
        "memory://policy",
        "Refunds are accepted within 30 days.",
        {"tenant": "demo"},
    )
    first = await repository.get(created.id)
    assert first is not None
    assert first.version == 1
    assert first.chunk_ids

    response = await service.query("What is the refund period?", 5, {"tenant": "demo"})
    assert response.answer == "Refunds are accepted within 30 days [C1]."
    assert response.citations[0].document_id == created.id

    updated = await service.add_document(
        "memory://policy",
        "Refunds are accepted within 45 days.",
        {"tenant": "demo"},
    )
    second = await repository.get(updated.id)
    assert second is not None
    assert second.version == 2
    assert set(first.chunk_ids).isdisjoint(second.chunk_ids)
    assert await store.get(first.chunk_ids) == []

    assert await service.delete_document(created.id)
    assert await repository.get(created.id) is None
    assert await store.get(second.chunk_ids) == []


@pytest.mark.asyncio
async def test_worker_handler_indexes_only_sources_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "documents"
    root.mkdir()
    source = root / "policy.txt"
    source.write_text("Refunds are accepted within 30 days.", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("Must not be read.", encoding="utf-8")

    embeddings = HashEmbeddingProvider(dimensions=32)
    store = InMemoryVectorStore()
    repository = SQLiteDocumentRepository(tmp_path / "rag.db")
    service = LocalRAGService(
        embeddings=embeddings,
        store=store,
        repository=repository,
        retriever=HybridRetriever(embeddings, store),
        llm=GroundedLLM(),
        dimensions=32,
    )
    await service.startup()
    handler = LocalIngestionHandler(service, ingestion_root=root)

    await handler({"source": str(source), "metadata": {"tenant": "demo"}})
    stored = await repository.get_by_source(source.resolve().as_posix())
    assert stored is not None
    assert stored.metadata["tenant"] == "demo"

    with pytest.raises(ValueError, match="inside ingestion root"):
        await handler({"source": str(outside), "metadata": {}})


@pytest.mark.asyncio
async def test_offline_end_to_end_job_to_answer(tmp_path: Path) -> None:
    root = tmp_path / "documents"
    root.mkdir()
    source = root / "handbook.txt"
    source.write_text("Refunds are accepted within 30 days.", encoding="utf-8")
    embeddings = HashEmbeddingProvider(dimensions=48)
    store = InMemoryVectorStore()
    repository = SQLiteDocumentRepository(tmp_path / "rag.db")
    service = LocalRAGService(
        embeddings=embeddings,
        store=store,
        repository=repository,
        retriever=HybridRetriever(embeddings, store),
        llm=GroundedLLM(),
        dimensions=48,
    )
    await service.startup()
    queue = InMemoryJobQueue()
    worker = Worker(
        queue,
        {"ingest": LocalIngestionHandler(service, ingestion_root=root)},
    )
    job = await queue.enqueue(
        "ingest",
        {"source": str(source), "metadata": {"tenant": "demo"}},
        idempotency_key="handbook-v1",
    )

    assert await worker.run_once(timeout=0.1)
    completed = await queue.get(job.id)
    assert completed is not None
    assert completed.state is JobState.SUCCEEDED
    response = await service.query("What is the refund period?", 5, {"tenant": "demo"})
    assert response.answer == "Refunds are accepted within 30 days [C1]."
    assert response.citations
    assert response.citations[0].document_id
