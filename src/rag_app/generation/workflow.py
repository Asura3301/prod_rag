"""A bounded, citation-validating LangGraph RAG workflow."""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, START, StateGraph

from .prompts import ABSTENTION_TEXT, ANSWER_PROMPT, QUERY_REWRITE_PROMPT
from .state import STATE_VERSION, Citation, Evidence, RAGResponse, RAGState

_CITATION_PATTERN = re.compile(r"\[(C\d+)\]")
_CITATION_BRACKETS = str.maketrans({"【": "[", "】": "]", "［": "[", "］": "]"})


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    """Safety and state-size limits for one graph."""

    max_rewrites: int = 1
    max_evidence: int = 8
    max_chunk_chars: int = 4_000
    max_context_chars: int = 16_000
    minimum_score: float | None = None

    def __post_init__(self) -> None:
        if self.max_rewrites != 1:
            raise ValueError("This workflow permits exactly one bounded rewrite")
        if self.max_evidence <= 0 or self.max_chunk_chars <= 0:
            raise ValueError("Evidence limits must be positive")


def _message_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping):
                text = item.get("text")
                if text:
                    parts.append(str(text))
            else:
                parts.append(str(item))
        return "\n".join(parts).strip()
    return str(content).strip()


async def _ainvoke(component: Any, value: str) -> Any:
    if hasattr(component, "ainvoke"):
        return await component.ainvoke(value)
    if hasattr(component, "retrieve"):
        result = component.retrieve(value)
        return await result if inspect.isawaitable(result) else result
    if hasattr(component, "invoke"):
        return await asyncio.to_thread(component.invoke, value)
    if callable(component):
        result = component(value)
        return await result if inspect.isawaitable(result) else result
    raise TypeError(f"{type(component).__name__} is neither callable nor invokable")


class RAGWorkflow:
    """Node implementation with injected retriever and language model."""

    def __init__(
        self,
        retriever: Any,
        llm: Any,
        config: GenerationConfig | None = None,
    ) -> None:
        self.retriever = retriever
        self.llm = llm
        self.config = config or GenerationConfig()

    def validate_request(self, state: RAGState) -> dict[str, Any]:
        request = str(state.get("request") or state.get("query") or "").strip()
        base: dict[str, Any] = {
            "state_version": STATE_VERSION,
            "request": request,
            "query": request,
            "original_query": request,
            "retrieval_query": request,
            "rewrite_count": 0,
            "max_rewrites": self.config.max_rewrites,
            "evidence": [],
            "valid_citation_ids": [],
            "status": "running",
        }
        if not request:
            base.update(
                status="invalid_request",
                error="A non-empty request is required.",
                draft_answer=ABSTENTION_TEXT,
            )
        return base

    @staticmethod
    def route_valid_request(state: RAGState) -> str:
        return "finalize_response" if state.get("status") == "invalid_request" else "analyze_query"

    def analyze_query(self, state: RAGState) -> dict[str, Any]:
        # Deliberately deterministic: query analysis does not spend a model call,
        # and the explicit rewrite node handles one failed retrieval.
        query = " ".join(state["query"].split())
        return {"retrieval_query": query}

    async def retrieve(self, state: RAGState) -> dict[str, Any]:
        result = await _ainvoke(self.retriever, state["retrieval_query"])
        if hasattr(result, "candidates"):
            result = result.candidates
        if result is None:
            documents: list[Any] = []
        elif isinstance(result, (str, bytes, Mapping)):
            documents = [result]
        else:
            documents = list(result)
        evidence = [
            item
            for index, raw in enumerate(documents[: self.config.max_evidence], start=1)
            if (item := self._to_evidence(raw, index)) is not None
        ]
        return {"evidence": evidence}

    def _to_evidence(self, raw: Any, index: int) -> Evidence | None:
        score: float | None = None
        document = raw
        if isinstance(raw, tuple) and len(raw) == 2:
            document, raw_score = raw
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                score = None

        if isinstance(document, Mapping):
            metadata = dict(document.get("metadata") or {})
            content = document.get("page_content", document.get("content", document.get("text", "")))
            score_value = document.get("score", metadata.get("score", score))
        else:
            metadata = dict(getattr(document, "metadata", {}) or {})
            content = getattr(
                document,
                "page_content",
                getattr(document, "content", getattr(document, "text", "")),
            )
            score_value = getattr(document, "score", metadata.get("score", score))

        text = str(content or "").strip()
        if not text:
            return None
        try:
            normalized_score = float(score_value) if score_value is not None else None
        except (TypeError, ValueError):
            normalized_score = None

        chunk_id = str(
            metadata.get("chunk_id")
            or metadata.get("id")
            or metadata.get("document_id")
            or f"chunk-{index}"
        )
        document_id = str(metadata.get("document_id") or chunk_id)
        source = str(metadata.get("source") or metadata.get("url") or "unknown")
        page = metadata.get("page", metadata.get("page_number"))
        section_value = metadata.get("section", metadata.get("heading"))
        return {
            "citation_id": f"C{index}",
            "chunk_id": chunk_id,
            "document_id": document_id,
            "source": source,
            "page": page,
            "section": str(section_value) if section_value is not None else None,
            "content": text[: self.config.max_chunk_chars],
            "score": normalized_score,
        }

    def grade_evidence(self, state: RAGState) -> dict[str, Any]:
        threshold = self.config.minimum_score
        usable = [
            item
            for item in state.get("evidence", [])
            if item["content"] and (threshold is None or item["score"] is None or item["score"] >= threshold)
        ]
        # Re-number after filtering so prompt identifiers remain contiguous.
        normalized: list[Evidence] = []
        for index, item in enumerate(usable, start=1):
            normalized.append({**item, "citation_id": f"C{index}"})
        return {"evidence": normalized}

    @staticmethod
    def route_after_grading(state: RAGState) -> str:
        if state.get("evidence"):
            return "build_context"
        if state.get("rewrite_count", 0) < state.get("max_rewrites", 1):
            return "rewrite_query"
        return "build_context"

    async def rewrite_query(self, state: RAGState) -> dict[str, Any]:
        if state.get("rewrite_count", 0) >= self.config.max_rewrites:
            return {}
        prompt = QUERY_REWRITE_PROMPT.format(
            original_query=state["original_query"],
            retrieval_query=state["retrieval_query"],
        )
        rewritten = _message_text(await _ainvoke(self.llm, prompt))
        return {
            "retrieval_query": rewritten or state["retrieval_query"],
            "rewrite_count": state.get("rewrite_count", 0) + 1,
        }

    def build_context(self, state: RAGState) -> dict[str, Any]:
        blocks = []
        for item in state.get("evidence", []):
            location = f"source={item['source']}"
            if item["page"] is not None:
                location += f", page={item['page']}"
            if item["section"]:
                location += f", section={item['section']}"
            blocks.append(
                f"[{item['citation_id']}] chunk_id={item['chunk_id']}; {location}\n"
                f"{item['content']}"
            )
        return {"context": "\n\n".join(blocks)[: self.config.max_context_chars]}

    async def generate_answer(self, state: RAGState) -> dict[str, Any]:
        if not state.get("evidence"):
            return {"draft_answer": ABSTENTION_TEXT, "status": "abstained"}
        prompt = ANSWER_PROMPT.format(query=state["original_query"], context=state["context"])
        answer = _message_text(await _ainvoke(self.llm, prompt))
        return {"draft_answer": answer or ABSTENTION_TEXT}

    def validate_citations(self, state: RAGState) -> dict[str, Any]:
        answer = state.get("draft_answer", ABSTENTION_TEXT).strip().translate(_CITATION_BRACKETS)
        if answer == ABSTENTION_TEXT or not state.get("evidence"):
            return {
                "draft_answer": ABSTENTION_TEXT,
                "status": "abstained",
                "valid_citation_ids": [],
            }

        cited = _CITATION_PATTERN.findall(answer)
        allowed = {item["citation_id"] for item in state["evidence"]}
        if not cited or any(citation_id not in allowed for citation_id in cited):
            return {
                "draft_answer": ABSTENTION_TEXT,
                "status": "abstained",
                "valid_citation_ids": [],
                "error": "Generated answer contained missing or invalid citations.",
            }
        ordered_unique = list(dict.fromkeys(cited))
        return {
            "draft_answer": answer,
            "status": "answered",
            "valid_citation_ids": ordered_unique,
        }

    def finalize_response(self, state: RAGState) -> dict[str, Any]:
        evidence_by_id = {
            item["citation_id"]: item for item in state.get("evidence", [])
        }
        citations: list[Citation] = []
        for citation_id in state.get("valid_citation_ids", []):
            item = evidence_by_id[citation_id]
            citations.append(
                {
                    "citation_id": citation_id,
                    "chunk_id": item["chunk_id"],
                    "document_id": item["document_id"],
                    "source": item["source"],
                    "page": item["page"],
                    "section": item["section"],
                    "excerpt": item["content"][:280],
                    "score": item["score"],
                }
            )
        status = state.get("status", "abstained")
        response: RAGResponse = {
            "answer": state.get("draft_answer", ABSTENTION_TEXT),
            "status": status if status != "running" else "abstained",
            "citations": citations,
            "query": state.get("original_query", ""),
            "rewrite_count": state.get("rewrite_count", 0),
            "state_version": STATE_VERSION,
        }
        # Clear transient bodies before the terminal checkpoint.
        return {
            "response": response,
            "evidence": [],
            "context": "",
            "draft_answer": "",
        }


def build_rag_graph(
    retriever: Any,
    llm: Any,
    *,
    checkpointer: Any | None = None,
    config: GenerationConfig | None = None,
) -> Any:
    """Build and compile the finite RAG graph."""

    workflow = RAGWorkflow(retriever=retriever, llm=llm, config=config)
    graph = StateGraph(RAGState)
    graph.add_node("validate_request", workflow.validate_request)
    graph.add_node("analyze_query", workflow.analyze_query)
    graph.add_node("retrieve", workflow.retrieve)
    graph.add_node("grade_evidence", workflow.grade_evidence)
    graph.add_node("rewrite_query", workflow.rewrite_query)
    graph.add_node("build_context", workflow.build_context)
    graph.add_node("generate_answer", workflow.generate_answer)
    graph.add_node("validate_citations", workflow.validate_citations)
    graph.add_node("finalize_response", workflow.finalize_response)

    graph.add_edge(START, "validate_request")
    graph.add_conditional_edges(
        "validate_request",
        workflow.route_valid_request,
        {
            "analyze_query": "analyze_query",
            "finalize_response": "finalize_response",
        },
    )
    graph.add_edge("analyze_query", "retrieve")
    graph.add_edge("retrieve", "grade_evidence")
    graph.add_conditional_edges(
        "grade_evidence",
        workflow.route_after_grading,
        {
            "rewrite_query": "rewrite_query",
            "build_context": "build_context",
        },
    )
    graph.add_edge("rewrite_query", "retrieve")
    graph.add_edge("build_context", "generate_answer")
    graph.add_edge("generate_answer", "validate_citations")
    graph.add_edge("validate_citations", "finalize_response")
    graph.add_edge("finalize_response", END)
    return graph.compile(checkpointer=checkpointer)


create_rag_graph = build_rag_graph
