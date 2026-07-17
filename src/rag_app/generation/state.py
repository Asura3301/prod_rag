"""Typed state and public response models for the RAG graph."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

STATE_VERSION = 1


class Evidence(TypedDict):
    citation_id: str
    chunk_id: str
    document_id: str
    source: str
    page: int | str | None
    section: str | None
    content: str
    score: float | None


class Citation(TypedDict):
    citation_id: str
    chunk_id: str
    document_id: str
    source: str
    page: int | str | None
    section: str | None
    excerpt: str
    score: float | None


class RAGResponse(TypedDict):
    answer: str
    status: Literal["answered", "abstained", "invalid_request"]
    citations: list[Citation]
    query: str
    rewrite_count: int
    state_version: int


class RAGState(TypedDict, total=False):
    """Versioned graph state. Large raw documents are never stored."""

    state_version: int
    request: str
    query: str
    original_query: str
    retrieval_query: str
    rewrite_count: int
    max_rewrites: int
    evidence: list[Evidence]
    context: str
    draft_answer: str
    valid_citation_ids: list[str]
    status: Literal["running", "answered", "abstained", "invalid_request"]
    error: NotRequired[str]
    response: RAGResponse
    metadata: dict[str, Any]
