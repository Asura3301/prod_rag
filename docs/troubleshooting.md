# Troubleshooting

Run commands from the repository root in PowerShell.

## Quick diagnostic sequence

```powershell
python --version
uv --version
uv sync
uv run rag --help
uv run pytest -q
```

For containers:

```powershell
docker info
docker compose config
docker compose ps
docker compose logs api
docker compose logs redis
```

## `uv` is not recognized

Close and reopen PowerShell after installing `uv`. Verify whether its installation directory is on
`PATH`:

```powershell
Get-Command uv -ErrorAction SilentlyContinue
```

Reinstall using the official command:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Official reference: [Installing uv](https://docs.astral.sh/uv/getting-started/installation/).

## Python version is unsupported

The project requires Python 3.12 or newer:

```powershell
uv python install 3.12
uv sync --python 3.12
```

## `rag` command is not found

Use it through the project environment:

```powershell
uv sync
uv run rag --help
```

Do not expect a globally installed `rag` executable.

## Port 8000 is already in use

Find the owning process:

```powershell
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue |
  Select-Object LocalAddress, LocalPort, State, OwningProcess
```

Use another port:

```powershell
uv run rag serve --port 8080
```

Then set clients to `http://localhost:8080`.

For Compose:

```powershell
$env:API_PORT = "8080"
docker compose up -d
```

## API returns `401 invalid API token`

The token in `Authorization` must exactly match the server process variable:

```powershell
$env:RAG_API_TOKEN = "local-api-token"
uv run rag serve
```

Client:

```powershell
$headers = @{ Authorization = "Bearer local-api-token" }
Invoke-RestMethod http://localhost:8000/api/v1/documents -Headers $headers
```

If you changed the environment variable, restart the API. `.env` is not automatically read by the
current API factory for tokens; export them in the process or configure Compose.

## `/metrics` or `/debug/state` returns `503`

Administrative endpoints are intentionally disabled when no admin token is visible to the API.

```powershell
$env:RAG_ADMIN_TOKEN = "local-admin-token"
uv run rag serve
```

Then send `Authorization: Bearer local-admin-token`. Restart after changing the variable.

## API returns `429`

The in-process default limit is 120 `/api/*` requests per client per minute. Respect the
`Retry-After` header. For custom limits, construct the app with `rate_limit_per_minute`.

This limiter is process-local; use an ingress or distributed limiter for multi-replica production.

## API returns `413 request body is too large`

The default body limit is 10 MiB based on `Content-Length`. Direct text documents must fit inside
that limit. Configure a different `max_body_bytes` in the app factory and enforce the same or
stricter limit at the ingress.

## Data disappears after restart

The default `InMemoryRAGService`, `InMemoryVectorStore`, and `InMemoryJobQueue` are ephemeral.
Starting Qdrant or pgvector alone does not rewire the API.

For persistent behavior, implement the composition described in
[configuration](configuration.md#runtime-wiring-status): persistent document registry,
embedding/indexing service, selected vector store, hybrid retriever, and a `RAGService` adapter.

## The worker succeeds but the API cannot answer from the file

This is a current architectural limitation, not a timing problem. The worker validates, loads, and
chunks, but does not embed/index its result or update the API’s in-memory document service.

Use `POST /api/v1/documents` for the current end-to-end demo. Complete worker-to-index composition
before expecting ingestion jobs to become queryable.

## Containerized worker cannot find a Windows file

The worker sees the container filesystem, not arbitrary host paths. The provided Compose file does
not mount a document directory.

For local experiments, add a controlled read-only bind mount to both the component that creates
the job and the worker, then submit the container path. For production, use authenticated object
storage and pass an object identifier rather than a host path.

## Docker cannot connect to `dockerDesktopLinuxEngine`

Start Docker Desktop and wait until the engine reports ready:

```powershell
docker info
```

If it still fails, check that Docker Desktop is using Linux containers and that the current user
can access the Docker engine. This is an environment problem; rebuilding Python dependencies will
not fix it.

## Compose profile service does not start

Services with profiles are disabled unless the profile is selected:

```powershell
docker compose --profile workers --profile qdrant up -d
docker compose --profile workers --profile pgvector up -d
```

Inspect:

```powershell
docker compose --profile workers --profile qdrant config --services
docker compose --profile workers --profile qdrant ps
```

Official reference: [Docker Compose profiles](https://docs.docker.com/compose/how-tos/profiles/).

## Cannot connect to Qdrant on `localhost:6333`

The provided Compose service does not publish Qdrant to the host. `http://qdrant:6333` works only
from another container on the Compose network.

For host-side tests, run the host-published command in [usage](usage.md#qdrant-adapter) or add a
temporary port mapping:

```yaml
ports:
  - "6333:6333"
```

Check:

```powershell
Test-NetConnection localhost -Port 6333
```

Official reference: [Qdrant local quickstart](https://qdrant.tech/documentation/quickstart/).

## Qdrant reports a vector dimension mismatch

A collection has a fixed dense-vector dimension. The embedding provider and collection must agree.
Create a new versioned collection or intentionally recreate a disposable test collection.

Never use `recreate=True` against production data unless deletion is explicitly intended.

## Cannot connect to PostgreSQL on `localhost:5432`

The provided Compose PostgreSQL service is not published to Windows. Inside Compose, use hostname
`postgres`. For host-side integration testing, follow the host-published `docker run` example in
[usage](usage.md#pgvector-adapter) or add a temporary `5432:5432` mapping.

Check:

```powershell
Test-NetConnection localhost -Port 5432
```

## PostgreSQL says extension `vector` is unavailable

Use a PostgreSQL image that includes pgvector, such as the project’s `pgvector/pgvector:pg16`.
The adapter executes:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

The database user needs permission to enable the extension. Official reference:
[pgvector](https://github.com/pgvector/pgvector).

## pgvector query reports a dimension mismatch

The table column is created as `VECTOR(n)`. Use the same embedding model/dimension for creation,
indexing, and querying. A model change requires a new table/index or controlled migration.

## Redis connection refused

Check the service:

```powershell
docker compose up redis -d
docker compose ps redis
docker compose exec redis redis-cli ping
```

Expected result: `PONG`.

Host worker:

```powershell
$env:REDIS_URL = "redis://localhost:6379/0"
uv run python scripts/run_worker.py
```

Container worker uses `redis://redis:6379/0`.

Official reference: [Run Redis on Docker](https://redis.io/docs/latest/operate/oss_and_stack/install/install-stack/docker/).

## Job is repeatedly retried or dead-lettered

Inspect its `error` field:

```powershell
Invoke-RestMethod `
  -Uri "http://localhost:8000/api/v1/ingestion/jobs/JOB_ID" `
  -Headers @{ Authorization = "Bearer local-api-token" }
```

Common causes are an inaccessible source path, unsupported extension, file-size limit, missing
LibreOffice, parser failure, or malformed content. The worker retries immediately up to three
attempts; deterministic failures then enter `dead_letter`.

Correct the cause and submit a reviewed new idempotency key. Do not blindly replay poison jobs.

## Unsupported file extension

Only extensions listed in [usage](usage.md#document-loaders-and-ingestion) are routed. Renaming a
file does not convert it and can trigger MIME mismatch validation. Convert the file to a supported
format or implement and test a new loader route.

## `libmagic` cannot be found on Windows

Synchronize dependencies so the Windows-specific `python-magic-bin` package is installed:

```powershell
uv sync
```

Restart the terminal or Python process after installation.

## `soffice` command was not found

Install LibreOffice, locate `soffice.exe`, and add its directory to `PATH`. Verify:

```powershell
soffice --version
```

Legacy `.doc`, `.ppt`, or `.xls` processing may need LibreOffice. Modern `.docx`, `.pptx`, and
`.xlsx` files use the Docling route.

## Docling rejects `.ipynb`

This project intentionally routes `.ipynb` through the plain-text loader because the installed
Docling route does not accept it. The notebook is read as JSON text. Add a notebook-specific
loader if cell-aware metadata is required.

## JSON loader returns no documents or a jq error

The default jq schema is:

```text
.[].content
```

Use input shaped as an array of objects with `content`, or construct `JSONDocumentLoader` with the
correct schema. Install/synchronize the `jq` Python dependency:

```powershell
uv sync
```

## Docling or local embedding first run is slow

Model artifacts can be downloaded and initialized on first use. Check network access, available
disk space, and model cache permissions. For controlled deployment, pre-download approved models
into the image or a managed cache and pin model revisions.

## OpenTelemetry produces no traces

Verify:

```powershell
uv sync --extra observability
$env:OTEL_SERVICE_NAME = "prod-rag-api"
$env:OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4318/v1/traces"
uv run rag serve
```

The endpoint must be reachable from the API process. In a container, `localhost` points to that
container, not the host or another Compose service.

Tracing setup is intentionally optional; missing instrumentation packages do not stop the API.

## Tests unexpectedly skip integrations

The default pytest options exclude `slow`, `integration`, and `legacy`. Select them explicitly and
provide service variables:

```powershell
$env:RAG_TEST_QDRANT_URL = "http://localhost:6333"
$env:RAG_TEST_PGVECTOR_DSN = "postgresql://rag:password@localhost:5432/rag"
uv run pytest -q -m integration
```

## Ruff or MyPy fails

Run each tool directly:

```powershell
uv run ruff check src tests scripts examples
uv run ruff format --check src tests scripts examples
uv run mypy src/rag_app
```

Apply safe Ruff fixes:

```powershell
uv run ruff check --fix src tests scripts examples
uv run ruff format src tests scripts examples
```

Review changes before committing.

## Where to ask for more evidence

When reporting a problem, include:

- The exact command.
- Full error text.
- Python and `uv` versions.
- Host versus container execution.
- Relevant service status from `docker compose ps`.
- The loader extension or API route.
- Redacted configuration names, never secrets or document content.
