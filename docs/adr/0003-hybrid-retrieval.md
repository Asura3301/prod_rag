# ADR-0003: Hybrid retrieval with rank fusion and reranking

## Status

Accepted.

## Decision

Balanced and quality profiles retrieve dense and lexical/sparse candidates with
tenant and ACL filters applied in both branches. Candidate lists are merged
with Reciprocal Rank Fusion (RRF, default rank constant 60). We do not average
raw dense and lexical scores because their scales and distributions differ.

The quality profile optionally applies a cross-encoder reranker, then expands
adjacent or parent chunks, removes duplicate content, and enforces a final
context budget. The fast profile uses dense retrieval only.

## Consequences

Each stage emits diagnostics and can be evaluated independently. RRF is a
robust default, not a universal optimum; candidate limits, the rank constant,
reranker, and context budget are configuration subject to golden-dataset
evaluation. ACL and tenant filters must never be deferred until after ranking.

References:

- Qdrant hybrid queries: https://qdrant.tech/documentation/search/hybrid-queries/
- Cormack, Clarke, and Buettcher, Reciprocal Rank Fusion, 2009:
  https://doi.org/10.1145/1571941.1572114
