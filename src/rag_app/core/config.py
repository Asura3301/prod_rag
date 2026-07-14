"""Application configuration loaded explicitly from environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderConfig(_ConfigModel):
    """Connection details shared by provider-backed services."""

    provider: str = Field(default="openai", min_length=1)
    api_key: SecretStr | None = None
    base_url: str | None = None
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=3, ge=0, le=20)


class LLMConfig(ProviderConfig):
    model: str = Field(default="gpt-4o-mini", min_length=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, gt=0)


class EmbeddingConfig(ProviderConfig):
    model: str = Field(default="text-embedding-3-small", min_length=1)
    dimensions: int | None = Field(default=None, gt=0)
    batch_size: int = Field(default=64, gt=0)


class VectorStoreConfig(_ConfigModel):
    provider: str = Field(default="memory", min_length=1)
    url: str | None = None
    api_key: SecretStr | None = None
    index_name: str = Field(default="documents", min_length=1)
    namespace: str = Field(default="default", min_length=1)
    timeout_seconds: float = Field(default=30.0, gt=0)


class ChunkingConfig(_ConfigModel):
    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=120, ge=0)

    @model_validator(mode="after")
    def validate_overlap(self) -> ChunkingConfig:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self


class RetrievalConfig(_ConfigModel):
    top_k: int = Field(default=5, gt=0, le=100)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class LoggingConfig(_ConfigModel):
    level: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = "INFO"
    json_output: bool = Field(
        default=True,
        validation_alias=AliasChoices("json_output", "json"),
    )


class Settings(BaseSettings):
    """Validated, provider-neutral runtime settings.

    Nested values use ``__`` separators, for example
    ``RAG_LLM__PROVIDER=anthropic``.
    """

    model_config = SettingsConfigDict(
        env_prefix="RAG_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    app_name: str = Field(default="prod-rag", min_length=1)
    environment: Literal["development", "test", "staging", "production"] = "development"
    debug: bool = False
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embeddings: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    vector_store: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @model_validator(mode="after")
    def validate_environment(self) -> Settings:
        if self.environment == "production" and self.debug:
            raise ValueError("debug must be disabled in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Create and cache settings on first explicit call."""

    return Settings()
