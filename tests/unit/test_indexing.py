import asyncio

import pytest

from rag_app.indexing import (
    BatchedCachedEmbeddingProvider,
    HashEmbeddingProvider,
    HashSparseEmbeddingProvider,
    IndexingConfigurationError,
    IndexingService,
    InMemoryEmbeddingCache,
    InMemoryVectorStore,
    PgVectorStore,
    QdrantVectorStore,
    VectorRecord,
)


def run(coroutine):
    return asyncio.run(coroutine)


def test_hash_embeddings_are_deterministic_and_normalized():
    provider = HashEmbeddingProvider(dimensions=32)
    first, second = run(provider.embed_documents(["alpha beta", "alpha beta"]))
    assert first == second
    assert sum(value * value for value in first) == pytest.approx(1.0)
    assert run(provider.embed_query("alpha beta")) == first


def test_batched_cache_deduplicates_content_and_provider_calls():
    class CountingProvider(HashEmbeddingProvider):
        def __init__(self):
            super().__init__(dimensions=16)
            self.calls = []

        async def embed_documents(self, texts):
            self.calls.append(list(texts))
            return await super().embed_documents(texts)

    underlying = CountingProvider()
    provider = BatchedCachedEmbeddingProvider(
        underlying, batch_size=1, cache=InMemoryEmbeddingCache()
    )
    first = run(provider.embed_documents(["one", "one", "two"]))
    second = run(provider.embed_documents(["two", "one"]))
    assert first[0] == first[1] == second[1]
    assert len(underlying.calls) == 2
    assert all(len(batch) == 1 for batch in underlying.calls)


def test_in_memory_store_upsert_filter_query_and_delete():
    async def scenario():
        store = InMemoryVectorStore()
        await store.create(2)
        await store.upsert(
            [
                VectorRecord("a", [1, 0], "alpha", {"tenant_id": "t1", "acl": ["u1"]}),
                VectorRecord("b", [0, 1], "beta", {"tenant_id": "t2", "acl": ["u2"]}),
            ]
        )
        results = await store.query(
            [1, 0], filters={"tenant_id": "t1", "acl": ["u1"]}
        )
        assert [item.id for item in results] == ["a"]
        assert results[0].score == pytest.approx(1.0)
        assert await store.delete(filters={"tenant_id": "t1"}) == 1
        assert [item.id for item in await store.query([0, 1])] == ["b"]

    run(scenario())


def test_indexing_service_accepts_domain_like_chunks():
    class Chunk:
        chunk_id = "chunk-1"
        content = "production retrieval"
        tenant_id = "tenant-a"
        metadata = {"document_id": "doc-1"}

    async def scenario():
        embeddings = HashEmbeddingProvider(dimensions=24)
        store = InMemoryVectorStore()
        service = IndexingService(embeddings, store)
        result = await service.index([Chunk()])
        assert result.ids == ["chunk-1"]
        found = await store.query(
            await embeddings.embed_query("production retrieval"),
            filters={"tenant_id": "tenant-a"},
        )
        assert found[0].text == "production retrieval"

    run(scenario())


def test_indexing_service_stores_sparse_vectors_when_configured():
    async def scenario():
        dense = HashEmbeddingProvider(dimensions=8)
        sparse = HashSparseEmbeddingProvider(dimensions=128)
        store = InMemoryVectorStore()
        service = IndexingService(dense, store, sparse_embeddings=sparse)
        await service.index([{"id": "hybrid", "text": "exact invoice ABC-42"}])
        [record] = await store.scan()
        assert record.sparse_vector is not None
        assert record.sparse_vector.indices
        assert len(record.sparse_vector.indices) == len(record.sparse_vector.values)

    run(scenario())


def test_external_store_configuration_is_explicit():
    with pytest.raises(IndexingConfigurationError, match="URL"):
        QdrantVectorStore("chunks")
    with pytest.raises(IndexingConfigurationError, match="DSN"):
        PgVectorStore("")
