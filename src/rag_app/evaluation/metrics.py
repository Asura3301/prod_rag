"""Dependency-free retrieval, citation, and latency metrics."""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


def recall_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    _validate_k(k)
    if not relevant:
        return 1.0
    return len(set(retrieved[:k]) & relevant) / len(relevant)


def precision_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    _validate_k(k)
    considered = retrieved[:k]
    if not considered:
        return 0.0
    return sum(item in relevant for item in considered) / len(considered)


def mean_reciprocal_rank(results: Iterable[Sequence[str]], relevant: Iterable[set[str]]) -> float:
    pairs = list(zip(results, relevant, strict=True))
    if not pairs:
        return 0.0
    scores = []
    for ranking, expected in pairs:
        rank = next((index for index, item in enumerate(ranking, 1) if item in expected), None)
        scores.append(1 / rank if rank else 0.0)
    return statistics.fmean(scores)


def ndcg_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    _validate_k(k)
    gains = [1.0 if item in relevant else 0.0 for item in retrieved[:k]]
    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal_count = min(len(relevant), k)
    ideal = sum(1 / math.log2(index + 2) for index in range(ideal_count))
    return dcg / ideal if ideal else 1.0


@dataclass(frozen=True)
class CitationScores:
    precision: float
    recall: float


def citation_scores(predicted: Iterable[str], expected: Iterable[str]) -> CitationScores:
    predicted_set, expected_set = set(predicted), set(expected)
    precision = (
        len(predicted_set & expected_set) / len(predicted_set)
        if predicted_set
        else float(not expected_set)
    )
    recall = (
        len(predicted_set & expected_set) / len(expected_set)
        if expected_set
        else 1.0
    )
    return CitationScores(precision=precision, recall=recall)


@dataclass(frozen=True)
class LatencySummary:
    count: int
    mean_ms: float
    median_ms: float
    p95_ms: float
    max_ms: float


def latency_summary(latencies_ms: Iterable[float]) -> LatencySummary:
    values = sorted(float(value) for value in latencies_ms)
    if not values:
        return LatencySummary(0, 0.0, 0.0, 0.0, 0.0)
    if any(value < 0 for value in values):
        raise ValueError("latencies cannot be negative")
    return LatencySummary(
        count=len(values),
        mean_ms=statistics.fmean(values),
        median_ms=statistics.median(values),
        p95_ms=_percentile(values, 0.95),
        max_ms=values[-1],
    )


def _percentile(values: Sequence[float], quantile: float) -> float:
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    fraction = position - lower
    return values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * fraction


def _validate_k(k: int) -> None:
    if k < 1:
        raise ValueError("k must be at least one")
