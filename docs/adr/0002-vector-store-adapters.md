# ADR-0002: Qdrant and pgvector adapters

## Status

Accepted.

## Decision

The domain and retrieval layers depend on a small asynchronous `VectorStore`
contract. The template ships two production adapters:

- Qdrant is the search-focused profile. It stores named `dense` and `sparse`
  vectors, performs payload filtering before scoring, and supports sparse/dense
  candidate generation.
- PostgreSQL with pgvector is the consolidated profile. It stores dense vectors
  with HNSW and lexical terms with a generated `tsvector` plus GIN index.

The in-memory exact-cosine adapter is only a deterministic development and test
baseline. Adapter conformance tests require stable IDs, filtering semantics,
upsert/delete behavior, and ranked results; raw backend scores are not expected
to match.

## Consequences

Application code remains backend-neutral, while backend-specific schema and
search optimizations remain inside adapters. Qdrant requires an additional
service. pgvector reduces operational components but shares database resources
with metadata, jobs, and LangGraph checkpoints. Teams must benchmark both with
their own corpus rather than infer quality or capacity from this template.
