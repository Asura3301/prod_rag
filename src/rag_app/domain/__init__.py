"""Public domain models and adapter contracts."""

from rag_app.domain.models import (
    ChunkRecord,
    Citation,
    DocumentRecord,
    Embedding,
    IngestionRecord,
    IngestionStatus,
    Metadata,
    RAGResponse,
    SearchResult,
)
from rag_app.domain.protocols import (
    EmbeddingProvider,
    EmbeddingProviderProtocol,
    LLMProtocol,
    LLMProvider,
    RAGService,
    Retriever,
    RetrieverProtocol,
    VectorStore,
    VectorStoreProtocol,
)

__all__ = [
    "ChunkRecord",
    "Citation",
    "DocumentRecord",
    "Embedding",
    "EmbeddingProvider",
    "EmbeddingProviderProtocol",
    "IngestionRecord",
    "IngestionStatus",
    "LLMProtocol",
    "LLMProvider",
    "Metadata",
    "RAGResponse",
    "RAGService",
    "Retriever",
    "RetrieverProtocol",
    "SearchResult",
    "VectorStore",
    "VectorStoreProtocol",
]
