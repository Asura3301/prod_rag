"""Application-specific exception hierarchy."""

from __future__ import annotations

from typing import Any


class RAGError(Exception):
    """Base class for expected application failures."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "rag_error",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class ConfigurationError(RAGError):
    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, code="configuration_error", **kwargs)


class IngestionError(RAGError):
    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, code="ingestion_error", **kwargs)


class EmbeddingError(RAGError):
    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, code="embedding_error", **kwargs)


class VectorStoreError(RAGError):
    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, code="vector_store_error", **kwargs)


class RetrievalError(RAGError):
    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, code="retrieval_error", **kwargs)


class GenerationError(RAGError):
    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, code="generation_error", **kwargs)


class ExternalServiceError(RAGError):
    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        retryable: bool = False,
        **kwargs: Any,
    ) -> None:
        details = dict(kwargs.pop("details", {}) or {})
        details.update(provider=provider, retryable=retryable)
        super().__init__(
            message,
            code="external_service_error",
            details=details,
            **kwargs,
        )
        self.provider = provider
        self.retryable = retryable
