# Production RAG

A production-oriented retrieval-augmented generation service with explicit boundaries for
ingestion, retrieval/generation, asynchronous work, evaluation, and operations. The API and
worker adapters are dependency-injectable; local development and tests use deterministic,
in-memory implementations and require no infrastructure.

## Quickstart

Python 3.12 is required.

```bash
uv sync
copy .env.example .env  # Windows; use: cp .env.example .env
uv run rag serve --reload
```

Open `http://localhost:8000/docs`, or verify the service:

```bash
curl http://localhost:8000/health/ready
curl -X POST http://localhost:8000/api/v1/documents \
  -H "Authorization: Bearer change-me" \
  -H "Content-Type: application/json" \
  -d '{"source":"memory://handbook","text":"Refunds are available within thirty days."}'
curl -X POST http://localhost:8000/api/v1/query \
  -H "Authorization: Bearer change-me" \
  -H "Content-Type: application/json" \
  -d '{"query":"What is the refund policy?","top_k":5}'
```

The default service is intentionally ephemeral. Inject production retrieval and queue adapters
through `create_app(service=..., queue=...)`.

## Architecture and profiles

- Ingestion: validated local/web sources, Docling for layout-rich documents,
  Unstructured for legacy formats, specialized CSV/JSON loaders, deterministic
  chunk IDs, hashes, versions, and idempotent registry behavior.
- Indexing: cached/batched dense embeddings plus optional sparse embeddings.
  Qdrant uses named dense/sparse vectors; pgvector uses HNSW plus PostgreSQL
  full-text search.
- Retrieval: `fast` (dense), `balanced` (dense + lexical + RRF), and `quality`
  (hybrid + reranker + neighboring context) profiles with ACL filters applied
  before ranking.
- Generation: a bounded LangGraph workflow that retries retrieval at most once,
  validates citations, abstains without evidence, and accepts a production
  Postgres checkpointer.
- Operations: FastAPI, SSE, Redis-backed jobs, request IDs, API/admin tokens,
  rate limiting, structured logs, Prometheus-compatible metrics, and optional
  OpenTelemetry export.

The primary adapters are Qdrant and PostgreSQL/pgvector. The deterministic
in-memory store and hash embeddings exist for tests and demos; they are not
semantic production substitutes. See [architecture](docs/architecture.md) and
the ADRs under `docs/adr/`.

## Containers

```bash
# API + Redis (set non-default API/admin tokens outside local development)
docker compose up --build

# Include worker and choose one vector store
docker compose --profile workers --profile pgvector up --build
docker compose --profile workers --profile qdrant up --build
```

Set `RAG_ADMIN_TOKEN` before exposing the service. `/metrics` and `/debug/state` return 503 when
no token is configured and require `Authorization: Bearer <token>` otherwise.

## API

- `POST /api/v1/ingestion/jobs`; `GET/DELETE /api/v1/ingestion/jobs/{id}`
- `POST/GET /api/v1/documents`; `GET/DELETE /api/v1/documents/{id}`
- `POST /api/v1/query`; `POST /api/v1/query/stream` (SSE)
- `GET /health/live`; `GET /health/ready`
- protected `GET /metrics`; protected `GET /debug/state`

See [architecture](docs/architecture.md), [deployment](docs/deployment.md),
[security](docs/security.md), and [evaluation](docs/evaluation.md). A standard-library client is
available at `examples/api_client.py`.

## Evaluation and tests

The evaluation package includes Recall@k, Precision@k, MRR, nDCG@k, citation precision/recall,
and latency summaries. The starter golden set is `evals/golden_dataset.jsonl`.

```bash
uv run pytest -q
uv run ruff check src tests scripts examples
uv run mypy src/rag_app
```

Quality changes should be evaluated separately from ordinary unit tests.
`rag_app.evaluation` loads JSONL golden data, computes retrieval/citation/
latency reports, and enforces candidate-versus-baseline tolerances. The starter
dataset is intentionally small and must be replaced or extended with reviewed,
domain-specific cases before making quality claims.

## Project status

The repository is a production-oriented template, not a claim that one model or
retrieval profile is universally state of the art. Publish corpus provenance,
model/index versions, hardware, latency, cost, and quality metrics before making
comparative claims. The default service is safe for local smoke tests but is
not persistent production storage.

Contributions follow [CONTRIBUTING.md](CONTRIBUTING.md). Report vulnerabilities according to
[SECURITY.md](SECURITY.md).
