"""Hybrid retrieval, fusion, expansion, budgeting, and diagnostics."""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from rag_app.indexing.embeddings import EmbeddingProvider, SparseEmbeddingProvider
from rag_app.indexing.vector_stores import (
    VectorRecord,
    VectorSearchResult,
    VectorStore,
    metadata_matches,
)

from .rerankers import IdentityReranker, Reranker


class RetrievalProfile(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    QUALITY = "quality"


@dataclass(frozen=True, slots=True)
class ProfileSettings:
    candidate_multiplier: int
    use_lexical: bool
    use_reranker: bool
    use_expansion: bool


PROFILE_SETTINGS = {
    RetrievalProfile.FAST: ProfileSettings(2, False, False, False),
    RetrievalProfile.BALANCED: ProfileSettings(4, True, False, True),
    RetrievalProfile.QUALITY: ProfileSettings(8, True, True, True),
}


@dataclass(slots=True)
class RetrievalRequest:
    query: str
    top_k: int = 5
    profile: RetrievalProfile | str = RetrievalProfile.BALANCED
    filters: dict[str, Any] = field(default_factory=dict)
    tenant_id: str | None = None
    principals: tuple[str, ...] = ()
    max_tokens: int | None = None
    max_characters: int | None = None

    def effective_filters(self) -> dict[str, Any]:
        result = dict(self.filters)
        if self.tenant_id is not None:
            result["tenant_id"] = self.tenant_id
        if self.principals:
            result["acl"] = self.principals
        return result


@dataclass(slots=True)
class RetrievalCandidate:
    id: str
    text: str
    metadata: dict[str, Any]
    score: float = 0.0
    dense_score: float | None = None
    lexical_score: float | None = None
    rerank_score: float | None = None
    sources: tuple[str, ...] = ()

    @classmethod
    def from_vector_result(
        cls, result: VectorSearchResult, source: str = "dense"
    ) -> RetrievalCandidate:
        return cls(
            id=result.id,
            text=result.text,
            metadata=dict(result.metadata),
            score=result.score,
            dense_score=result.score if source == "dense" else None,
            lexical_score=result.score if source == "lexical" else None,
            sources=(source,),
        )


@dataclass(slots=True)
class RetrievalDiagnostics:
    profile: str
    dense_candidates: int = 0
    lexical_candidates: int = 0
    fused_candidates: int = 0
    expanded_candidates: int = 0
    deduplicated: int = 0
    returned: int = 0
    budget_truncated: bool = False
    estimated_tokens: int = 0
    characters: int = 0
    timings_ms: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class RetrievalResult:
    candidates: list[RetrievalCandidate]
    context: str
    diagnostics: RetrievalDiagnostics


@runtime_checkable
class LexicalRetriever(Protocol):
    async def search(
        self,
        query: str,
        *,
        limit: int,
        filters: Mapping[str, Any] | None = None,
    ) -> list[RetrievalCandidate]: ...


@runtime_checkable
class ExpansionHook(Protocol):
    async def expand(
        self,
        candidates: Sequence[RetrievalCandidate],
        *,
        store: VectorStore,
        filters: Mapping[str, Any],
    ) -> list[RetrievalCandidate]: ...


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold(), flags=re.UNICODE)


class InMemoryLexicalRetriever:
    """Small BM25 implementation over any store exposing async ``scan``."""

    def __init__(self, store: object, *, k1: float = 1.5, b: float = 0.75) -> None:
        self.store = store
        self.k1 = k1
        self.b = b

    async def search(
        self,
        query: str,
        *,
        limit: int,
        filters: Mapping[str, Any] | None = None,
    ) -> list[RetrievalCandidate]:
        scan = getattr(self.store, "scan", None)
        if scan is None:
            return []
        records: list[VectorRecord] = await scan(filters=filters)
        query_terms = _tokens(query)
        if not records or not query_terms:
            return []
        documents = [_tokens(record.text) for record in records]
        average_length = sum(map(len, documents)) / len(documents) or 1.0
        frequencies = Counter(
            term for terms in documents for term in set(terms)
        )
        scored: list[RetrievalCandidate] = []
        for record, terms in zip(records, documents, strict=True):
            term_counts = Counter(terms)
            score = 0.0
            for term in query_terms:
                frequency = term_counts[term]
                if not frequency:
                    continue
                document_frequency = frequencies[term]
                inverse_frequency = math.log(
                    1 + (len(documents) - document_frequency + 0.5) / (document_frequency + 0.5)
                )
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * len(terms) / average_length
                )
                score += inverse_frequency * frequency * (self.k1 + 1) / denominator
            if score > 0:
                scored.append(
                    RetrievalCandidate(
                        id=record.id,
                        text=record.text,
                        metadata=dict(record.metadata),
                        score=score,
                        lexical_score=score,
                        sources=("lexical",),
                    )
                )
        scored.sort(key=lambda item: (-item.score, item.id))
        return scored[:limit]


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[RetrievalCandidate]],
    *,
    rank_constant: int = 60,
) -> list[RetrievalCandidate]:
    """Fuse ranked lists with standard RRF: ``sum(1 / (k + rank))``."""
    if rank_constant < 0:
        raise ValueError("rank_constant cannot be negative")
    combined: dict[str, RetrievalCandidate] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            if item.id not in combined:
                combined[item.id] = replace(item, score=0.0)
            target = combined[item.id]
            target.score += 1.0 / (rank_constant + rank)
            target.sources = tuple(dict.fromkeys((*target.sources, *item.sources)))
            if item.dense_score is not None:
                target.dense_score = item.dense_score
            if item.lexical_score is not None:
                target.lexical_score = item.lexical_score
    return sorted(combined.values(), key=lambda item: (-item.score, item.id))


class AdjacentExpansionHook:
    def __init__(self, window: int = 1) -> None:
        self.window = window

    async def expand(
        self,
        candidates: Sequence[RetrievalCandidate],
        *,
        store: VectorStore,
        filters: Mapping[str, Any],
    ) -> list[RetrievalCandidate]:
        scan = getattr(store, "scan", None)
        if scan is None or self.window <= 0:
            return []
        records: list[VectorRecord] = await scan(filters=filters)
        expanded = []
        for candidate in candidates:
            document_id = candidate.metadata.get("document_id")
            index = candidate.metadata.get("chunk_index")
            if document_id is None or not isinstance(index, int):
                continue
            for record in records:
                other_index = record.metadata.get("chunk_index")
                if (
                    record.metadata.get("document_id") == document_id
                    and isinstance(other_index, int)
                    and 0 < abs(other_index - index) <= self.window
                ):
                    expanded.append(
                        RetrievalCandidate(
                            record.id,
                            record.text,
                            dict(record.metadata),
                            score=candidate.score,
                            sources=("adjacent",),
                        )
                    )
        return expanded


class ParentExpansionHook:
    async def expand(
        self,
        candidates: Sequence[RetrievalCandidate],
        *,
        store: VectorStore,
        filters: Mapping[str, Any],
    ) -> list[RetrievalCandidate]:
        scan = getattr(store, "scan", None)
        if scan is None:
            return []
        parent_ids = {
            str(item.metadata["parent_id"])
            for item in candidates
            if item.metadata.get("parent_id") is not None
        }
        if not parent_ids:
            return []
        records: list[VectorRecord] = await scan(filters=filters)
        return [
            RetrievalCandidate(
                record.id,
                record.text,
                dict(record.metadata),
                sources=("parent",),
            )
            for record in records
            if record.id in parent_ids
        ]


def deduplicate_candidates(
    candidates: Sequence[RetrievalCandidate],
) -> tuple[list[RetrievalCandidate], int]:
    seen_ids: set[str] = set()
    seen_content: set[str] = set()
    result = []
    for candidate in candidates:
        content_hash = hashlib.sha256(candidate.text.strip().encode("utf-8")).hexdigest()
        if candidate.id in seen_ids or content_hash in seen_content:
            continue
        seen_ids.add(candidate.id)
        seen_content.add(content_hash)
        result.append(candidate)
    return result, len(candidates) - len(result)


TokenCounter = Callable[[str], int]


def budget_context(
    candidates: Sequence[RetrievalCandidate],
    *,
    max_tokens: int | None,
    max_characters: int | None,
    token_counter: TokenCounter | None = None,
) -> tuple[list[RetrievalCandidate], str, int, bool]:
    counter = token_counter or (lambda text: max(1, math.ceil(len(text) / 4)))
    accepted: list[RetrievalCandidate] = []
    texts: list[str] = []
    token_count = 0
    character_count = 0
    truncated = False
    for candidate in candidates:
        separator_length = 2 if texts else 0
        candidate_tokens = counter(candidate.text)
        if max_tokens is not None and token_count + candidate_tokens > max_tokens:
            truncated = True
            continue
        if (
            max_characters is not None
            and character_count + separator_length + len(candidate.text) > max_characters
        ):
            truncated = True
            continue
        accepted.append(candidate)
        texts.append(candidate.text)
        token_count += candidate_tokens
        character_count += separator_length + len(candidate.text)
    return accepted, "\n\n".join(texts), token_count, truncated


class HybridRetriever:
    def __init__(
        self,
        embeddings: EmbeddingProvider,
        store: VectorStore,
        *,
        sparse_embeddings: SparseEmbeddingProvider | None = None,
        lexical: LexicalRetriever | None = None,
        reranker: Reranker | None = None,
        expansion_hooks: Sequence[ExpansionHook] = (),
        token_counter: TokenCounter | None = None,
        rrf_rank_constant: int = 60,
    ) -> None:
        self.embeddings = embeddings
        self.sparse_embeddings = sparse_embeddings
        self.store = store
        self.lexical = lexical or InMemoryLexicalRetriever(store)
        self.reranker = reranker or IdentityReranker()
        self.expansion_hooks = tuple(expansion_hooks)
        self.token_counter = token_counter
        self.rrf_rank_constant = rrf_rank_constant

    async def _lexical_search(
        self,
        query: str,
        *,
        limit: int,
        filters: Mapping[str, Any],
    ) -> list[RetrievalCandidate]:
        sparse_query = getattr(self.store, "sparse_query", None)
        if self.sparse_embeddings is not None and callable(sparse_query):
            sparse_vector = await self.sparse_embeddings.embed_query(query)
            results = await sparse_query(sparse_vector, limit=limit, filters=filters)
            return [
                RetrievalCandidate.from_vector_result(item, source="lexical")
                for item in results
            ]
        lexical_query = getattr(self.store, "lexical_query", None)
        if callable(lexical_query):
            results = await lexical_query(query, limit=limit, filters=filters)
            return [
                RetrievalCandidate.from_vector_result(item, source="lexical")
                for item in results
            ]
        return await self.lexical.search(query, limit=limit, filters=filters)

    async def retrieve(self, request: RetrievalRequest | str, **kwargs: Any) -> RetrievalResult:
        if isinstance(request, str):
            request = RetrievalRequest(query=request, **kwargs)
        if not request.query.strip():
            raise ValueError("query cannot be empty")
        if request.top_k <= 0:
            raise ValueError("top_k must be positive")
        profile = RetrievalProfile(request.profile)
        settings = PROFILE_SETTINGS[profile]
        diagnostics = RetrievalDiagnostics(profile=profile.value)
        filters = request.effective_filters()
        candidate_limit = request.top_k * settings.candidate_multiplier
        started = time.perf_counter()

        query_vector = await self.embeddings.embed_query(request.query)
        dense_task = asyncio.create_task(
            self.store.query(query_vector, limit=candidate_limit, filters=filters)
        )
        lexical_task = (
            asyncio.create_task(
                self._lexical_search(
                    request.query,
                    limit=candidate_limit,
                    filters=filters,
                )
            )
            if settings.use_lexical
            else None
        )
        dense_results = await dense_task
        dense = [
            RetrievalCandidate.from_vector_result(item)
            for item in dense_results
            if metadata_matches(item.metadata, filters)
        ]
        lexical = await lexical_task if lexical_task is not None else []
        lexical = [item for item in lexical if metadata_matches(item.metadata, filters)]
        diagnostics.timings_ms["candidate_search"] = (time.perf_counter() - started) * 1000
        diagnostics.dense_candidates = len(dense)
        diagnostics.lexical_candidates = len(lexical)

        fused = reciprocal_rank_fusion(
            [dense, lexical] if settings.use_lexical else [dense],
            rank_constant=self.rrf_rank_constant,
        )
        diagnostics.fused_candidates = len(fused)
        if settings.use_reranker:
            rerank_started = time.perf_counter()
            fused = await self.reranker.rerank(
                request.query, fused, top_k=candidate_limit
            )
            diagnostics.timings_ms["rerank"] = (time.perf_counter() - rerank_started) * 1000

        expanded: list[RetrievalCandidate] = []
        if settings.use_expansion:
            for hook in self.expansion_hooks:
                values = await hook.expand(fused[: request.top_k], store=self.store, filters=filters)
                expanded.extend(
                    value for value in values if metadata_matches(value.metadata, filters)
                )
        diagnostics.expanded_candidates = len(expanded)
        unique, removed = deduplicate_candidates([*fused, *expanded])
        diagnostics.deduplicated = removed

        budgeted, context, estimated_tokens, truncated = budget_context(
            unique,
            max_tokens=request.max_tokens,
            max_characters=request.max_characters,
            token_counter=self.token_counter,
        )
        budgeted = budgeted[: request.top_k]
        context = "\n\n".join(item.text for item in budgeted)
        diagnostics.returned = len(budgeted)
        diagnostics.budget_truncated = truncated
        diagnostics.estimated_tokens = sum(
            (self.token_counter or (lambda text: max(1, math.ceil(len(text) / 4))))(item.text)
            for item in budgeted
        )
        diagnostics.characters = len(context)
        diagnostics.timings_ms["total"] = (time.perf_counter() - started) * 1000
        return RetrievalResult(budgeted, context, diagnostics)
