"""External vector-store contract tests.

These tests are opt-in and never contact services unless both the dependency and
the corresponding RAG_TEST_* environment variable are present.
"""

import asyncio
import importlib.util
import os
import uuid

import pytest

from rag_app.indexing import PgVectorStore, QdrantVectorStore, VectorRecord

pytestmark = pytest.mark.integration


def run(coroutine):
    return asyncio.run(coroutine)


async def assert_contract(store):
    await store.create(3, recreate=True)
    first_id = str(uuid.uuid4())
    second_id = str(uuid.uuid4())
    await store.upsert(
        [
            VectorRecord(first_id, [1, 0, 0], "first", {"tenant_id": "a"}),
            VectorRecord(second_id, [0, 1, 0], "second", {"tenant_id": "b"}),
        ]
    )
    results = await store.query([1, 0, 0], limit=5, filters={"tenant_id": "a"})
    assert [item.id for item in results] == [first_id]
    assert results[0].text == "first"
    assert await store.delete([first_id]) >= 1
    assert await store.query([1, 0, 0], filters={"tenant_id": "a"}) == []


@pytest.mark.skipif(
    importlib.util.find_spec("qdrant_client") is None
    or not os.getenv("RAG_TEST_QDRANT_URL"),
    reason="qdrant-client and RAG_TEST_QDRANT_URL are required",
)
def test_qdrant_vector_store_contract():
    store = QdrantVectorStore(
        os.getenv("RAG_TEST_QDRANT_COLLECTION", "rag_vectors_contract"),
        url=os.environ["RAG_TEST_QDRANT_URL"],
        api_key=os.getenv("RAG_TEST_QDRANT_API_KEY"),
    )
    run(assert_contract(store))


@pytest.mark.skipif(
    importlib.util.find_spec("psycopg") is None or not os.getenv("RAG_TEST_PGVECTOR_DSN"),
    reason="psycopg and RAG_TEST_PGVECTOR_DSN are required",
)
def test_pgvector_store_contract():
    store = PgVectorStore(
        os.environ["RAG_TEST_PGVECTOR_DSN"],
        table=os.getenv("RAG_TEST_PGVECTOR_TABLE", "rag_vectors_contract"),
    )
    run(assert_contract(store))
