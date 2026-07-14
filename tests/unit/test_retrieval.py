import asyncio

from rag_app.indexing import (
    HashEmbeddingProvider,
    HashSparseEmbeddingProvider,
    InMemoryVectorStore,
    VectorRecord,
    VectorSearchResult,
)
from rag_app.retrieval import (
    AdjacentExpansionHook,
    HybridRetriever,
    RetrievalCandidate,
    RetrievalRequest,
    budget_context,
    reciprocal_rank_fusion,
)


def run(coroutine):
    return asyncio.run(coroutine)


def test_reciprocal_rank_fusion_uses_standard_formula():
    dense = [
        RetrievalCandidate("a", "A", {}, sources=("dense",)),
        RetrievalCandidate("b", "B", {}, sources=("dense",)),
    ]
    lexical = [
        RetrievalCandidate("b", "B", {}, sources=("lexical",)),
        RetrievalCandidate("c", "C", {}, sources=("lexical",)),
    ]
    fused = reciprocal_rank_fusion([dense, lexical], rank_constant=60)
    assert [item.id for item in fused] == ["b", "a", "c"]
    assert fused[0].score == 1 / 62 + 1 / 61
    assert fused[0].sources == ("dense", "lexical")


def test_hybrid_retrieval_filters_before_fusion_and_reports_diagnostics():
    async def scenario():
        embeddings = HashEmbeddingProvider(dimensions=32)
        store = InMemoryVectorStore()
        texts = ["alpha retrieval guide", "alpha secret", "unrelated material"]
        vectors = await embeddings.embed_documents(texts)
        await store.upsert(
            [
                VectorRecord(
                    "public",
                    vectors[0],
                    texts[0],
                    {"tenant_id": "t1", "acl": ["user"], "document_id": "d1"},
                ),
                VectorRecord(
                    "secret",
                    vectors[1],
                    texts[1],
                    {"tenant_id": "t1", "acl": ["admin"], "document_id": "d2"},
                ),
                VectorRecord(
                    "other",
                    vectors[2],
                    texts[2],
                    {"tenant_id": "t2", "acl": ["user"], "document_id": "d3"},
                ),
            ]
        )
        retriever = HybridRetriever(embeddings, store)
        result = await retriever.retrieve(
            RetrievalRequest(
                "alpha",
                top_k=3,
                tenant_id="t1",
                principals=("user",),
                profile="balanced",
            )
        )
        assert [item.id for item in result.candidates] == ["public"]
        assert result.context == "alpha retrieval guide"
        assert result.diagnostics.dense_candidates == 1
        assert result.diagnostics.lexical_candidates == 1
        assert result.diagnostics.returned == 1

    run(scenario())


def test_quality_profile_reranks_and_adjacent_expands():
    class ReverseReranker:
        async def rerank(self, query, candidates, *, top_k=None):
            del query
            return list(reversed(candidates))[:top_k]

    async def scenario():
        embeddings = HashEmbeddingProvider(dimensions=16)
        store = InMemoryVectorStore()
        texts = ["alpha first", "alpha second", "alpha third"]
        vectors = await embeddings.embed_documents(texts)
        await store.upsert(
            [
                VectorRecord(
                    str(index),
                    vector,
                    text,
                    {"tenant_id": "t", "document_id": "doc", "chunk_index": index},
                )
                for index, (vector, text) in enumerate(zip(vectors, texts, strict=True))
            ]
        )
        retriever = HybridRetriever(
            embeddings,
            store,
            reranker=ReverseReranker(),
            expansion_hooks=[AdjacentExpansionHook(window=1)],
        )
        result = await retriever.retrieve(
            RetrievalRequest("alpha", top_k=3, tenant_id="t", profile="quality")
        )
        assert len(result.candidates) == 3
        assert result.diagnostics.expanded_candidates > 0
        assert "rerank" in result.diagnostics.timings_ms

    run(scenario())


def test_context_budget_skips_oversized_candidates_without_overflow():
    candidates = [
        RetrievalCandidate("long", "x" * 20, {}),
        RetrievalCandidate("short", "okay", {}),
    ]
    accepted, context, tokens, truncated = budget_context(
        candidates,
        max_tokens=2,
        max_characters=10,
        token_counter=lambda text: len(text.split()),
    )
    assert [item.id for item in accepted] == ["short"]
    assert context == "okay"
    assert tokens == 1
    assert truncated


def test_fast_profile_uses_dense_only():
    async def scenario():
        embeddings = HashEmbeddingProvider(dimensions=8)
        store = InMemoryVectorStore()
        vector = await embeddings.embed_query("fast")
        await store.upsert([VectorRecord("one", vector, "fast path")])
        result = await HybridRetriever(embeddings, store).retrieve(
            RetrievalRequest("fast", profile="fast")
        )
        assert result.diagnostics.lexical_candidates == 0
        assert result.candidates[0].sources == ("dense",)

    run(scenario())


def test_qdrant_style_sparse_backend_is_used_for_hybrid_retrieval():
    class SparseCapableStore(InMemoryVectorStore):
        async def sparse_query(self, vector, *, limit=10, filters=None):
            assert vector.indices
            record = VectorRecord(
                "lexical",
                [],
                "exact product code ABC-42",
                {"tenant_id": "t1", "acl": ["user"]},
            )
            return [VectorSearchResult(record, 0.9)][:limit]

    async def scenario():
        dense = HashEmbeddingProvider(dimensions=8)
        sparse = HashSparseEmbeddingProvider(dimensions=128)
        store = SparseCapableStore()
        await store.create(8)
        result = await HybridRetriever(
            dense,
            store,
            sparse_embeddings=sparse,
        ).retrieve(
            RetrievalRequest(
                "ABC-42",
                tenant_id="t1",
                principals=("user",),
                profile="balanced",
            )
        )
        assert [candidate.id for candidate in result.candidates] == ["lexical"]
        assert result.diagnostics.lexical_candidates == 1

    run(scenario())
