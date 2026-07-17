from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from rag_app.core.config import Settings
from rag_app.core.logging import setup_logging
from rag_app.llm import LLMConfig as RuntimeLLMConfig
from rag_app.llm import LLMProvider


def settings_without_dotenv() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_load_nested_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ENVIRONMENT", "staging")
    monkeypatch.setenv("RAG_LLM__PROVIDER", "anthropic")
    monkeypatch.setenv("RAG_LLM__MODEL", "claude-test")
    monkeypatch.setenv("RAG_LLM__API_KEY", "secret")
    monkeypatch.setenv("RAG_EMBEDDINGS__DIMENSIONS", "768")
    monkeypatch.setenv("RAG_RETRIEVAL__TOP_K", "12")

    settings = settings_without_dotenv()

    assert settings.environment == "staging"
    assert settings.llm.provider == "anthropic"
    assert settings.llm.model == "claude-test"
    assert settings.llm.api_key is not None
    assert settings.llm.api_key.get_secret_value() == "secret"
    assert settings.embeddings.dimensions == 768
    assert settings.retrieval.top_k == 12
    assert "secret" not in repr(settings)


def test_settings_load_canonical_local_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_RUNTIME_MODE", "distributed")
    monkeypatch.setenv("RAG_API_TOKEN", "api-secret")
    monkeypatch.setenv("RAG_ADMIN_TOKEN", "admin-secret")
    monkeypatch.setenv("RAG_REDIS_URL", "redis://redis:6379/1")
    monkeypatch.setenv("RAG_SQLITE_PATH", "state/documents.sqlite3")
    monkeypatch.setenv("RAG_INGESTION_ROOT", "incoming")
    monkeypatch.setenv("RAG_LLM__PROVIDER", "local")
    monkeypatch.setenv("RAG_LLM__MODEL", "local-model")
    monkeypatch.setenv("RAG_LLM__BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("RAG_LLM__TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("RAG_EMBEDDINGS__PROVIDER", "local")
    monkeypatch.setenv("RAG_EMBEDDINGS__MODEL", "BAAI/bge-m3")
    monkeypatch.setenv("RAG_VECTOR_STORE__PROVIDER", "qdrant")
    monkeypatch.setenv("RAG_VECTOR_STORE__URL", "http://127.0.0.1:6333")

    settings = settings_without_dotenv()

    assert settings.runtime_mode == "distributed"
    assert settings.api_token is not None
    assert settings.api_token.get_secret_value() == "api-secret"
    assert settings.admin_token is not None
    assert settings.admin_token.get_secret_value() == "admin-secret"
    assert settings.redis_url == "redis://redis:6379/1"
    assert settings.sqlite_path == Path("state/documents.sqlite3")
    assert settings.ingestion_root == Path("incoming")
    assert settings.llm.provider == "local"
    assert settings.embeddings.model == "BAAI/bge-m3"
    assert settings.vector_store.provider == "qdrant"
    assert "api-secret" not in repr(settings)
    assert "admin-secret" not in repr(settings)


def test_runtime_llm_config_adapts_typed_core_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_LLM__PROVIDER", "local")
    monkeypatch.setenv("RAG_LLM__MODEL", "local-model")
    monkeypatch.setenv("RAG_LLM__API_KEY", "local-secret")
    monkeypatch.setenv("RAG_LLM__BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("RAG_LLM__TIMEOUT_SECONDS", "45")
    settings = settings_without_dotenv()

    config = RuntimeLLMConfig.from_core(settings.llm)

    assert config.provider is LLMProvider.LOCAL
    assert config.model == "local-model"
    assert config.api_key == "local-secret"
    assert config.base_url == "http://127.0.0.1:1234/v1"
    assert config.timeout == 45


def test_runtime_llm_config_prefers_canonical_env_and_accepts_legacy() -> None:
    legacy = RuntimeLLMConfig.from_env(
        {
            "RAG_LLM_PROVIDER": "local",
            "RAG_LLM_MODEL": "legacy-model",
            "RAG_LLM_BASE_URL": "http://legacy.test/v1",
            "RAG_LLM_TIMEOUT": "12",
        }
    )
    canonical = RuntimeLLMConfig.from_env(
        {
            "RAG_LLM__PROVIDER": "local",
            "RAG_LLM__MODEL": "canonical-model",
            "RAG_LLM__TIMEOUT_SECONDS": "25",
            "RAG_LLM_MODEL": "legacy-model",
            "RAG_LLM_TIMEOUT": "12",
        }
    )

    assert legacy.model == "legacy-model"
    assert legacy.base_url == "http://legacy.test/v1"
    assert legacy.timeout == 12
    assert canonical.model == "canonical-model"
    assert canonical.timeout == 25


def test_chunk_overlap_must_be_smaller_than_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_CHUNKING__CHUNK_SIZE", "100")
    monkeypatch.setenv("RAG_CHUNKING__CHUNK_OVERLAP", "100")

    with pytest.raises(ValidationError, match="chunk_overlap"):
        settings_without_dotenv()


def test_production_rejects_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ENVIRONMENT", "production")
    monkeypatch.setenv("RAG_DEBUG", "true")

    with pytest.raises(ValidationError, match="debug must be disabled"):
        settings_without_dotenv()


def test_json_logging_contains_context(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging("INFO", json_output=True)
    logging.getLogger("rag.test").info("indexed", extra={"document_id": "doc-1"})

    event = json.loads(capsys.readouterr().out)
    assert event["level"] == "INFO"
    assert event["message"] == "indexed"
    assert event["document_id"] == "doc-1"
