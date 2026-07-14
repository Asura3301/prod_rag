# Evaluation

Evaluation is a release gate, not only an offline report. Each golden record contains a query,
relevant document IDs, expected citation IDs, and a reference answer.

## Metrics

- Recall@k measures relevant-document coverage.
- Precision@k measures retrieval concentration.
- MRR rewards an early first relevant result.
- nDCG@k measures ranking quality with logarithmic position discount.
- Citation precision and recall detect unsupported or missing citations.
- Mean, median, p95, and maximum latency expose service performance.

Report metrics by corpus, tenant, language, and query category where sample size permits. Do not
compare scores across changed corpora without versioning the dataset and index configuration.

## Release process

1. Add reviewed failure cases to `evals/golden_dataset.jsonl`; never tune only against the test
   set.
2. Run retrieval and citation evaluation against the candidate index.
3. Compare to the pinned baseline with confidence intervals for sufficiently large samples.
4. Block releases on material quality regression, excessive p95 latency, or unsupported answers.
5. Sample production feedback after redaction and human review, then promote suitable examples
   into a separate evaluation split.

The metric implementation is in `src/rag_app/evaluation/metrics.py`.
`src/rag_app/evaluation/runner.py` loads versioned JSONL examples, requires one
prediction per example, produces a serializable report, and compares a
candidate with a pinned baseline using explicit quality and p95-latency
tolerances. Empty-relevance behavior is explicit: retrieval recall and nDCG are
1.0 when nothing is relevant; empty retrieved precision is 0.0 unless citation
prediction and expectation are both empty.

Do not run hosted LLM judges on every pull request by default. Use deterministic
retrieval/citation gates in PR CI and a scheduled or manually approved workflow
for expensive groundedness/correctness judges. Fix the judge model and rubric,
retain calibration examples, cap spend, and periodically compare judge results
with human review.
