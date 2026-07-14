"""Public indexing API."""

from .embeddings import (
    BatchedCachedEmbeddingProvider,
    EmbeddingCache,
    EmbeddingProvider,
    HashEmbeddingProvider,
    HashSparseEmbeddingProvider,
    HuggingFaceEmbeddingProvider,
    IndexingConfigurationError,
    InMemoryEmbeddingCache,
    LocalHuggingFaceEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    OpenAIEmbeddingProvider,
    SparseEmbedding,
    SparseEmbeddingProvider,
)
from .service import IndexingResult, IndexingService
from .vector_stores import (
    InMemoryVectorStore,
    PgVectorStore,
    QdrantVectorStore,
    VectorRecord,
    VectorSearchResult,
    VectorStore,
    coerce_vector_record,
    metadata_matches,
)

__all__ = [
    "BatchedCachedEmbeddingProvider",
    "EmbeddingCache",
    "EmbeddingProvider",
    "HashEmbeddingProvider",
    "HashSparseEmbeddingProvider",
    "HuggingFaceEmbeddingProvider",
    "InMemoryEmbeddingCache",
    "IndexingConfigurationError",
    "IndexingResult",
    "IndexingService",
    "InMemoryVectorStore",
    "LocalHuggingFaceEmbeddingProvider",
    "OpenAICompatibleEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "PgVectorStore",
    "QdrantVectorStore",
    "SparseEmbedding",
    "SparseEmbeddingProvider",
    "VectorRecord",
    "VectorSearchResult",
    "VectorStore",
    "coerce_vector_record",
    "metadata_matches",
]
