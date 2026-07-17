"""Network-free unit tests for the bounded generation graph."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from langchain_core.documents import Document

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from rag_app.generation import ABSTENTION_TEXT, STATE_VERSION, build_rag_graph


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return next(self.responses)


class FakeRetriever:
    def __init__(self, batches: list[list[Document]]) -> None:
        self.batches = iter(batches)
        self.queries: list[str] = []

    def invoke(self, query: str) -> list[Document]:
        self.queries.append(query)
        return next(self.batches)


def _document() -> Document:
    return Document(
        page_content="The deployment window starts at 09:00 UTC.",
        metadata={
            "chunk_id": "ops-17",
            "source": "runbook.pdf",
            "page": 12,
            "section": "Deployment",
        },
    )


@pytest.mark.asyncio
async def test_answer_path_returns_structured_citation() -> None:
    retriever = FakeRetriever([[_document()]])
    llm = FakeLLM(["It starts at 09:00 UTC [C1]."])

    result = await build_rag_graph(retriever, llm).ainvoke(
        {"request": "When does deployment start?"}
    )
    response = result["response"]

    assert response["status"] == "answered"
    assert response["answer"] == "It starts at 09:00 UTC [C1]."
    assert response["citations"] == [
        {
            "citation_id": "C1",
            "chunk_id": "ops-17",
            "document_id": "ops-17",
            "source": "runbook.pdf",
            "page": 12,
            "section": "Deployment",
            "excerpt": "The deployment window starts at 09:00 UTC.",
            "score": None,
        }
    ]
    assert response["state_version"] == STATE_VERSION
    assert result["evidence"] == []
    assert result["context"] == ""


@pytest.mark.asyncio
async def test_no_evidence_abstains_deterministically() -> None:
    retriever = FakeRetriever([[], []])
    llm = FakeLLM(["broader deployment timing"])

    result = await build_rag_graph(retriever, llm).ainvoke({"request": "Unknown fact"})
    response = result["response"]

    assert response["status"] == "abstained"
    assert response["answer"] == ABSTENTION_TEXT
    assert response["citations"] == []
    assert len(llm.prompts) == 1  # rewrite only; no unsupported answer generation


@pytest.mark.asyncio
async def test_rewrite_is_bounded_to_one_attempt() -> None:
    retriever = FakeRetriever([[], []])
    llm = FakeLLM(["broader query"])

    result = await build_rag_graph(retriever, llm).ainvoke({"request": "Find this"})

    assert retriever.queries == ["Find this", "broader query"]
    assert result["response"]["rewrite_count"] == 1
    assert len(llm.prompts) == 1


@pytest.mark.asyncio
async def test_invalid_citation_causes_safe_abstention() -> None:
    retriever = FakeRetriever([[_document()]])
    llm = FakeLLM(["An unsupported answer [C99]."])

    result = await build_rag_graph(retriever, llm).ainvoke({"request": "When?"})
    response = result["response"]

    assert response["status"] == "abstained"
    assert response["answer"] == ABSTENTION_TEXT
    assert response["citations"] == []
    assert "invalid citations" in result["error"]


@pytest.mark.asyncio
async def test_fullwidth_citation_brackets_are_normalized() -> None:
    retriever = FakeRetriever([[_document()]])
    llm = FakeLLM(["It starts at 09:00 UTC【C1】."])

    result = await build_rag_graph(retriever, llm).ainvoke({"request": "When?"})

    assert result["response"]["status"] == "answered"
    assert result["response"]["answer"] == "It starts at 09:00 UTC[C1]."
    assert result["response"]["citations"][0]["citation_id"] == "C1"
