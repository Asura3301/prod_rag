"""Structural contracts implemented by infrastructure adapters."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from rag_app.domain.models import ChunkRecord, Embedding, RAGResponse, SearchResult


@runtime_checkable
class EmbeddingProvider(Protocol):
    async def embed_documents(self, texts: Sequence[str]) -> list[Embedding]:
        """Embed document texts while preserving input order."""
        ...

    async def embed_query(self, text: str) -> Embedding:
        """Embed one search query."""
        ...


@runtime_checkable
class VectorStore(Protocol):
    async def upsert(
        self,
        chunks: Sequence[ChunkRecord],
        embeddings: Sequence[Embedding],
    ) -> None:
        """Insert or replace chunks and their vectors."""
        ...

    async def delete(self, chunk_ids: Sequence[str]) -> None:
        """Delete vectors by stable chunk identifier."""
        ...

    async def search(
        self,
        query_embedding: Embedding,
        *,
        top_k: int,
        filters: dict[str, object] | None = None,
    ) -> list[SearchResult]:
        """Return ranked nearest chunks."""
        ...


@runtime_checkable
class Retriever(Protocol):
    async def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        filters: dict[str, object] | None = None,
    ) -> list[SearchResult]:
        """Retrieve ranked evidence for a natural-language query."""
        ...


@runtime_checkable
class LLMProvider(Protocol):
    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
    ) -> str:
        """Generate text from a provider-neutral prompt."""
        ...


@runtime_checkable
class RAGService(Protocol):
    async def answer(self, query: str) -> RAGResponse:
        """Answer a query with citations."""
        ...


EmbeddingProviderProtocol = EmbeddingProvider
VectorStoreProtocol = VectorStore
RetrieverProtocol = Retriever
LLMProtocol = LLMProvider
