"""High-level indexing orchestration."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .embeddings import EmbeddingProvider, SparseEmbeddingProvider
from .vector_stores import VectorRecord, VectorStore


def _field(value: object, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


@dataclass(slots=True)
class IndexingResult:
    indexed: int
    ids: list[str]
    model_id: str


def _chunk_fields(chunk: object, position: int) -> tuple[str, str, dict[str, Any]]:
    text = _field(chunk, "text") or _field(chunk, "content") or _field(chunk, "page_content")
    if text is None:
        raise TypeError("Chunk requires text, content, or page_content")
    metadata = dict(_field(chunk, "metadata", {}) or {})
    for key in ("tenant_id", "document_id", "parent_id", "chunk_index", "acl"):
        value = _field(chunk, key)
        if value is not None and key not in metadata:
            metadata[key] = value
    identifier = _field(chunk, "id") or _field(chunk, "chunk_id")
    if identifier is None:
        digest = hashlib.sha256(
            f"{metadata.get('document_id', '')}:{position}:{text}".encode()
        ).hexdigest()
        identifier = digest
    return str(identifier), str(text), metadata


class IndexingService:
    def __init__(
        self,
        embeddings: EmbeddingProvider,
        store: VectorStore,
        *,
        sparse_embeddings: SparseEmbeddingProvider | None = None,
    ) -> None:
        self.embeddings = embeddings
        self.sparse_embeddings = sparse_embeddings
        self.store = store

    async def index(self, chunks: Sequence[object]) -> IndexingResult:
        values = [_chunk_fields(chunk, index) for index, chunk in enumerate(chunks)]
        texts = [item[1] for item in values]
        vectors = await self.embeddings.embed_documents(texts)
        if len(vectors) != len(values):
            raise ValueError("Embedding provider returned an unexpected vector count")
        sparse_vectors = (
            await self.sparse_embeddings.embed_documents(texts)
            if self.sparse_embeddings is not None
            else [None] * len(values)
        )
        if len(sparse_vectors) != len(values):
            raise ValueError("Sparse embedding provider returned an unexpected vector count")
        records = [
            VectorRecord(identifier, list(vector), text, metadata, sparse)
            for (identifier, text, metadata), vector, sparse in zip(
                values, vectors, sparse_vectors, strict=True
            )
        ]
        if records:
            await self.store.upsert(records)
        return IndexingResult(
            indexed=len(records),
            ids=[record.id for record in records],
            model_id=self.embeddings.model_id,
        )

    async def delete(
        self,
        ids: Sequence[str] | None = None,
        *,
        filters: Mapping[str, Any] | None = None,
    ) -> int:
        return await self.store.delete(ids, filters=filters)
