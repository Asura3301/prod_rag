"""Provider-neutral configuration for chat models."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rag_app.core.config import LLMConfig as CoreLLMConfig


class LLMProvider(StrEnum):
    """Supported chat-model API families."""

    OPENAI = "openai"
    OPENAI_COMPATIBLE = "openai_compatible"
    LOCAL = "local"
    ANTHROPIC = "anthropic"


@dataclass(frozen=True, slots=True)
class LLMConfig:
    """Configuration shared by all supported providers."""

    provider: LLMProvider | str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    temperature: float = 0.0
    max_tokens: int = 2048
    timeout: float = 60.0
    max_retries: int = 2

    def __post_init__(self) -> None:
        provider = (
            self.provider
            if isinstance(self.provider, LLMProvider)
            else LLMProvider(str(self.provider).lower())
        )
        object.__setattr__(self, "provider", provider)
        if not self.model.strip():
            raise ValueError("LLM model must not be empty")
        if self.timeout <= 0:
            raise ValueError("LLM timeout must be positive")
        if self.max_retries < 0:
            raise ValueError("LLM max_retries must be non-negative")
        if self.max_tokens <= 0:
            raise ValueError("LLM max_tokens must be positive")
        if provider is LLMProvider.ANTHROPIC and self.base_url:
            raise ValueError("base_url is only supported by OpenAI-compatible providers")

    @classmethod
    def from_core(cls, config: CoreLLMConfig) -> LLMConfig:
        """Adapt validated core settings to the provider runtime configuration."""

        return cls(
            provider=config.provider,
            model=config.model,
            api_key=config.api_key.get_secret_value() if config.api_key else None,
            base_url=config.base_url,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            timeout=config.timeout_seconds,
            max_retries=config.max_retries,
        )

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        prefix: str = "RAG_LLM__",
    ) -> LLMConfig:
        """Load canonical nested variables, with temporary legacy-key fallback."""

        env = os.environ if environ is None else environ
        legacy_prefix = "RAG_LLM_" if prefix == "RAG_LLM__" else None

        def configured(name: str, *, legacy_name: str | None = None) -> str | None:
            value = env.get(f"{prefix}{name}")
            if value is not None or legacy_prefix is None:
                return value
            return env.get(f"{legacy_prefix}{legacy_name or name}")

        provider = LLMProvider((configured("PROVIDER") or "openai_compatible").lower())
        model = configured("MODEL") or env.get("MODEL_ID", "")
        api_key = configured("API_KEY")
        base_url = configured("BASE_URL")

        if provider is LLMProvider.ANTHROPIC:
            api_key = api_key or env.get("ANTHROPIC_API_KEY")
        else:
            api_key = api_key or env.get("OPENAI_API_KEY")
            base_url = base_url or env.get("API_BASE")

        return cls(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=float(configured("TEMPERATURE") or "0"),
            max_tokens=int(configured("MAX_TOKENS") or "2048"),
            timeout=float(configured("TIMEOUT_SECONDS", legacy_name="TIMEOUT") or "60"),
            max_retries=int(configured("MAX_RETRIES") or "2"),
        )
