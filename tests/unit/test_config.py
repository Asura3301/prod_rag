from __future__ import annotations

import json
import logging

import pytest
from pydantic import ValidationError

from rag_app.core.config import Settings
from rag_app.core.logging import setup_logging


def test_settings_load_nested_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ENVIRONMENT", "staging")
    monkeypatch.setenv("RAG_LLM__PROVIDER", "anthropic")
    monkeypatch.setenv("RAG_LLM__MODEL", "claude-test")
    monkeypatch.setenv("RAG_LLM__API_KEY", "secret")
    monkeypatch.setenv("RAG_EMBEDDINGS__DIMENSIONS", "768")
    monkeypatch.setenv("RAG_RETRIEVAL__TOP_K", "12")

    settings = Settings(_env_file=None)

    assert settings.environment == "staging"
    assert settings.llm.provider == "anthropic"
    assert settings.llm.model == "claude-test"
    assert settings.llm.api_key is not None
    assert settings.llm.api_key.get_secret_value() == "secret"
    assert settings.embeddings.dimensions == 768
    assert settings.retrieval.top_k == 12
    assert "secret" not in repr(settings)


def test_chunk_overlap_must_be_smaller_than_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_CHUNKING__CHUNK_SIZE", "100")
    monkeypatch.setenv("RAG_CHUNKING__CHUNK_OVERLAP", "100")

    with pytest.raises(ValidationError, match="chunk_overlap"):
        Settings(_env_file=None)


def test_production_rejects_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ENVIRONMENT", "production")
    monkeypatch.setenv("RAG_DEBUG", "true")

    with pytest.raises(ValidationError, match="debug must be disabled"):
        Settings(_env_file=None)


def test_json_logging_contains_context(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging("INFO", json_output=True)
    logging.getLogger("rag.test").info("indexed", extra={"document_id": "doc-1"})

    event = json.loads(capsys.readouterr().out)
    assert event["level"] == "INFO"
    assert event["message"] == "indexed"
    assert event["document_id"] == "doc-1"
