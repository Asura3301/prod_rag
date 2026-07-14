"""Injectable RAG service boundary and safe local implementation."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from .models import Citation, DocumentResponse, QueryResponse


class RAGService(Protocol):
    async def query(self, text: str, top_k: int, filters: dict[str, Any]) -> QueryResponse: ...
    async def add_document(
        self, source: str, text: str, metadata: dict[str, Any] | None = None
    ) -> DocumentResponse: ...
    async def list_documents(self) -> list[DocumentResponse]: ...
    async def get_document(self, document_id: str) -> DocumentResponse | None: ...
    async def delete_document(self, document_id: str) -> bool: ...
    async def ready(self) -> bool: ...


@dataclass
class ServiceStats:
    queries: int = 0
    documents_deleted: int = 0


class InMemoryRAGService:
    """Deterministic lexical retrieval for development and dependency-free tests."""

    def __init__(self, documents: list[DocumentResponse] | None = None) -> None:
        self._documents = {document.id: document for document in documents or []}
        self.stats = ServiceStats()

    async def add_document(
        self, source: str, text: str, metadata: dict[str, Any] | None = None
    ) -> DocumentResponse:
        document = DocumentResponse(
            id=str(uuid4()),
            source=source,
            text=text,
            metadata=metadata or {},
            created_at=datetime.now(UTC),
        )
        self._documents[document.id] = document
        return document

    async def query(self, text: str, top_k: int, filters: dict[str, Any]) -> QueryResponse:
        started = time.perf_counter()
        self.stats.queries += 1
        terms = set(text.casefold().split())
        matches: list[tuple[float, DocumentResponse]] = []
        for document in self._documents.values():
            if any(document.metadata.get(key) != value for key, value in filters.items()):
                continue
            words = set(document.text.casefold().split())
            score = len(terms & words) / max(len(terms), 1)
            if score:
                matches.append((score, document))
        matches.sort(key=lambda item: item[0], reverse=True)
        citations = [
            Citation(document_id=document.id, excerpt=document.text[:280], score=score)
            for score, document in matches[:top_k]
        ]
        answer = (
            " ".join(citation.excerpt for citation in citations)
            if citations
            else "No relevant information was found in the indexed documents."
        )
        return QueryResponse(
            answer=answer,
            citations=citations,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    async def list_documents(self) -> list[DocumentResponse]:
        return list(self._documents.values())

    async def get_document(self, document_id: str) -> DocumentResponse | None:
        return self._documents.get(document_id)

    async def delete_document(self, document_id: str) -> bool:
        removed = self._documents.pop(document_id, None) is not None
        self.stats.documents_deleted += int(removed)
        return removed

    async def ready(self) -> bool:
        return True
