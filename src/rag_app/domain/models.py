"""Provider-independent domain models for ingestion and RAG."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

Metadata = dict[str, JsonValue]
Embedding = list[float]
NonEmptyText = Annotated[str, Field(min_length=1)]


def utc_now() -> datetime:
    return datetime.now(UTC)


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)


class IngestionStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class DocumentRecord(DomainModel):
    id: NonEmptyText
    content: NonEmptyText
    source: NonEmptyText
    content_hash: NonEmptyText
    metadata: Metadata = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value


class ChunkRecord(DomainModel):
    id: NonEmptyText
    document_id: NonEmptyText
    content: NonEmptyText
    index: int = Field(ge=0)
    content_hash: NonEmptyText
    token_count: int | None = Field(default=None, ge=0)
    embedding: Embedding | None = None
    metadata: Metadata = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value

    @field_validator("embedding")
    @classmethod
    def require_nonempty_embedding(cls, value: Embedding | None) -> Embedding | None:
        if value == []:
            raise ValueError("embedding must not be empty")
        return value


class SearchResult(DomainModel):
    chunk: ChunkRecord
    score: float
    rank: int = Field(ge=1)


class Citation(DomainModel):
    chunk_id: NonEmptyText
    document_id: NonEmptyText
    source: NonEmptyText
    excerpt: NonEmptyText
    title: str | None = None
    page: int | None = Field(default=None, ge=1)
    url: str | None = None
    score: float | None = None


class RAGResponse(DomainModel):
    query: NonEmptyText
    answer: NonEmptyText
    citations: list[Citation] = Field(default_factory=list)
    model: str | None = None
    latency_ms: float | None = Field(default=None, ge=0)
    metadata: Metadata = Field(default_factory=dict)


class IngestionRecord(DomainModel):
    document_id: NonEmptyText
    status: IngestionStatus = IngestionStatus.PENDING
    chunks_processed: int = Field(default=0, ge=0)
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def require_optional_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must be timezone-aware")
        return value
