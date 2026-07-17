# Getting Started

This guide takes you from a clean Windows machine to a verified local RAG demo. Run commands
from the repository root (`*\prod_RAG`) in PowerShell unless a section says otherwise.

## 1. Understand the two operating modes

The repository currently has two distinct usage levels:

1. **Runnable demo:** FastAPI, direct text documents, in-memory search, health checks, API
   authentication, Redis jobs, loader/chunking CLI, and deterministic local retrieval examples.
2. **Production building blocks:** Qdrant, pgvector, provider embeddings, hybrid retrieval,
   reranking, LangGraph, PostgreSQL checkpoints, and OpenTelemetry. These components work through
   their Python interfaces and tests, but are not yet automatically composed into the default API.

Starting a Qdrant or pgvector Compose profile starts that database; it does not change the default
API from `InMemoryRAGService`. See [configuration](configuration.md#runtime-wiring-status).

## 2. Prerequisites

Install:

- Git.
- Python 3.12 or newer.
- `uv`, the Python project and environment manager.
- Docker Desktop only if you want Redis, Qdrant, pgvector, or containerized startup.
- Optional: LibreOffice for legacy `.doc` ingestion.

Verify:

```powershell
git --version
python --version
docker --version
docker compose version
```

The Python result must be `3.12` or newer. Docker commands are optional for the first local demo.

### Install uv on Windows

The official standalone installer is:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close and reopen PowerShell, then verify:

```powershell
uv --version
```

`uv` can install a suitable Python interpreter if necessary:

```powershell
uv python install 3.12
```

## 3. Install the project

Open the repository:

```powershell
Set-Location *your-path*\prod_RAG
```

Install the locked base and development dependencies:

```powershell
uv sync
```

Optional features are not installed by default. Install only what you need:

```powershell
# Qdrant client
uv sync --extra qdrant

# PostgreSQL/pgvector client and LangGraph PostgreSQL checkpoint support
uv sync --extra pgvector

# Local Hugging Face embeddings and cross-encoder reranking
uv sync --extra local-models

# OpenTelemetry, LangSmith, and Prometheus client libraries
uv sync --extra observability

# Everything
uv sync --all-extras
```

Confirm the CLI:

```powershell
uv run rag --help
```

## 4. Create local configuration

Copy the template:

```powershell
Copy-Item .env.example .env
```

For the first demo, the model and vector-store settings can remain unchanged because the default
API does not call a hosted LLM or embedding provider.

Important: `Settings()` loads `.env`, but the current default API factory directly reads selected
process environment variables. Set tokens in the current PowerShell session before starting it:

```powershell
$env:RAG_API_TOKEN = "local-api-token"
$env:RAG_ADMIN_TOKEN = "local-admin-token"
```

Generate high-entropy production tokens instead of using these examples:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Never commit `.env`.

## 5. Start the local API

```powershell
uv run rag serve --reload
```

Expected address: `http://localhost:8000`.

Use `--reload` only during development. Stop the server with `Ctrl+C`.

Open:

- Swagger UI: <http://localhost:8000/docs>
- ReDoc: <http://localhost:8000/redoc>
- OpenAPI JSON: <http://localhost:8000/openapi.json>

## 6. Verify health

Open a second PowerShell window:

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
```

Both should report an available service.

## 7. Add and query a document

```powershell
$headers = @{ Authorization = "Bearer local-api-token" }

$document = Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/api/v1/documents `
  -Headers $headers `
  -ContentType "application/json" `
  -Body (@{
    source = "memory://handbook"
    text = "Refund requests are accepted within 30 days of purchase."
    metadata = @{ category = "policy"; tenant_id = "demo" }
  } | ConvertTo-Json)

$document
```

Query it:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/api/v1/query `
  -Headers $headers `
  -ContentType "application/json" `
  -Body (@{
    query = "How long do I have to request a refund?"
    top_k = 5
    filters = @{ category = "policy" }
  } | ConvertTo-Json -Depth 4)
```

The response contains `answer`, `citations`, and `latency_ms`. Data is lost when this API process
restarts because this mode is intentionally in memory.

Run the packaged client instead:

```powershell
$env:RAG_API_URL = "http://localhost:8000"
$env:RAG_API_TOKEN = "local-api-token"
uv run python examples/api_client.py
```

Or query with the CLI:

```powershell
uv run rag query "What is the refund policy?" --api-token local-api-token
```

## 8. Run the standalone indexing and retrieval example

This example exercises dense hashing, indexing, BM25-style lexical retrieval, reciprocal-rank
fusion, metadata filtering, and retrieval diagnostics without external services:

```powershell
uv run python examples/local_pipeline.py
```

Hash embeddings are deterministic test features, not a semantic production embedding model.

## 9. Test file ingestion

The ingestion CLI validates, loads, and chunks a file or directory. It does not insert the chunks
into the running API or a vector database.

```powershell
uv run rag ingest .\path\to\document.pdf
uv run rag ingest .\path\to\document-directory
```

The command prints counts and one result per source. A nonzero process exit code means at least one
item failed.

Supported routes include PDF, modern and legacy Office documents, text/Markdown/source files,
CSV, JSON, HTML/XHTML, and directories. Exact extension routing and optional system requirements
are in the [usage guide](usage.md#document-loaders-and-ingestion).

## 10. Start with Docker Compose

Make sure Docker Desktop is running:

```powershell
docker info
```

Set local Compose values in the current terminal:

```powershell
$env:RAG_API_TOKEN = "local-api-token"
$env:RAG_ADMIN_TOKEN = "local-admin-token"
$env:POSTGRES_PASSWORD = "local-postgres-password"
```

### API and Redis

```powershell
docker compose up --build -d
docker compose ps
docker compose logs -f api
```

Press `Ctrl+C` to leave log viewing; detached containers continue running.

### API, Redis, and worker

```powershell
docker compose --profile workers up --build -d
```

### Add pgvector

```powershell
docker compose --profile workers --profile pgvector up --build -d
```

### Add Qdrant

```powershell
docker compose --profile workers --profile qdrant up --build -d
```

Choose one vector backend for an application deployment. Both can be started for adapter testing,
but neither is automatically attached to the default API.

Only the API port is published by the provided Compose file. Qdrant and PostgreSQL are reachable
between containers, not directly from Windows. To run host-side integration tests, add temporary
port mappings or run the tests inside a container.

### Stop containers

```powershell
docker compose --profile workers --profile pgvector --profile qdrant down
```

Keep persisted data by using `down` as shown. Delete local volumes and all their data only when
intentional:

```powershell
docker compose --profile workers --profile pgvector --profile qdrant down --volumes
```

## 11. Run quality checks

```powershell
# Fast unit tests; configured to exclude slow, integration, and legacy tests
uv run pytest -q

# Lint
uv run ruff check src tests scripts examples

# Type check
uv run mypy src/rag_app

# Build wheel and source distribution
uv build
```

Run formatting only when you intend to modify files:

```powershell
uv run ruff format src tests scripts examples
```

## 12. Next steps

1. Read [configuration](configuration.md) and select a model, embeddings, and vector store.
2. Follow [usage](usage.md) for every CLI/API route, worker, adapter, evaluation, and telemetry.
3. Use [troubleshooting](troubleshooting.md) if startup or loader checks fail.
4. Before exposing the service, complete the production-wiring and security checklist.

## Official documentation

- [uv installation](https://docs.astral.sh/uv/getting-started/installation/)
- [uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/)
- [Docker Compose profiles](https://docs.docker.com/compose/how-tos/profiles/)
- [FastAPI first steps and interactive docs](https://fastapi.tiangolo.com/tutorial/first-steps/)
- [Uvicorn/FastAPI manual deployment](https://fastapi.tiangolo.com/deployment/manually/)
- [Redis on Docker](https://redis.io/docs/latest/operate/oss_and_stack/install/install-stack/docker/)
- [Qdrant local quickstart](https://qdrant.tech/documentation/quickstart/)
- [pgvector project documentation](https://github.com/pgvector/pgvector)
