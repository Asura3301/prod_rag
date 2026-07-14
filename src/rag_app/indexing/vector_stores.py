"""Async vector-store contracts and production adapters."""

from __future__ import annotations

import asyncio
import math
import re
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .embeddings import IndexingConfigurationError, SparseEmbedding


@dataclass(slots=True)
class VectorRecord:
    id: str
    vector: list[float]
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    sparse_vector: SparseEmbedding | None = None


@dataclass(slots=True)
class VectorSearchResult:
    record: VectorRecord
    score: float

    @property
    def id(self) -> str:
        return self.record.id

    @property
    def text(self) -> str:
        return self.record.text

    @property
    def metadata(self) -> dict[str, Any]:
        return self.record.metadata


@runtime_checkable
class VectorStore(Protocol):
    async def create(self, dimensions: int, *, recreate: bool = False) -> None: ...

    async def upsert(self, records: Sequence[VectorRecord]) -> None: ...

    async def delete(
        self, ids: Sequence[str] | None = None, *, filters: Mapping[str, Any] | None = None
    ) -> int: ...

    async def query(
        self,
        vector: Sequence[float],
        *,
        limit: int = 10,
        filters: Mapping[str, Any] | None = None,
    ) -> list[VectorSearchResult]: ...


def _field(value: object, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def coerce_vector_record(value: object) -> VectorRecord:
    """Coerce dataclasses, mappings, and planned domain chunks into a record."""
    if isinstance(value, VectorRecord):
        return value
    identifier = _field(value, "id") or _field(value, "chunk_id") or _field(value, "document_id")
    vector = _field(value, "vector")
    if vector is None:
        vector = _field(value, "embedding")
    text = _field(value, "text") or _field(value, "content") or _field(value, "page_content")
    metadata = dict(_field(value, "metadata", {}) or {})
    sparse_vector = _field(value, "sparse_vector")
    for key in ("tenant_id", "parent_id", "document_id", "chunk_index", "acl"):
        item = _field(value, key)
        if item is not None and key not in metadata:
            metadata[key] = item
    if identifier is None or vector is None or text is None:
        raise TypeError("A vector record requires id/chunk_id, vector/embedding, and text/content")
    return VectorRecord(
        id=str(identifier),
        vector=[float(item) for item in vector],
        text=str(text),
        metadata=metadata,
        sparse_vector=sparse_vector,
    )


def metadata_matches(metadata: Mapping[str, Any], filters: Mapping[str, Any] | None) -> bool:
    """Apply equality/membership filters, including tenant and ACL constraints."""
    if not filters:
        return True
    for key, expected in filters.items():
        actual = metadata.get(key)
        if key in {"acl", "allowed_principals"}:
            if expected is None:
                continue
            required = {str(item) for item in _as_sequence(expected)}
            available = {str(item) for item in _as_sequence(actual)}
            if not required.intersection(available):
                return False
        elif isinstance(expected, Mapping):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
        elif isinstance(expected, (list, tuple, set, frozenset)):
            if isinstance(actual, (list, tuple, set, frozenset)):
                if not set(expected).intersection(actual):
                    return False
            elif actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def _as_sequence(value: Any) -> Sequence[Any]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return value
    if isinstance(value, (set, frozenset)):
        return tuple(value)
    return (value,)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError(f"Vector dimension mismatch: {len(left)} != {len(right)}")
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def _qdrant_id(identifier: str) -> str | int:
    """Map arbitrary application IDs to Qdrant-compatible UUIDs."""
    try:
        return str(uuid.UUID(identifier))
    except (ValueError, AttributeError):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"rag-record:{identifier}"))


class InMemoryVectorStore:
    """Concurrency-safe baseline store with exact cosine search."""

    def __init__(self) -> None:
        self._dimensions: int | None = None
        self._records: dict[str, VectorRecord] = {}
        self._lock = asyncio.Lock()

    @property
    def dimensions(self) -> int | None:
        return self._dimensions

    async def create(self, dimensions: int, *, recreate: bool = False) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        async with self._lock:
            if self._dimensions is not None and self._dimensions != dimensions and not recreate:
                raise ValueError("Store already exists with different dimensions")
            self._dimensions = dimensions
            if recreate:
                self._records.clear()

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        values = [coerce_vector_record(record) for record in records]
        async with self._lock:
            for record in values:
                if self._dimensions is None:
                    self._dimensions = len(record.vector)
                if len(record.vector) != self._dimensions:
                    raise ValueError(
                        f"Record {record.id!r} has {len(record.vector)} dimensions; "
                        f"expected {self._dimensions}"
                    )
            self._records.update({record.id: record for record in values})

    async def delete(
        self, ids: Sequence[str] | None = None, *, filters: Mapping[str, Any] | None = None
    ) -> int:
        selected = set(ids) if ids is not None else None
        async with self._lock:
            targets = [
                key
                for key, record in self._records.items()
                if (selected is None or key in selected) and metadata_matches(record.metadata, filters)
            ]
            for key in targets:
                del self._records[key]
        return len(targets)

    async def query(
        self,
        vector: Sequence[float],
        *,
        limit: int = 10,
        filters: Mapping[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        if limit < 0:
            raise ValueError("limit cannot be negative")
        async with self._lock:
            records = list(self._records.values())
            dimensions = self._dimensions
        if dimensions is not None and len(vector) != dimensions:
            raise ValueError(f"Query has {len(vector)} dimensions; expected {dimensions}")
        matches = [
            VectorSearchResult(record=record, score=_cosine(vector, record.vector))
            for record in records
            if metadata_matches(record.metadata, filters)
        ]
        matches.sort(key=lambda item: (-item.score, item.id))
        return matches[:limit]

    async def scan(
        self, *, filters: Mapping[str, Any] | None = None
    ) -> list[VectorRecord]:
        async with self._lock:
            records = list(self._records.values())
        return [record for record in records if metadata_matches(record.metadata, filters)]

    async def get(self, ids: Sequence[str]) -> list[VectorRecord]:
        async with self._lock:
            return [self._records[item] for item in ids if item in self._records]


class QdrantVectorStore:
    """Qdrant adapter; imports and connection setup are deferred until use."""

    def __init__(
        self,
        collection: str,
        *,
        url: str | None = None,
        api_key: str | None = None,
        client: object | None = None,
    ) -> None:
        if not collection:
            raise IndexingConfigurationError("Qdrant collection name is required")
        if client is None and not url:
            raise IndexingConfigurationError("Qdrant URL is required when no client is supplied")
        self.collection = collection
        self.url = url
        self.api_key = api_key
        self._client = client

    def _modules(self) -> tuple[object, object]:
        try:
            from qdrant_client import AsyncQdrantClient, models
        except ImportError as exc:
            raise IndexingConfigurationError(
                "Qdrant support requires the optional 'qdrant-client' package"
            ) from exc
        return AsyncQdrantClient, models

    def _get_client(self) -> object:
        if self._client is None:
            client_type, _ = self._modules()
            self._client = client_type(url=self.url, api_key=self.api_key)  # type: ignore[operator]
        return self._client

    def _filter(self, filters: Mapping[str, Any] | None) -> object | None:
        if not filters:
            return None
        _, models = self._modules()
        conditions = []
        for key, value in filters.items():
            if key in {"acl", "allowed_principals"}:
                conditions.append(
                    models.FieldCondition(key=f"metadata.{key}", match=models.MatchAny(any=list(_as_sequence(value))))  # type: ignore[attr-defined]
                )
            else:
                conditions.append(
                    models.FieldCondition(key=f"metadata.{key}", match=models.MatchValue(value=value))  # type: ignore[attr-defined]
                )
        return models.Filter(must=conditions)  # type: ignore[attr-defined]

    async def create(self, dimensions: int, *, recreate: bool = False) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        client = self._get_client()
        _, models = self._modules()
        if recreate:
            with suppress(Exception):
                await client.delete_collection(self.collection)  # type: ignore[attr-defined]
        exists = await client.collection_exists(self.collection)  # type: ignore[attr-defined]
        if not exists:
            await client.create_collection(  # type: ignore[attr-defined]
                collection_name=self.collection,
                vectors_config={
                    "dense": models.VectorParams(  # type: ignore[attr-defined]
                        size=dimensions, distance=models.Distance.COSINE  # type: ignore[attr-defined]
                    )
                },
                sparse_vectors_config={
                    "sparse": models.SparseVectorParams()  # type: ignore[attr-defined]
                },
            )

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        client = self._get_client()
        _, models = self._modules()
        points = []
        for record in map(coerce_vector_record, records):
            vectors: dict[str, Any] = {"dense": record.vector}
            if record.sparse_vector is not None:
                vectors["sparse"] = models.SparseVector(  # type: ignore[attr-defined]
                    indices=record.sparse_vector.indices,
                    values=record.sparse_vector.values,
                )
            points.append(
                models.PointStruct(  # type: ignore[attr-defined]
                    id=_qdrant_id(record.id),
                    vector=vectors,
                    payload={
                        "_record_id": record.id,
                        "text": record.text,
                        "metadata": record.metadata,
                    },
                )
            )
        if points:
            await client.upsert(collection_name=self.collection, points=points, wait=True)  # type: ignore[attr-defined]

    async def delete(
        self, ids: Sequence[str] | None = None, *, filters: Mapping[str, Any] | None = None
    ) -> int:
        if ids is None and not filters:
            raise ValueError("delete requires ids or filters")
        client = self._get_client()
        _, models = self._modules()
        selector = (
            models.PointIdsList(points=[_qdrant_id(item) for item in ids])  # type: ignore[attr-defined]
            if ids is not None
            else models.FilterSelector(filter=self._filter(filters))  # type: ignore[attr-defined]
        )
        await client.delete(collection_name=self.collection, points_selector=selector, wait=True)  # type: ignore[attr-defined]
        return len(ids) if ids is not None else 0

    async def query(
        self,
        vector: Sequence[float],
        *,
        limit: int = 10,
        filters: Mapping[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        client = self._get_client()
        response = await client.query_points(  # type: ignore[attr-defined]
            collection_name=self.collection,
            query=list(vector),
            using="dense",
            query_filter=self._filter(filters),
            limit=limit,
            with_payload=True,
        )
        points = getattr(response, "points", response)
        results = []
        for point in points:
            payload = dict(point.payload or {})
            record = VectorRecord(
                id=str(payload.get("_record_id", point.id)),
                vector=[],
                text=str(payload.get("text", "")),
                metadata=dict(payload.get("metadata", {})),
            )
            results.append(VectorSearchResult(record, float(point.score)))
        return results

    async def sparse_query(
        self,
        vector: SparseEmbedding,
        *,
        limit: int = 10,
        filters: Mapping[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        client = self._get_client()
        _, models = self._modules()
        response = await client.query_points(  # type: ignore[attr-defined]
            collection_name=self.collection,
            query=models.SparseVector(  # type: ignore[attr-defined]
                indices=vector.indices,
                values=vector.values,
            ),
            using="sparse",
            query_filter=self._filter(filters),
            limit=limit,
            with_payload=True,
        )
        points = getattr(response, "points", response)
        results = []
        for point in points:
            payload = dict(point.payload or {})
            results.append(
                VectorSearchResult(
                    VectorRecord(
                        id=str(payload.get("_record_id", point.id)),
                        vector=[],
                        text=str(payload.get("text", "")),
                        metadata=dict(payload.get("metadata", {})),
                    ),
                    float(point.score),
                )
            )
        return results


class PgVectorStore:
    """Minimal pgvector adapter backed by psycopg 3."""

    def __init__(
        self,
        dsn: str,
        *,
        table: str = "rag_vectors",
    ) -> None:
        if not dsn:
            raise IndexingConfigurationError("PostgreSQL DSN is required")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            raise IndexingConfigurationError("Invalid pgvector table name")
        self.dsn = dsn
        self.table = table
        self._dimensions: int | None = None

    def _connect(self) -> object:
        try:
            import psycopg
        except ImportError as exc:
            raise IndexingConfigurationError(
                "PgVector support requires the optional 'psycopg[binary]' package"
            ) from exc
        return psycopg.AsyncConnection.connect(self.dsn)

    async def create(self, dimensions: int, *, recreate: bool = False) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        connection = await self._connect()  # type: ignore[misc]
        async with connection, connection.cursor() as cursor:
            await cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            if recreate:
                await cursor.execute(f'DROP TABLE IF EXISTS "{self.table}"')
            await cursor.execute(
                f'CREATE TABLE IF NOT EXISTS "{self.table}" ('
                f"id TEXT PRIMARY KEY, embedding VECTOR({dimensions}) NOT NULL, "
                "text TEXT NOT NULL, metadata JSONB NOT NULL DEFAULT '{}'::jsonb, "
                "search_vector TSVECTOR GENERATED ALWAYS AS "
                "(to_tsvector('simple', text)) STORED)"
            )
            await cursor.execute(
                f'CREATE INDEX IF NOT EXISTS "{self.table}_fts_idx" '
                f'ON "{self.table}" USING GIN (search_vector)'
            )
            await cursor.execute(
                f'CREATE INDEX IF NOT EXISTS "{self.table}_vector_idx" '
                f'ON "{self.table}" USING hnsw (embedding vector_cosine_ops)'
            )
        self._dimensions = dimensions

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        import json

        connection = await self._connect()  # type: ignore[misc]
        async with connection, connection.cursor() as cursor:
            for record in map(coerce_vector_record, records):
                vector = "[" + ",".join(str(item) for item in record.vector) + "]"
                await cursor.execute(
                    f'INSERT INTO "{self.table}" (id, embedding, text, metadata) '
                    "VALUES (%s, %s::vector, %s, %s::jsonb) "
                    "ON CONFLICT (id) DO UPDATE SET embedding=EXCLUDED.embedding, "
                    "text=EXCLUDED.text, metadata=EXCLUDED.metadata",
                    (record.id, vector, record.text, json.dumps(record.metadata)),
                )

    async def delete(
        self, ids: Sequence[str] | None = None, *, filters: Mapping[str, Any] | None = None
    ) -> int:
        if ids is None and not filters:
            raise ValueError("delete requires ids or filters")
        clauses, params = self._sql_filters(filters)
        if ids is not None:
            clauses.insert(0, "id = ANY(%s)")
            params.insert(0, list(ids))
        where = " AND ".join(clauses)
        connection = await self._connect()  # type: ignore[misc]
        async with connection, connection.cursor() as cursor:
            await cursor.execute(f'DELETE FROM "{self.table}" WHERE {where}', params)
            return max(cursor.rowcount, 0)

    def _sql_filters(
        self, filters: Mapping[str, Any] | None
    ) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for key, value in (filters or {}).items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError(f"Invalid metadata filter key: {key!r}")
            if key in {"acl", "allowed_principals"}:
                clauses.append(f"(metadata -> '{key}') ?| %s")
                params.append(list(_as_sequence(value)))
            else:
                clauses.append(f"metadata ->> '{key}' = %s")
                params.append(str(value))
        return clauses, params

    async def query(
        self,
        vector: Sequence[float],
        *,
        limit: int = 10,
        filters: Mapping[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        clauses, filter_params = self._sql_filters(filters)
        query_vector = "[" + ",".join(str(float(item)) for item in vector) + "]"
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params = [query_vector, *filter_params, query_vector, limit]
        connection = await self._connect()  # type: ignore[misc]
        async with connection, connection.cursor() as cursor:
            await cursor.execute(
                f'SELECT id, text, metadata, 1 - (embedding <=> %s::vector) AS score '
                f'FROM "{self.table}"{where} ORDER BY embedding <=> %s::vector LIMIT %s',
                params,
            )
            rows = await cursor.fetchall()
        return [
            VectorSearchResult(
                VectorRecord(str(row[0]), [], str(row[1]), dict(row[2] or {})),
                float(row[3]),
            )
            for row in rows
        ]

    async def lexical_query(
        self,
        query: str,
        *,
        limit: int = 10,
        filters: Mapping[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        clauses, filter_params = self._sql_filters(filters)
        clauses.insert(0, "search_vector @@ websearch_to_tsquery('simple', %s)")
        where = " AND ".join(clauses)
        params = [query, query, *filter_params, limit]
        connection = await self._connect()  # type: ignore[misc]
        async with connection, connection.cursor() as cursor:
            await cursor.execute(
                f'SELECT id, text, metadata, '
                "ts_rank_cd(search_vector, websearch_to_tsquery('simple', %s)) AS score "
                f'FROM "{self.table}" WHERE {where} ORDER BY score DESC LIMIT %s',
                params,
            )
            rows = await cursor.fetchall()
        return [
            VectorSearchResult(
                VectorRecord(str(row[0]), [], str(row[1]), dict(row[2] or {})),
                float(row[3]),
            )
            for row in rows
        ]
