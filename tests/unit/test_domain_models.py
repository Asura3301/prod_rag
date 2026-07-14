from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from rag_app.core.identifiers import chunk_id, content_hash, deterministic_id, document_id
from rag_app.domain import (
    ChunkRecord,
    Citation,
    DocumentRecord,
    IngestionRecord,
    IngestionStatus,
    RAGResponse,
    SearchResult,
)


def make_chunk() -> ChunkRecord:
    text = "A grounded passage."
    doc_id = document_id("manual.pdf", "Full document")
    return ChunkRecord(
        id=chunk_id(doc_id, 0, text),
        document_id=doc_id,
        content=text,
        index=0,
        content_hash=content_hash(text),
        metadata={"page": 1, "section": "Introduction"},
    )


def test_identifiers_are_deterministic_and_unambiguous() -> None:
    assert deterministic_id("ab", "c") == deterministic_id("ab", "c")
    assert deterministic_id("ab", "c") != deterministic_id("a", "bc")
    assert deterministic_id("value", namespace="a") != deterministic_id(
        "value", namespace="b"
    )
    assert content_hash("hello") == content_hash(b"hello")


def test_document_and_chunk_models_are_validated_and_immutable() -> None:
    text = "Full document"
    record = DocumentRecord(
        id=document_id("manual.pdf", text),
        content=text,
        source="manual.pdf",
        content_hash=content_hash(text),
        metadata={"language": "en"},
    )
    chunk = make_chunk()

    assert record.created_at.tzinfo is not None
    assert chunk.document_id == record.id
    with pytest.raises(ValidationError):
        chunk.index = 2


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        DocumentRecord(
            id="doc",
            content="content",
            source="source",
            content_hash="hash",
            created_at=datetime(2026, 1, 1),
        )


def test_search_citation_and_response_round_trip() -> None:
    chunk = make_chunk()
    result = SearchResult(chunk=chunk, score=0.91, rank=1)
    citation = Citation(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        source="manual.pdf",
        excerpt=chunk.content,
        page=1,
        score=result.score,
    )
    response = RAGResponse(
        query="What is grounded?",
        answer="The answer is grounded.",
        citations=[citation],
        model="test-model",
    )

    restored = RAGResponse.model_validate_json(response.model_dump_json())
    assert restored == response
    assert restored.citations[0].chunk_id == chunk.id


def test_ingestion_status_serializes_as_string() -> None:
    record = IngestionRecord(
        document_id="doc-1",
        status=IngestionStatus.COMPLETED,
        chunks_processed=3,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )

    assert record.model_dump(mode="json")["status"] == "completed"
