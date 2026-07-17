# Configuration Reference

The canonical runtime configuration is loaded by `rag_app.core.config.Settings` from process
environment variables and the repository-root `.env` file. Nested settings use a double underscore,
for example `RAG_LLM__MODEL` and `RAG_VECTOR_STORE__URL`.

## Loading and precedence

Pydantic Settings applies this effective precedence:

1. Values passed directly when constructing `Settings`.
2. Process environment variables.
3. Values in `.env`.
4. Code defaults.

`rag serve`, `rag worker`, and `rag doctor` use `get_settings()`. The query and enqueue CLIs also
fall back to the configured API token. Consequently, `RAG_API_TOKEN` and `RAG_ADMIN_TOKEN` load
from `.env`; they do not need to be exported in every PowerShell window.

Never commit `.env`. Process variables override values in it, so check for stale `$env:RAG_*`
values when observed behavior differs from the file.

Inspect resolved non-secret structure:

```powershell
uv run python -c "from rag_app.core.config import get_settings; print(get_settings().model_dump_json(indent=2))"
```

Do not publish this output if it contains deployment-specific values.

## Canonical Windows-local configuration

```dotenv
RAG_APP_NAME=prod-rag
RAG_RUNTIME_MODE=local
RAG_ENVIRONMENT=development
RAG_DEBUG=false
RAG_API_TOKEN=local-api-token
RAG_ADMIN_TOKEN=local-admin-token
RAG_REDIS_URL=redis://127.0.0.1:6379/0
RAG_SQLITE_PATH=.data/rag.sqlite3
RAG_INGESTION_ROOT=data/documents

RAG_LLM__PROVIDER=local
RAG_LLM__MODEL=gpt-oss-20b
RAG_LLM__API_KEY=
RAG_LLM__BASE_URL=http://127.0.0.1:1234/v1
RAG_LLM__TIMEOUT_SECONDS=30
RAG_LLM__MAX_RETRIES=3
RAG_LLM__TEMPERATURE=0
RAG_LLM__MAX_TOKENS=2048

RAG_EMBEDDINGS__PROVIDER=local
RAG_EMBEDDINGS__MODEL=BAAI/bge-m3
RAG_EMBEDDINGS__DIMENSIONS=1024
RAG_EMBEDDINGS__BATCH_SIZE=64
RAG_EMBEDDINGS__TIMEOUT_SECONDS=30
RAG_EMBEDDINGS__MAX_RETRIES=3

RAG_VECTOR_STORE__PROVIDER=qdrant
RAG_VECTOR_STORE__URL=http://127.0.0.1:6333
RAG_VECTOR_STORE__API_KEY=
RAG_VECTOR_STORE__INDEX_NAME=documents
RAG_VECTOR_STORE__NAMESPACE=default
RAG_VECTOR_STORE__TIMEOUT_SECONDS=30

RAG_CHUNKING__CHUNK_SIZE=800
RAG_CHUNKING__CHUNK_OVERLAP=120
RAG_RETRIEVAL__TOP_K=5
# RAG_RETRIEVAL__SCORE_THRESHOLD=0.7

RAG_LOGGING__LEVEL=INFO
RAG_LOGGING__JSON=true
```

Keep the double underscores shown above for every nested model setting; single-underscore variants
are not part of the canonical settings schema.

## Application and persistence

- `RAG_APP_NAME`: logical application name; default `prod-rag`.
- `RAG_RUNTIME_MODE`: currently only `local` is implemented.
- `RAG_ENVIRONMENT`: `development`, `test`, `staging`, or `production`.
- `RAG_DEBUG`: must be `false` when `RAG_ENVIRONMENT=production`.
- `RAG_API_TOKEN`: bearer token for `/api/*`.
- `RAG_ADMIN_TOKEN`: bearer token for `/metrics` and `/debug/state`. Those routes return `503` if
  the token is empty.
- `RAG_REDIS_URL`: Redis queue URL; local default `redis://127.0.0.1:6379/0`.
- `RAG_SQLITE_PATH`: SQLite document-registry path; use `.data/rag.sqlite3` for the repository
  local flow.
- `RAG_INGESTION_ROOT`: directory boundary enforced by the worker. Use `data/documents`; enqueued
  files and directories must resolve beneath it.

SQLite stores document records and indexed chunk identifiers. Qdrant stores vectors and payloads,
while Redis stores job state. The API and worker must resolve `RAG_SQLITE_PATH` and
`RAG_INGESTION_ROOT` consistently.

## LM Studio LLM

The supported Windows-local recipe is:

```dotenv
RAG_LLM__PROVIDER=local
RAG_LLM__MODEL=gpt-oss-20b
RAG_LLM__BASE_URL=http://127.0.0.1:1234/v1
RAG_LLM__API_KEY=
```

LM Studio must have `gpt-oss-20b` loaded and expose it under that exact model identifier. The base
URL includes `/v1`; the application uses the OpenAI-compatible API. An empty API key is appropriate
for the default unauthenticated local LM Studio server.

`TIMEOUT_SECONDS`, `MAX_RETRIES`, `TEMPERATURE`, and `MAX_TOKENS` control provider requests and
generation. A locally reachable endpoint is not evidence that the model is suitable for a
production workload.

## Sentence Transformers embeddings

Install:

```powershell
uv sync --extra local-models
```

The canonical local values are:

```dotenv
RAG_EMBEDDINGS__PROVIDER=local
RAG_EMBEDDINGS__MODEL=BAAI/bge-m3
RAG_EMBEDDINGS__DIMENSIONS=1024
RAG_EMBEDDINGS__BATCH_SIZE=64
```

`BAAI/bge-m3` is loaded through Sentence Transformers and emits 1024-dimensional vectors. The first
service or worker startup may download and initialize the model, which can take time and consume
substantial cache space.

The configured dimension is checked against an actual embedding during service startup. It must
also match the existing Qdrant collection. Changing the embedding model or dimension requires a
new versioned collection or a controlled reindex; do not silently reuse incompatible vectors.

## Qdrant

Install the client and start the local service:

```powershell
uv sync --extra qdrant
docker compose --profile qdrant up -d redis qdrant
```

Host-side configuration:

```dotenv
RAG_VECTOR_STORE__PROVIDER=qdrant
RAG_VECTOR_STORE__URL=http://127.0.0.1:6333
RAG_VECTOR_STORE__INDEX_NAME=documents
```

The local runtime creates the collection if needed with the configured dense dimension. It also
uses the project's sparse-vector path for hybrid retrieval.

## Redis and Qdrant host ports

Compose binds development services to loopback:

- Redis: `127.0.0.1:${REDIS_PORT:-6379}` -> container `6379`.
- Qdrant: `127.0.0.1:${QDRANT_PORT:-6333}` -> container `6333`.

For example, custom host ports require matching application URLs:

```powershell
$env:REDIS_PORT = "6380"
$env:QDRANT_PORT = "6334"
docker compose --profile qdrant up -d redis qdrant
```

```dotenv
RAG_REDIS_URL=redis://127.0.0.1:6380/0
RAG_VECTOR_STORE__URL=http://127.0.0.1:6334
```

Inside the full Compose network, service hostnames are `redis` and `qdrant`, not `127.0.0.1`.
The documented Windows-local flow runs only those data services in containers and runs the API and
worker on the host.

## Chunking, retrieval, and logging

- `RAG_CHUNKING__CHUNK_SIZE`: positive fallback chunk size; default `800`.
- `RAG_CHUNKING__CHUNK_OVERLAP`: default `120`, and must be smaller than chunk size.
- `RAG_RETRIEVAL__TOP_K`: default `5`, valid range `1` to `100`.
- `RAG_RETRIEVAL__SCORE_THRESHOLD`: optional `0` to `1` evidence threshold.
- `RAG_LOGGING__LEVEL`: `CRITICAL`, `ERROR`, `WARNING`, `INFO`, or `DEBUG`.
- `RAG_LOGGING__JSON`: structured JSON logging toggle.

The API request can supply its own `top_k`; the setting is primarily the runtime default for
composition.

## Client-only settings

`RAG_API_URL` may be used by examples, but it is not part of the nested application `Settings`
model. The CLI defaults to `http://127.0.0.1:8000` and supports `--api-url`.

Optional OpenTelemetry variables are also read by the API instrumentation layer:

```dotenv
OTEL_SERVICE_NAME=prod-rag-api
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318/v1/traces
```

Install `uv sync --extra observability` before enabling export.

## API factory versus runtime composition

`uv run rag serve` invokes `rag_app.runtime.composition.create_runtime_app`, which reads `Settings`
and injects the LM Studio/BGE-M3/Qdrant/SQLite service plus Redis queue into the API.

`rag_app.api.create_app` remains a lower-level injectable factory. Calling `create_app()` without a
service or queue uses in-memory demo defaults. It is useful for tests and custom applications, but
it is not the canonical local runtime entry point. Its compatibility environment behavior should
not be used to define new deployment configuration.

## Production configuration limits

The local defaults are deliberately insufficient for production:

- Replace `.env` with a managed secret mechanism and rotate distinct API/admin credentials.
- Use authenticated, encrypted, private service networking instead of unauthenticated loopback
  containers.
- Replace desktop LM Studio serving with an appropriately managed inference tier.
- Plan durable source storage, scanning, backups, restoration, collection versioning, and reindexing.
- Enforce tenant and ACL context server-side, not from untrusted query filters.
- Add distributed rate limiting, resource quotas, observability, high availability, and evaluation
  gates.
- Validate SQLite concurrency and recovery requirements or replace the registry for a distributed
  deployment.
