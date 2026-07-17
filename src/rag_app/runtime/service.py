"""End-to-end local RAG service backed by Qdrant and SQLite."""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langchain_core.documents import Document

from rag_app.api.models import Citation, DocumentResponse, QueryResponse
from rag_app.generation import GenerationConfig, build_rag_graph
from rag_app.indexing import (
    EmbeddingProvider,
    IndexingService,
    SparseEmbeddingProvider,
    VectorStore,
)
from rag_app.ingestion import StructureAwareChunker
from rag_app.ingestion.chunking import ChunkingStrategy
from rag_app.ingestion.pipeline import stable_id
from rag_app.retrieval import HybridRetriever, RetrievalRequest

from .repository import SQLiteDocumentRepository, StoredDocument


class GraphRetrieverAdapter:
    """Expose HybridRetriever through LangGraph's async invocation contract."""

    def __init__(
        self,
        retriever: HybridRetriever,
        *,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> None:
        self.retriever = retriever
        self.top_k = top_k
        self.filters = filters or {}

    async def ainvoke(self, query: str) -> list[dict[str, Any]]:
        result = await self.retriever.retrieve(
            RetrievalRequest(
                query=query,
                top_k=self.top_k,
                profile="balanced",
                filters=self.filters,
            )
        )
        return [
            {
                "text": candidate.text,
                "score": candidate.score,
                "metadata": {
                    **candidate.metadata,
                    "chunk_id": candidate.id,
                },
            }
            for candidate in result.candidates
        ]


class LocalRAGService:
    """Persistent single-user local service for API and worker composition."""

    def __init__(
        self,
        *,
        embeddings: EmbeddingProvider,
        store: VectorStore,
        repository: SQLiteDocumentRepository,
        retriever: HybridRetriever,
        llm: Any,
        dimensions: int,
        sparse_embeddings: SparseEmbeddingProvider | None = None,
        chunker: ChunkingStrategy | None = None,
        score_threshold: float | None = None,
    ) -> None:
        self.embeddings = embeddings
        self.store = store
        self.repository = repository
        self.retriever = retriever
        self.dimensions = dimensions
        self.chunker = chunker or StructureAwareChunker()
        self.indexing = IndexingService(
            embeddings,
            store,
            sparse_embeddings=sparse_embeddings,
        )
        self._llm = llm
        self._score_threshold = score_threshold
        self._ready = False

    async def startup(self) -> None:
        await self.repository.initialize()
        probe = await self.embeddings.embed_query("dimension probe")
        if len(probe) != self.dimensions:
            raise ValueError(
                f"Embedding model returned {len(probe)} dimensions; expected {self.dimensions}"
            )
        await self.store.create(self.dimensions)
        self._ready = True

    async def shutdown(self) -> None:
        client = getattr(self.store, "_client", None)
        close = getattr(client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result
        self._ready = False

    async def add_document(
        self, source: str, text: str, metadata: dict[str, Any] | None = None
    ) -> DocumentResponse:
        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("Document text cannot be empty")
        values = dict(metadata or {})
        content_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        current = await self.repository.get_by_source(source)
        if current is not None and current.content_hash == content_hash:
            return self._response(current)

        document_id = current.id if current is not None else str(uuid4())
        version = current.version + 1 if current is not None else 1
        chunks = self._chunks(
            document_id=document_id,
            source=source,
            text=normalized_text,
            metadata=values,
            content_hash=content_hash,
            version=version,
        )
        result = await self.indexing.index(chunks)
        stored = StoredDocument(
            id=document_id,
            source=source,
            text=normalized_text,
            metadata=values,
            created_at=current.created_at if current is not None else datetime.now(UTC),
            content_hash=content_hash,
            version=version,
            chunk_ids=tuple(result.ids),
        )
        await self.repository.upsert(stored)
        if current is not None:
            stale = [chunk_id for chunk_id in current.chunk_ids if chunk_id not in result.ids]
            if stale:
                await self.store.delete(stale)
        return self._response(stored)

    def _chunks(
        self,
        *,
        document_id: str,
        source: str,
        text: str,
        metadata: dict[str, Any],
        content_hash: str,
        version: int,
    ) -> list[Document]:
        base = Document(page_content=text, metadata=metadata)
        chunks: list[Document] = []
        for index, content in enumerate(self.chunker.split(base)):
            chunk_id = stable_id("chunk", document_id, content_hash, str(index), content)
            chunks.append(
                Document(
                    page_content=content,
                    metadata={
                        **metadata,
                        "source": source,
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "chunk_index": index,
                        "document_version": version,
                        "content_hash": content_hash,
                    },
                )
            )
        if not chunks:
            raise ValueError("No non-empty chunks produced")
        return chunks

    async def index_ingested_document(
        self,
        *,
        document_id: str,
        source: str,
        text: str,
        chunks: list[Document],
        content_hash: str,
        version: int,
        metadata: dict[str, Any],
    ) -> StoredDocument:
        current = await self.repository.get_by_source(source)
        if current is not None and current.content_hash == content_hash:
            return current
        effective_document_id = current.id if current is not None else document_id
        effective_version = current.version + 1 if current is not None else version
        normalized_chunks = [
            Document(
                page_content=chunk.page_content,
                metadata={
                    **chunk.metadata,
                    "document_id": effective_document_id,
                    "document_version": effective_version,
                },
            )
            for chunk in chunks
        ]
        indexed = await self.indexing.index(normalized_chunks)
        stored = StoredDocument(
            id=effective_document_id,
            source=source,
            text=text,
            metadata=metadata,
            created_at=current.created_at if current is not None else datetime.now(UTC),
            content_hash=content_hash,
            version=effective_version,
            chunk_ids=tuple(indexed.ids),
        )
        await self.repository.upsert(stored)
        if current is not None:
            stale = [chunk_id for chunk_id in current.chunk_ids if chunk_id not in indexed.ids]
            if stale:
                await self.store.delete(stale)
        return stored

    async def query(self, text: str, top_k: int, filters: dict[str, Any]) -> QueryResponse:
        started = time.perf_counter()
        graph = build_rag_graph(
            GraphRetrieverAdapter(self.retriever, top_k=top_k, filters=filters),
            self._llm,
            config=GenerationConfig(minimum_score=self._score_threshold),
        )
        result = await graph.ainvoke({"request": text})
        response = result["response"]
        citations = [
            Citation(
                document_id=item["document_id"],
                excerpt=item["excerpt"],
                score=float(item["score"] or 0.0),
            )
            for item in response["citations"]
        ]
        return QueryResponse(
            answer=response["answer"],
            citations=citations,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    async def list_documents(self) -> list[DocumentResponse]:
        return [
            self._response(document)
            for document in await self.repository.list_documents()
        ]

    async def get_document(self, document_id: str) -> DocumentResponse | None:
        document = await self.repository.get(document_id)
        return self._response(document) if document is not None else None

    async def delete_document(self, document_id: str) -> bool:
        document = await self.repository.delete(document_id)
        if document is None:
            return False
        if document.chunk_ids:
            await self.store.delete(document.chunk_ids)
        return True

    async def ready(self) -> bool:
        return self._ready

    @staticmethod
    def _response(document: StoredDocument) -> DocumentResponse:
        return DocumentResponse(
            id=document.id,
            source=document.source,
            text=document.text,
            metadata=document.metadata,
            created_at=document.created_at,
        )


__all__ = ["GraphRetrieverAdapter", "LocalRAGService"]
