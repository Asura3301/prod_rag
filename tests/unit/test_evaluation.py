import pytest

from rag_app.evaluation import (
    EvaluationPrediction,
    EvaluationReport,
    assert_no_regression,
    citation_scores,
    evaluate_predictions,
    latency_summary,
    load_golden_dataset,
    mean_reciprocal_rank,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)


def test_retrieval_metrics() -> None:
    retrieved = ["a", "x", "b"]
    relevant = {"a", "b"}
    assert recall_at_k(retrieved, relevant, 2) == 0.5
    assert precision_at_k(retrieved, relevant, 2) == 0.5
    expected_ndcg = (1 + 1 / 2) / (1 + 1 / 1.584962500721156)
    assert ndcg_at_k(retrieved, relevant, 3) == pytest.approx(expected_ndcg)


def test_mrr_and_citations() -> None:
    assert mean_reciprocal_rank([["x", "a"], ["b"]], [{"a"}, {"b"}]) == 0.75
    scores = citation_scores(["a", "wrong"], ["a", "b"])
    assert scores.precision == 0.5
    assert scores.recall == 0.5


def test_latency_summary() -> None:
    summary = latency_summary([10, 20, 30, 40])
    assert summary.count == 4
    assert summary.mean_ms == 25
    assert summary.median_ms == 25
    assert summary.p95_ms == pytest.approx(38.5)
    assert summary.max_ms == 40


def test_invalid_k_is_rejected() -> None:
    with pytest.raises(ValueError):
        recall_at_k([], set(), 0)


def test_golden_runner_and_regression_gate(tmp_path) -> None:
    dataset = tmp_path / "golden.jsonl"
    dataset.write_text(
        '{"id":"q1","query":"policy","relevant_document_ids":["d1"],'
        '"expected_citation_ids":["c1"],"reference_answer":"answer"}\n',
        encoding="utf-8",
    )
    examples = load_golden_dataset(dataset)
    prediction = EvaluationPrediction(
        example_id="q1",
        retrieved_document_ids=("d1",),
        citation_ids=("c1",),
        answer="answer",
        latency_ms=10,
    )
    report = evaluate_predictions(examples, [prediction], k=1)
    assert report.recall_at_k == 1.0
    assert report.citation_precision == 1.0
    assert_no_regression(report, report)

    regressed = EvaluationReport(
        examples=1,
        recall_at_k=0.0,
        precision_at_k=0.0,
        mrr=0.0,
        ndcg_at_k=0.0,
        citation_precision=0.0,
        citation_recall=0.0,
        abstention_accuracy=0.0,
        latency_p95_ms=100,
    )
    with pytest.raises(AssertionError):
        assert_no_regression(regressed, report)
