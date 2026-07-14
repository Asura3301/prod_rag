"""Orchestrated, idempotent ingestion pipeline."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from pathlib import Path

from langchain_core.documents import Document

from .chunking import ChunkingStrategy, StructureAwareChunker
from .document_loaders import route_loader
from .models import (
    IngestedDocument,
    IngestionItemResult,
    IngestionResult,
    IngestionStatus,
)
from .registry import InMemoryDocumentRegistry
from .sources import DocumentSource, SourceItem, as_source

logger = logging.getLogger(__name__)
INGESTION_SCHEMA_VERSION = 1
Loader = Callable[[str | Path], list[Document]]


class IngestionPipeline:
    """Load, normalize, chunk, identify, and register source documents."""

    def __init__(
        self,
        *,
        registry: InMemoryDocumentRegistry | None = None,
        chunker: ChunkingStrategy | None = None,
        loader: Loader = route_loader,
        continue_on_error: bool = True,
    ) -> None:
        self.registry = registry if registry is not None else InMemoryDocumentRegistry()
        self.chunker = chunker if chunker is not None else StructureAwareChunker()
        self.loader = loader
        self.continue_on_error = continue_on_error

    def ingest(self, source: DocumentSource | str | Path) -> IngestionResult:
        results: list[IngestionItemResult] = []
        for item in as_source(source).items():
            try:
                results.append(self._ingest_item(item))
            except Exception as exc:
                logger.exception("Failed to ingest %s", item.source_id)
                if not self.continue_on_error:
                    raise
                results.append(
                    IngestionItemResult(
                        source_id=item.source_id,
                        status=IngestionStatus.FAILED,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
        return IngestionResult(items=tuple(results))

    def _ingest_item(self, item: SourceItem) -> IngestionItemResult:
        content_hash = hash_file(item.path)
        current = self.registry.get(item.source_id)
        if current is not None and current.content_hash == content_hash:
            return IngestionItemResult(
                source_id=item.source_id,
                status=IngestionStatus.UNCHANGED,
                document_id=current.document_id,
                content_id=current.content_id,
                content_hash=current.content_hash,
                version=current.version,
                chunk_count=len(current.chunks),
            )

        loaded = self.loader(item.path)
        if hash_file(item.path) != content_hash:
            raise RuntimeError(f"Source changed while being loaded: {item.source_id}")
        if not loaded:
            raise ValueError(f"Loader returned no documents for {item.source_id}")
        document_id = stable_id("document", item.source_id)
        content_id = stable_id("content", content_hash)
        version = 1 if current is None else current.version + 1
        chunks = self._chunk_documents(
            loaded=loaded,
            item=item,
            document_id=document_id,
            content_id=content_id,
            content_hash=content_hash,
            version=version,
        )
        if not chunks:
            raise ValueError(f"No non-empty chunks produced for {item.source_id}")
        record = IngestedDocument(
            document_id=document_id,
            content_id=content_id,
            source_id=item.source_id,
            content_hash=content_hash,
            version=version,
            chunks=tuple(chunks),
            metadata={
                "source": item.source_id,
                "content_id": content_id,
                "content_hash": content_hash,
                "schema_version": INGESTION_SCHEMA_VERSION,
                "chunking_version": self.chunker.version,
            },
        )
        effective, changed = self.registry.register(record)
        status = IngestionStatus.INGESTED if changed else IngestionStatus.UNCHANGED
        return IngestionItemResult(
            source_id=item.source_id,
            status=status,
            document_id=effective.document_id,
            content_id=effective.content_id,
            content_hash=effective.content_hash,
            version=effective.version,
            chunk_count=len(effective.chunks),
        )

    def _chunk_documents(
        self,
        *,
        loaded: list[Document],
        item: SourceItem,
        document_id: str,
        content_id: str,
        content_hash: str,
        version: int,
    ) -> list[Document]:
        chunks: list[Document] = []
        for part_index, document in enumerate(loaded):
            for text in self.chunker.split(document):
                chunk_index = len(chunks)
                chunk_id = stable_id(
                    "chunk",
                    document_id,
                    content_hash,
                    str(chunk_index),
                    text,
                )
                metadata = dict(document.metadata or {})
                metadata.update(
                    {
                        "source": item.source_id,
                        "document_id": document_id,
                        "content_id": content_id,
                        "chunk_id": chunk_id,
                        "chunk_index": chunk_index,
                        "source_part_index": part_index,
                        "content_hash": content_hash,
                        "document_version": version,
                        "schema_version": INGESTION_SCHEMA_VERSION,
                        "chunking_version": self.chunker.version,
                    }
                )
                chunks.append(Document(page_content=text, metadata=metadata))
        return chunks


def hash_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def stable_id(namespace: str, *parts: str) -> str:
    payload = "\x1f".join((namespace, *parts)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = ["INGESTION_SCHEMA_VERSION", "IngestionPipeline", "hash_file", "stable_id"]
