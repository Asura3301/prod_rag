"""Local worker ingestion handler that persists and indexes results."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from rag_app.ingestion import (
    IngestionPipeline,
    IngestionStatus,
    LocalDirectorySource,
    LocalFileSource,
)

from .service import LocalRAGService


class LocalIngestionHandler:
    def __init__(
        self,
        service: LocalRAGService,
        *,
        ingestion_root: str | Path,
        pipeline: IngestionPipeline | None = None,
    ) -> None:
        self.service = service
        self.root = Path(ingestion_root).expanduser().resolve()
        self.pipeline = pipeline or IngestionPipeline()

    async def __call__(self, payload: dict[str, Any]) -> None:
        source_value = payload.get("source")
        if not isinstance(source_value, str) or not source_value:
            raise ValueError("ingestion payload requires a source")
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("ingestion metadata must be an object")

        source_path = Path(source_value).expanduser().resolve(strict=True)
        self._require_inside_root(source_path)
        source = (
            LocalDirectorySource(source_path)
            if source_path.is_dir()
            else LocalFileSource(source_path, allowed_root=self.root)
        )
        result = await asyncio.to_thread(self.pipeline.ingest, source)
        if result.failed_count:
            errors = [item.error for item in result.items if item.error]
            raise RuntimeError("; ".join(errors))

        for item in result.items:
            if item.status not in {IngestionStatus.INGESTED, IngestionStatus.UNCHANGED}:
                continue
            record = self.pipeline.registry.get(item.source_id)
            if record is None:
                raise RuntimeError(f"Registry record missing for {item.source_id}")
            chunks = [
                Document(
                    page_content=chunk.page_content,
                    metadata={**metadata, **chunk.metadata},
                )
                for chunk in record.chunks
            ]
            await self.service.index_ingested_document(
                document_id=record.document_id,
                source=record.source_id,
                text="\n\n".join(chunk.page_content for chunk in record.chunks),
                chunks=chunks,
                content_hash=record.content_hash,
                version=record.version,
                metadata={**metadata, **record.metadata},
            )

    def _require_inside_root(self, path: Path) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"Source must be inside ingestion root: {self.root}") from exc


__all__ = ["LocalIngestionHandler"]
