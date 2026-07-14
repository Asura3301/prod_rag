"""Typed ingestion records independent of the evolving domain package."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from langchain_core.documents import Document


class IngestionStatus(StrEnum):
    INGESTED = "ingested"
    UNCHANGED = "unchanged"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IngestedDocument:
    document_id: str
    content_id: str
    source_id: str
    content_hash: str
    version: int
    chunks: tuple[Document, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IngestionItemResult:
    source_id: str
    status: IngestionStatus
    document_id: str | None = None
    content_id: str | None = None
    content_hash: str | None = None
    version: int | None = None
    chunk_count: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class IngestionResult:
    items: tuple[IngestionItemResult, ...]

    @property
    def ingested_count(self) -> int:
        return sum(item.status is IngestionStatus.INGESTED for item in self.items)

    @property
    def unchanged_count(self) -> int:
        return sum(item.status is IngestionStatus.UNCHANGED for item in self.items)

    @property
    def failed_count(self) -> int:
        return sum(item.status is IngestionStatus.FAILED for item in self.items)

    @property
    def chunk_count(self) -> int:
        return sum(item.chunk_count for item in self.items)


__all__ = [
    "IngestedDocument", "IngestionItemResult", "IngestionResult", "IngestionStatus",
]
