"""Reproducible offline evaluation records, reports, and regression gates."""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .metrics import (
    citation_scores,
    latency_summary,
    mean_reciprocal_rank,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)


@dataclass(frozen=True, slots=True)
class GoldenExample:
    id: str
    query: str
    relevant_document_ids: tuple[str, ...]
    expected_citation_ids: tuple[str, ...]
    reference_answer: str


@dataclass(frozen=True, slots=True)
class EvaluationPrediction:
    example_id: str
    retrieved_document_ids: tuple[str, ...]
    citation_ids: tuple[str, ...]
    answer: str
    latency_ms: float


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    examples: int
    recall_at_k: float
    precision_at_k: float
    mrr: float
    ndcg_at_k: float
    citation_precision: float
    citation_recall: float
    abstention_accuracy: float
    latency_p95_ms: float

    def to_dict(self) -> dict[str, int | float]:
        return asdict(self)


def load_golden_dataset(path: str | Path) -> list[GoldenExample]:
    rows = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            data: dict[str, Any] = json.loads(line)
            rows.append(
                GoldenExample(
                    id=str(data["id"]),
                    query=str(data["query"]),
                    relevant_document_ids=tuple(map(str, data["relevant_document_ids"])),
                    expected_citation_ids=tuple(map(str, data["expected_citation_ids"])),
                    reference_answer=str(data["reference_answer"]),
                )
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid golden dataset row {line_number}: {exc}") from exc
    if not rows:
        raise ValueError("Golden dataset is empty")
    return rows


def evaluate_predictions(
    examples: list[GoldenExample],
    predictions: list[EvaluationPrediction],
    *,
    k: int = 5,
) -> EvaluationReport:
    by_id = {prediction.example_id: prediction for prediction in predictions}
    if set(by_id) != {example.id for example in examples}:
        raise ValueError("Predictions must contain exactly one row for every golden example")

    ranked: list[tuple[str, ...]] = []
    relevant: list[set[str]] = []
    recalls: list[float] = []
    precisions: list[float] = []
    ndcgs: list[float] = []
    citation_precisions: list[float] = []
    citation_recalls: list[float] = []
    abstentions: list[float] = []
    latencies: list[float] = []
    for example in examples:
        prediction = by_id[example.id]
        expected_docs = set(example.relevant_document_ids)
        ranking = prediction.retrieved_document_ids
        ranked.append(ranking)
        relevant.append(expected_docs)
        recalls.append(recall_at_k(ranking, expected_docs, k))
        precisions.append(precision_at_k(ranking, expected_docs, k))
        ndcgs.append(ndcg_at_k(ranking, expected_docs, k))
        citations = citation_scores(prediction.citation_ids, example.expected_citation_ids)
        citation_precisions.append(citations.precision)
        citation_recalls.append(citations.recall)
        expected_abstention = not example.relevant_document_ids
        actual_abstention = not prediction.answer.strip()
        abstentions.append(float(expected_abstention == actual_abstention))
        latencies.append(prediction.latency_ms)

    latency = latency_summary(latencies)
    return EvaluationReport(
        examples=len(examples),
        recall_at_k=statistics.fmean(recalls),
        precision_at_k=statistics.fmean(precisions),
        mrr=mean_reciprocal_rank(ranked, relevant),
        ndcg_at_k=statistics.fmean(ndcgs),
        citation_precision=statistics.fmean(citation_precisions),
        citation_recall=statistics.fmean(citation_recalls),
        abstention_accuracy=statistics.fmean(abstentions),
        latency_p95_ms=latency.p95_ms,
    )


def assert_no_regression(
    candidate: EvaluationReport,
    baseline: EvaluationReport,
    *,
    quality_tolerance: float = 0.02,
    latency_tolerance: float = 0.20,
) -> None:
    quality_metrics = (
        "recall_at_k",
        "precision_at_k",
        "mrr",
        "ndcg_at_k",
        "citation_precision",
        "citation_recall",
        "abstention_accuracy",
    )
    regressions = [
        metric
        for metric in quality_metrics
        if getattr(candidate, metric) + quality_tolerance < getattr(baseline, metric)
    ]
    if (
        baseline.latency_p95_ms > 0
        and candidate.latency_p95_ms
        > baseline.latency_p95_ms * (1.0 + latency_tolerance)
    ):
        regressions.append("latency_p95_ms")
    if regressions:
        raise AssertionError(f"Evaluation regression detected: {', '.join(regressions)}")


__all__ = [
    "EvaluationPrediction",
    "EvaluationReport",
    "GoldenExample",
    "assert_no_regression",
    "evaluate_predictions",
    "load_golden_dataset",
]
