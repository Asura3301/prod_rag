"""Optional reranking adapters."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from typing import Protocol, TypeVar, runtime_checkable

from rag_app.indexing.embeddings import IndexingConfigurationError


class Rerankable(Protocol):
    text: str
    rerank_score: float | None


T = TypeVar("T", bound=Rerankable)


@runtime_checkable
class Reranker(Protocol):
    async def rerank(
        self, query: str, candidates: Sequence[T], *, top_k: int | None = None
    ) -> list[T]: ...


class IdentityReranker:
    async def rerank(
        self, query: str, candidates: Sequence[T], *, top_k: int | None = None
    ) -> list[T]:
        del query
        return list(candidates[:top_k])


class CrossEncoderReranker:
    """Lazy sentence-transformers cross-encoder adapter.

    Candidate objects must expose ``text`` and may expose a mutable ``rerank_score``.
    """

    def __init__(self, model: str = "BAAI/bge-reranker-base", **model_kwargs: object) -> None:
        self.model_id = model
        self._model_kwargs = model_kwargs
        self._model: object | None = None
        self._lock = asyncio.Lock()

    async def _get_model(self) -> object:
        if self._model is None:
            async with self._lock:
                if self._model is None:
                    try:
                        from sentence_transformers import CrossEncoder
                    except ImportError as exc:
                        raise IndexingConfigurationError(
                            "Local reranking requires the optional "
                            "'sentence-transformers' package"
                        ) from exc
                    self._model = await asyncio.to_thread(
                        CrossEncoder, self.model_id, **self._model_kwargs
                    )
        return self._model

    async def rerank(
        self, query: str, candidates: Sequence[T], *, top_k: int | None = None
    ) -> list[T]:
        model = await self._get_model()
        pairs = [(query, str(item.text)) for item in candidates]
        scores = await asyncio.to_thread(model.predict, pairs)  # type: ignore[attr-defined]
        ranked = sorted(
            zip(candidates, scores, strict=True),
            key=lambda pair: float(pair[1]),
            reverse=True,
        )
        output = []
        for candidate, score in ranked[:top_k]:
            with contextlib.suppress(AttributeError, TypeError):
                candidate.rerank_score = float(score)
            output.append(candidate)
        return output
