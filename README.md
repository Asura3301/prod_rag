# Production RAG Template

A production-oriented retrieval-augmented generation service with explicit boundaries for
ingestion, retrieval/generation, asynchronous work, evaluation, and operations. The API and
worker adapters are dependency-injectable; local development and tests use deterministic,
in-memory implementations and require no infrastructure.

## Quickstart

Python 3.12 is required.

```powershell
uv sync
$env:RAG_API_TOKEN = "local-api-token"
$env:RAG_ADMIN_TOKEN = "local-admin-token"
uv run rag serve --reload
```

Open `http://localhost:8000/docs`, or verify the service:

```bash
curl http://localhost:8000/health/ready
curl -X POST http://localhost:8000/api/v1/documents \
  -H "Authorization: Bearer local-api-token" \
  -H "Content-Type: application/json" \
  -d '{"source":"memory://handbook","text":"Refunds are available within thirty days."}'
curl -X POST http://localhost:8000/api/v1/query \
  -H "Authorization: Bearer local-api-token" \
  -H "Content-Type: application/json" \
  -d '{"query":"What is the refund policy?","top_k":5}'
```

The default service is intentionally ephemeral. Inject production retrieval and queue adapters
through `create_app(service=..., queue=...)`.

## Documentation

Start here:

- [Getting started](docs/getting-started.md): Windows-first installation, first API query,
  ingestion, Docker profiles, and verification.
- [Configuration reference](docs/configuration.md): every environment variable, provider recipe,
  Compose setting, and current runtime-wiring status.
- [Usage guide](docs/usage.md): CLI, all HTTP routes, loaders, worker, Qdrant, pgvector, hybrid
  retrieval, evaluation, telemetry, and runnable examples.
- [Troubleshooting](docs/troubleshooting.md): startup, authentication, Docker, Redis, vector-store,
  loader, telemetry, and test failures.

The default API is a functional in-memory demo. Optional production adapters are implemented but
must be explicitly composed into a persistent `RAGService`; enabling a database Compose profile
alone does not change the API backend.

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
[security](docs/security.md), [evaluation](docs/evaluation.md), and the
[complete usage guide](docs/usage.md). Runnable examples are available under `examples/`.

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

## LICENSE

[MIT](LICENSE)

Contributions follow [CONTRIBUTING.md](CONTRIBUTING.md). Report vulnerabilities according to
[SECURITY.md](SECURITY.md).