"""Provider-neutral embedding providers and batching/cache utilities."""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class IndexingConfigurationError(RuntimeError):
    """Raised when an optional indexing provider is not correctly configured."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Small async embedding contract used by the indexing and retrieval layers."""

    @property
    def model_id(self) -> str: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


@dataclass(frozen=True, slots=True)
class SparseEmbedding:
    indices: list[int]
    values: list[float]

    def __post_init__(self) -> None:
        if len(self.indices) != len(self.values):
            raise ValueError("Sparse indices and values must have equal length")


@runtime_checkable
class SparseEmbeddingProvider(Protocol):
    @property
    def model_id(self) -> str: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[SparseEmbedding]: ...

    async def embed_query(self, text: str) -> SparseEmbedding: ...


class HashSparseEmbeddingProvider:
    """Deterministic sparse lexical features for local development and tests."""

    def __init__(self, dimensions: int = 262_144) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions

    @property
    def model_id(self) -> str:
        return f"hash-sparse-v1-{self.dimensions}"

    def _embed(self, text: str) -> SparseEmbedding:
        counts: dict[int, float] = {}
        for token in text.casefold().split():
            index = int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big")
            index %= self.dimensions
            counts[index] = counts.get(index, 0.0) + 1.0
        norm = math.sqrt(sum(value * value for value in counts.values())) or 1.0
        indices = sorted(counts)
        return SparseEmbedding(indices, [counts[index] / norm for index in indices])

    async def embed_documents(self, texts: Sequence[str]) -> list[SparseEmbedding]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> SparseEmbedding:
        return self._embed(text)


class HashEmbeddingProvider:
    """Deterministic, dependency-free feature hashing embeddings.

    This provider is intended for tests and demos, not semantic production search.
    Token feature hashing makes related texts somewhat comparable while preserving
    exact reproducibility across Python processes and platforms.
    """

    def __init__(self, dimensions: int = 128, model_id: str | None = None) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions
        self._model_id = model_id or f"hash-embedding-v1-{dimensions}"

    @property
    def model_id(self) -> str:
        return self._model_id

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = text.casefold().split()
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimensions
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class OpenAICompatibleEmbeddingProvider:
    """Lazy adapter around ``langchain_openai.OpenAIEmbeddings``."""

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        **kwargs: object,
    ) -> None:
        if not model:
            raise IndexingConfigurationError("An embedding model name is required")
        try:
            from langchain_openai import OpenAIEmbeddings
        except ImportError as exc:
            raise IndexingConfigurationError(
                "OpenAI embeddings require the optional 'langchain-openai' package"
            ) from exc
        options = dict(kwargs)
        if api_key is not None:
            options["api_key"] = api_key
        if base_url is not None:
            options["base_url"] = base_url
        embedding_model: object = OpenAIEmbeddings
        self._client = embedding_model(model=model, **options)  # type: ignore[operator]
        self._model_id = model

    @property
    def model_id(self) -> str:
        return self._model_id

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._client.aembed_documents(list(texts))

    async def embed_query(self, text: str) -> list[float]:
        return await self._client.aembed_query(text)


class HuggingFaceEmbeddingProvider:
    """Lazy local SentenceTransformers/BGE adapter."""

    def __init__(
        self,
        model: str = "BAAI/bge-small-en-v1.5",
        *,
        normalize_embeddings: bool = True,
        query_instruction: str | None = None,
        **model_kwargs: object,
    ) -> None:
        self._model_id = model
        self._normalize = normalize_embeddings
        self._instruction = query_instruction
        self._model_kwargs = model_kwargs
        self._model: object | None = None
        self._load_lock = asyncio.Lock()

    @property
    def model_id(self) -> str:
        return self._model_id

    async def _get_model(self) -> object:
        if self._model is None:
            async with self._load_lock:
                if self._model is None:
                    try:
                        from sentence_transformers import SentenceTransformer
                    except ImportError as exc:
                        raise IndexingConfigurationError(
                            "Local embeddings require the optional "
                            "'sentence-transformers' package"
                        ) from exc
                    self._model = await asyncio.to_thread(
                        SentenceTransformer, self._model_id, **self._model_kwargs
                    )
        return self._model

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        model = await self._get_model()

        def encode() -> list[list[float]]:
            values = model.encode(  # type: ignore[attr-defined]
                list(texts), normalize_embeddings=self._normalize
            )
            return [list(map(float, row)) for row in values]

        return await asyncio.to_thread(encode)

    async def embed_query(self, text: str) -> list[float]:
        query = f"{self._instruction}{text}" if self._instruction else text
        return (await self.embed_documents([query]))[0]


# Discoverable aliases for common provider naming conventions.
OpenAIEmbeddingProvider = OpenAICompatibleEmbeddingProvider
LocalHuggingFaceEmbeddingProvider = HuggingFaceEmbeddingProvider


class EmbeddingCache(Protocol):
    async def get(self, key: str) -> list[float] | None: ...

    async def set(self, key: str, value: Sequence[float]) -> None: ...


class InMemoryEmbeddingCache:
    def __init__(self) -> None:
        self._values: dict[str, tuple[float, ...]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> list[float] | None:
        async with self._lock:
            value = self._values.get(key)
        return list(value) if value is not None else None

    async def set(self, key: str, value: Sequence[float]) -> None:
        async with self._lock:
            self._values[key] = tuple(float(item) for item in value)


class BatchedCachedEmbeddingProvider:
    """Adds bounded batching and content/model-addressed caching to a provider."""

    def __init__(
        self,
        provider: EmbeddingProvider,
        *,
        batch_size: int = 64,
        cache: EmbeddingCache | None = None,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.provider = provider
        self.batch_size = batch_size
        self.cache = cache or InMemoryEmbeddingCache()

    @property
    def model_id(self) -> str:
        return self.provider.model_id

    def cache_key(self, text: str) -> str:
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return f"{self.model_id}:{content_hash}"

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        results: list[list[float] | None] = [None] * len(texts)
        missing_by_key: dict[str, tuple[str, list[int]]] = {}
        for index, text in enumerate(texts):
            key = self.cache_key(text)
            cached = await self.cache.get(key)
            if cached is not None:
                results[index] = cached
            elif key in missing_by_key:
                missing_by_key[key][1].append(index)
            else:
                missing_by_key[key] = (text, [index])

        missing = list(missing_by_key.items())
        for start in range(0, len(missing), self.batch_size):
            batch = missing[start : start + self.batch_size]
            vectors = await self.provider.embed_documents([item[1][0] for item in batch])
            if len(vectors) != len(batch):
                raise ValueError("Embedding provider returned an unexpected vector count")
            for (key, (_, indices)), vector in zip(batch, vectors, strict=True):
                normalized = list(map(float, vector))
                await self.cache.set(key, normalized)
                for index in indices:
                    results[index] = list(normalized)
        return [result if result is not None else [] for result in results]

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]


async def run_sync_or_async(
    function: Callable[..., object | Awaitable[object]], *args: object
) -> object:
    """Call an expansion hook regardless of whether it is sync or async."""
    result = function(*args)
    return await result if isinstance(result, Awaitable) else result
