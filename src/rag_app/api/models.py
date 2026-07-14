"""Public API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class IngestionRequest(BaseModel):
    source: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class DocumentCreateRequest(BaseModel):
    source: str = Field(min_length=1)
    text: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobResponse(BaseModel):
    id: str
    kind: str
    state: str
    attempts: int
    max_attempts: int
    error: str | None = None
    created_at: str
    updated_at: str


class DocumentResponse(BaseModel):
    id: str
    source: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class QueryRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=100)
    filters: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    document_id: str
    excerpt: str
    score: float


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    latency_ms: float
