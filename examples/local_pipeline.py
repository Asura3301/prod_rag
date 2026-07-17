"""Runnable indexing and hybrid-retrieval example with no external services."""

from __future__ import annotations

import asyncio
import json

from rag_app.indexing import HashEmbeddingProvider, IndexingService, InMemoryVectorStore
from rag_app.retrieval import HybridRetriever, RetrievalRequest


async def main() -> None:
    embeddings = HashEmbeddingProvider(dimensions=128)
    store = InMemoryVectorStore()
    await store.create(dimensions=128)

    chunks = [
        {
            "id": "refund-policy",
            "text": "Refund requests are accepted within 30 days of purchase.",
            "metadata": {"tenant_id": "demo", "source": "memory://refunds"},
        },
        {
            "id": "support-hours",
            "text": "Customer support is available Monday through Friday, 09:00-17:00 UTC.",
            "metadata": {"tenant_id": "demo", "source": "memory://support"},
        },
    ]

    indexing = IndexingService(embeddings, store)
    indexed = await indexing.index(chunks)

    retriever = HybridRetriever(embeddings, store)
    result = await retriever.retrieve(
        RetrievalRequest(
            query="How long do I have to request a refund?",
            top_k=2,
            profile="balanced",
            tenant_id="demo",
        )
    )

    print(
        json.dumps(
            {
                "indexed": indexed.indexed,
                "results": [
                    {
                        "id": candidate.id,
                        "score": candidate.score,
                        "text": candidate.text,
                    }
                    for candidate in result.candidates
                ],
                "diagnostics": {
                    "profile": result.diagnostics.profile,
                    "dense_candidates": result.diagnostics.dense_candidates,
                    "lexical_candidates": result.diagnostics.lexical_candidates,
                    "returned": result.diagnostics.returned,
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
