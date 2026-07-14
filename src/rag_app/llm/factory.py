"""Lazy, provider-safe chat model construction."""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any

from .config import LLMConfig, LLMProvider


def create_chat_model(config: LLMConfig) -> Any:
    """Construct only the configured provider and delegate retries/timeouts to it."""

    common = {
        "model": config.model,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "timeout": config.timeout,
        "max_retries": config.max_retries,
    }

    if config.provider is LLMProvider.ANTHROPIC:
        from langchain_anthropic import ChatAnthropic

        anthropic_model: Any = ChatAnthropic
        return anthropic_model(api_key=config.api_key, **common)

    if config.provider in {
        LLMProvider.OPENAI,
        LLMProvider.OPENAI_COMPATIBLE,
        LLMProvider.LOCAL,
    }:
        from langchain_openai import ChatOpenAI

        api_key = config.api_key
        if config.provider is LLMProvider.LOCAL and not api_key:
            api_key = "local-not-required"
        openai_model: Any = ChatOpenAI
        return openai_model(
            api_key=api_key,
            base_url=config.base_url,
            **common,
        )

    raise ValueError(f"Unsupported LLM provider: {config.provider}")


class LazyChatModel:
    """A transparent proxy that constructs the provider on first use."""

    def __init__(
        self,
        config: LLMConfig,
        factory: Callable[[LLMConfig], Any] = create_chat_model,
    ) -> None:
        self.config = config
        self._factory = factory
        self._instance: Any | None = None
        self._lock = Lock()

    def get(self) -> Any:
        if self._instance is None:
            with self._lock:
                if self._instance is None:
                    self._instance = self._factory(self.config)
        return self._instance

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return self.get().invoke(*args, **kwargs)

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        return await self.get().ainvoke(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.get(), name)


def get_llm(config: LLMConfig, *, lazy: bool = True) -> Any:
    """Return a lazy model by default, or eagerly construct one."""

    return LazyChatModel(config) if lazy else create_chat_model(config)
