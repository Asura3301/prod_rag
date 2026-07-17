# Troubleshooting

Run commands from the repository root in PowerShell.

## Recommended diagnostic sequence

```powershell
python --version
uv --version
uv sync --extra qdrant --extra local-models
docker info
docker compose --profile qdrant up -d redis qdrant
docker compose --profile qdrant ps
uv run rag doctor
```

The expected local endpoints are:

- LM Studio: `http://127.0.0.1:1234/v1`
- Redis: `127.0.0.1:6379`
- Qdrant: `http://127.0.0.1:6333`
- API: `http://127.0.0.1:8000`

The expected local persistence paths are `data/documents` for allowed ingestion and
`.data/rag.sqlite3` for document metadata.

## `uv` or `rag` is not recognized

Install uv, reopen PowerShell, and run the CLI through the project environment:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv sync --extra qdrant --extra local-models
uv run rag --help
```

The project requires Python 3.12 or newer:

```powershell
uv python install 3.12
uv sync --python 3.12 --extra qdrant --extra local-models
```

## `rag doctor` reports `sentence-transformers` missing

Install the local-model extra:

```powershell
uv sync --extra qdrant --extra local-models
uv run rag doctor
```

Running only `uv sync` does not install this optional dependency.

## `rag doctor` reports an LM Studio failure

Confirm that LM Studio is running its local server:

```powershell
Invoke-RestMethod http://127.0.0.1:1234/v1/models
```

Check all three details:

1. The server listens on `127.0.0.1:1234`.
2. `.env` contains `RAG_LLM__BASE_URL=http://127.0.0.1:1234/v1`.
3. The `/models` response includes the exact identifier `gpt-oss-20b`, matching
   `RAG_LLM__MODEL=gpt-oss-20b`.

Downloading a model in LM Studio is not enough; load it and start the server. If LM Studio exposes
a different model identifier, either serve it as `gpt-oss-20b` or set the exact advertised name in
`.env`.

## First startup appears stuck at BGE loading

`rag serve` and `rag worker` each construct the local service. On first use, Sentence Transformers
may download and initialize `BAAI/bge-m3`; this can take time.

Check:

- network access to the model host;
- free disk space;
- write permission for the model cache;
- proxy or certificate policy;
- whether another Python process is already downloading the same model.

Wait for the first download to finish before treating startup as failed. In controlled
environments, pre-download an approved, pinned model revision into a managed cache.

`rag doctor` only verifies that Sentence Transformers is installed; it does not download or embed
with BGE-M3.

## Embedding dimension mismatch

The documented model is `BAAI/bge-m3`, which produces 1024-dimensional vectors:

```dotenv
RAG_EMBEDDINGS__MODEL=BAAI/bge-m3
RAG_EMBEDDINGS__DIMENSIONS=1024
```

Service startup validates the real embedding length. Qdrant collections also have a fixed vector
dimension. If the collection was previously created with another model or dimension, choose a new
`RAG_VECTOR_STORE__INDEX_NAME` or intentionally reset disposable local data.

Do not delete or recreate a production collection to solve this error without an approved backup
and reindex plan.

## Redis connection refused

Start and inspect Redis:

```powershell
docker compose --profile qdrant up -d redis qdrant
docker compose ps redis
docker compose exec redis redis-cli ping
Test-NetConnection 127.0.0.1 -Port 6379
```

Expected Redis response: `PONG`.

For the host API and worker:

```dotenv
RAG_REDIS_URL=redis://127.0.0.1:6379/0
```

The Compose file publishes Redis on loopback. If `REDIS_PORT` changes the host port, update
`RAG_REDIS_URL` to match.

## Cannot connect to Qdrant

Start the profile and test its loopback host port:

```powershell
docker compose --profile qdrant up -d redis qdrant
docker compose --profile qdrant ps qdrant
Test-NetConnection 127.0.0.1 -Port 6333
Invoke-RestMethod http://127.0.0.1:6333/collections
```

Host configuration:

```dotenv
RAG_VECTOR_STORE__URL=http://127.0.0.1:6333
```

If `QDRANT_PORT` changes the host port, update this URL. `http://qdrant:6333` is for another
container on the Compose network and does not resolve from the Windows host.

## Docker cannot connect

Start Docker Desktop and wait for the Linux container engine:

```powershell
docker info
```

If it still fails, verify that Docker Desktop is using Linux containers and that the current user
can access the engine. Reinstalling Python dependencies will not repair a Docker engine problem.

## Port 6379 or 6333 is already in use

Find the owning process:

```powershell
Get-NetTCPConnection -LocalPort 6379,6333 -ErrorAction SilentlyContinue |
  Select-Object LocalAddress, LocalPort, State, OwningProcess
```

Or choose alternate loopback ports:

```powershell
$env:REDIS_PORT = "6380"
$env:QDRANT_PORT = "6334"
docker compose --profile qdrant up -d redis qdrant
```

Then set:

```dotenv
RAG_REDIS_URL=redis://127.0.0.1:6380/0
RAG_VECTOR_STORE__URL=http://127.0.0.1:6334
```

## Port 8000 is already in use

```powershell
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue |
  Select-Object LocalAddress, LocalPort, State, OwningProcess
uv run rag serve --port 8080
```

Use `--api-url http://localhost:8080` with `rag enqueue` and `rag query`.

## API returns `401 invalid API token`

The canonical runtime loads `RAG_API_TOKEN` from `.env`:

```dotenv
RAG_API_TOKEN=local-api-token
```

Restart `rag serve` after changing `.env`. The CLI also loads the token from `.env`, or it can be
overridden:

```powershell
uv run rag query "test" --api-token local-api-token
```

Process environment variables have higher precedence than `.env`. Look for a stale override:

```powershell
Get-ChildItem Env:RAG_API_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:RAG_API_TOKEN -ErrorAction SilentlyContinue
```

Restart the process after removing it.

## `/metrics` or `/debug/state` returns `503`

Set an admin token in `.env` and restart the API:

```dotenv
RAG_ADMIN_TOKEN=local-admin-token
```

Send it as `Authorization: Bearer local-admin-token`. Use a distinct, high-entropy token outside
isolated local development.

## `rag enqueue --wait` never completes

Keep these running in separate windows:

```powershell
uv run rag serve
```

```powershell
uv run rag worker
```

Also verify Redis. The API enqueues jobs, but only the worker consumes them. If the worker exits
during BGE initialization or cannot reach Redis/Qdrant/SQLite, the job cannot succeed.

Inspect the job without waiting:

```powershell
uv run rag enqueue data/documents/<file>
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/v1/ingestion/jobs/JOB_ID" `
  -Headers @{ Authorization = "Bearer local-api-token" }
```

Review the worker error output and the job `error` field.

## Worker rejects a source outside the ingestion root

Use:

```dotenv
RAG_INGESTION_ROOT=data/documents
```

Place the source beneath that directory and enqueue it:

```powershell
uv run rag enqueue data/documents/<file> --wait
```

The CLI resolves the source to an absolute path, and the worker rejects paths that do not remain
inside the resolved root. Do not bypass this boundary with junctions, traversal, or broad roots;
copy reviewed files into the ingestion directory.

## File is unsupported or loading fails

See [Document loaders](usage.md#document-loaders). Common causes:

- unsupported extension;
- file larger than the default 100 MiB limit;
- MIME/extension mismatch;
- missing LibreOffice for legacy Office formats;
- parser or malformed-content failure;
- unavailable model artifacts for Docling.

Renaming a file does not convert it. For legacy Office formats, install LibreOffice and verify:

```powershell
soffice --version
```

## SQLite errors or locked database

The local registry is:

```dotenv
RAG_SQLITE_PATH=.data/rag.sqlite3
```

The API and worker should run from the same repository root so they resolve the same file. Confirm:

```powershell
Resolve-Path .data
Get-ChildItem .data\rag.sqlite3*
```

SQLite is configured with WAL mode and a busy timeout, but it remains a local database. Avoid
network shares, independently copied database files, and large multi-process deployments. Stop the
API and worker before moving or deleting the database.

## Documents exist in SQLite but queries return nothing

SQLite and Qdrant are separate persistence layers. This can occur after resetting only Qdrant,
changing collection names, or partially failing an index operation.

For disposable local data, stop API/worker and reset all coordinated state:

```powershell
docker compose --profile qdrant down --volumes
Remove-Item .data\rag.sqlite3* -ErrorAction SilentlyContinue
docker compose --profile qdrant up -d redis qdrant
```

Then restart API/worker and re-enqueue source documents. For non-disposable data, restore a
consistent backup or run an approved reconciliation/reindex process.

## `rag ingest` succeeds but `rag query` finds nothing

`rag ingest` is a standalone loader/chunker diagnostic. It does not enqueue or index data. Use:

```powershell
uv run rag enqueue data/documents/<file> --wait
uv run rag query "your question"
```

## Direct `create_app()` behaves like an in-memory demo

That is expected. `rag_app.api.create_app()` is the injectable low-level factory and defaults to
in-memory adapters when none are supplied.

Use the canonical runtime command:

```powershell
uv run rag serve
```

It launches `rag_app.runtime.composition.create_runtime_app`, loads `.env`, and injects the local
LM Studio/BGE-M3/Qdrant/SQLite/Redis composition.

## Web loader usage

The project wrapper is named `WebDocumentLoader`:

```python
from rag_app.ingestion import WebDocumentLoader
```

The project wrapper internally uses LangChain's `WebBaseLoader`.

## API returns `429` or `413`

The API factory defaults to a process-local limit of 120 `/api/*` requests per client per minute
and a 10 MiB request body limit. Respect `Retry-After` for `429`. Large files should be placed
under the ingestion root and enqueued, not embedded in a direct-text request.

Production deployments need ingress-level body/rate controls and distributed enforcement.

## OpenTelemetry produces no traces

```powershell
uv sync --extra observability
$env:OTEL_SERVICE_NAME = "prod-rag-api"
$env:OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4318/v1/traces"
uv run rag serve
```

The endpoint must be reachable from the API process. Missing optional instrumentation packages
disable tracing rather than preventing API startup.

## Information to include in a bug report

Include:

- the exact command and full error;
- Python and uv versions;
- `uv run rag doctor` output;
- whether API and worker are both running;
- `docker compose --profile qdrant ps`;
- the file extension and redacted job state;
- relevant variable names and endpoints.

Do not include tokens, document content, full `.env` files, or confidential model prompts.
