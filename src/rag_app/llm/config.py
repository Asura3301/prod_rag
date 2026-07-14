"""Provider-neutral configuration for chat models."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


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
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        prefix: str = "RAG_LLM_",
    ) -> LLMConfig:
        """Load configuration without reading or importing a provider SDK."""

        env = os.environ if environ is None else environ
        provider = LLMProvider(env.get(f"{prefix}PROVIDER", "openai_compatible").lower())
        model = env.get(f"{prefix}MODEL") or env.get("MODEL_ID", "")
        api_key = env.get(f"{prefix}API_KEY")
        base_url = env.get(f"{prefix}BASE_URL")

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
            temperature=float(env.get(f"{prefix}TEMPERATURE", "0")),
            max_tokens=int(env.get(f"{prefix}MAX_TOKENS", "2048")),
            timeout=float(env.get(f"{prefix}TIMEOUT", "60")),
            max_retries=int(env.get(f"{prefix}MAX_RETRIES", "2")),
        )
