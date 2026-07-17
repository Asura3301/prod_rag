# Configuration Reference

Configuration uses environment variables. Typed application settings use the `RAG_` prefix and
double underscores for nested values, for example `RAG_LLM__MODEL`.

## Configuration precedence and loading

`rag_app.core.config.Settings` uses Pydantic Settings and reads:

1. Constructor values.
2. Process environment variables.
3. The repository-root `.env` file.
4. Model defaults.

Process environment values take precedence over `.env`.

The current API factory does not instantiate `Settings`; it directly reads `RAG_API_TOKEN`,
`RAG_ADMIN_TOKEN`, `REDIS_URL`, `OTEL_SERVICE_NAME`, and `OTEL_EXPORTER_OTLP_ENDPOINT`. Therefore,
export those variables in the server process or configure them in Compose/orchestration.

Validate the typed settings at any time:

```powershell
uv run python -c "from rag_app.core.config import get_settings; print(get_settings().model_dump_json(indent=2))"
```

Secrets are redacted by Pydantic when represented normally. Do not print decrypted secrets in
shared logs.

## Application

- `RAG_APP_NAME`: logical application name; default `prod-rag`.
- `RAG_ENVIRONMENT`: `development`, `test`, `staging`, or `production`.
- `RAG_DEBUG`: boolean. It must be `false` when the environment is `production`.
- `RAG_API_TOKEN`: bearer token for `/api/*`; read directly by the API factory.
- `RAG_ADMIN_TOKEN`: bearer token for `/metrics` and `/debug/state`; read directly by the API
  factory. If absent, administrative endpoints return `503`.

Use different API and admin tokens. Put production values in the deployment platform’s secret
manager.

## LLM provider

- `RAG_LLM__PROVIDER`: provider name; default `openai`.
- `RAG_LLM__MODEL`: model identifier; default `gpt-4o-mini`.
- `RAG_LLM__API_KEY`: provider credential.
- `RAG_LLM__BASE_URL`: optional OpenAI-compatible endpoint.
- `RAG_LLM__TIMEOUT_SECONDS`: positive request timeout; default `30`.
- `RAG_LLM__MAX_RETRIES`: `0` to `20`; default `3`.
- `RAG_LLM__TEMPERATURE`: `0` to `2`; default `0`.
- `RAG_LLM__MAX_TOKENS`: positive response limit; default `2048`.

These values are validated by `Settings`, but the default API service does not construct a chat
model. Use them when composing `rag_app.llm` and the LangGraph workflow.

### OpenAI-compatible example

```dotenv
RAG_LLM__PROVIDER=openai
RAG_LLM__MODEL=gpt-4o-mini
RAG_LLM__API_KEY=replace-with-secret
```

For a compatible gateway or local server:

```dotenv
RAG_LLM__PROVIDER=openai
RAG_LLM__MODEL=your-model-name
RAG_LLM__API_KEY=replace-if-required
RAG_LLM__BASE_URL=http://localhost:1234/v1
```

The endpoint must implement the OpenAI chat-completions behavior expected by `ChatOpenAI`.

### Anthropic example

```dotenv
RAG_LLM__PROVIDER=anthropic
RAG_LLM__MODEL=your-enabled-anthropic-model
RAG_LLM__API_KEY=replace-with-secret
```

The typed core settings accept provider strings. The separate `rag_app.llm.config.LLMConfig`
contains the enum used by `create_chat_model`; application composition must map between them.

## Embeddings

- `RAG_EMBEDDINGS__PROVIDER`: default `openai`.
- `RAG_EMBEDDINGS__MODEL`: default `text-embedding-3-small`.
- `RAG_EMBEDDINGS__API_KEY`: embedding credential.
- `RAG_EMBEDDINGS__BASE_URL`: optional OpenAI-compatible endpoint.
- `RAG_EMBEDDINGS__DIMENSIONS`: optional positive dimension override.
- `RAG_EMBEDDINGS__BATCH_SIZE`: positive batch size; default `64`.
- `RAG_EMBEDDINGS__TIMEOUT_SECONDS`: default `30`.
- `RAG_EMBEDDINGS__MAX_RETRIES`: default `3`.

The embedding dimension must match the vector collection/table dimension. Changing models usually
requires a new index or a controlled reindex.

### Hosted OpenAI-compatible embeddings

```dotenv
RAG_EMBEDDINGS__PROVIDER=openai
RAG_EMBEDDINGS__MODEL=text-embedding-3-small
RAG_EMBEDDINGS__API_KEY=replace-with-secret
RAG_EMBEDDINGS__DIMENSIONS=1536
```

### Local Hugging Face embeddings

Install:

```powershell
uv sync --extra local-models
```

The code default is `BAAI/bge-small-en-v1.5`. The model is downloaded on first use and cached by
Sentence Transformers. Local model configuration is currently done when constructing
`HuggingFaceEmbeddingProvider`, not automatically from `Settings`.

## Vector store

- `RAG_VECTOR_STORE__PROVIDER`: `memory`, `qdrant`, or `pgvector` by convention.
- `RAG_VECTOR_STORE__URL`: service URL, primarily for Qdrant.
- `RAG_VECTOR_STORE__API_KEY`: optional Qdrant API key.
- `RAG_VECTOR_STORE__INDEX_NAME`: collection/table logical name; default `documents`.
- `RAG_VECTOR_STORE__NAMESPACE`: default `default`.
- `RAG_VECTOR_STORE__TIMEOUT_SECONDS`: default `30`.
- `POSTGRES_DSN`: PostgreSQL connection URI used outside the nested settings model.

### Qdrant

Local host-side example:

```dotenv
RAG_VECTOR_STORE__PROVIDER=qdrant
RAG_VECTOR_STORE__URL=http://localhost:6333
RAG_VECTOR_STORE__INDEX_NAME=documents
```

Inside Compose, use `http://qdrant:6333`, not `localhost`.

### pgvector

Local host-side example:

```dotenv
RAG_VECTOR_STORE__PROVIDER=pgvector
POSTGRES_DSN=postgresql://rag:replace-password@localhost:5432/rag
```

Inside Compose, the hostname is `postgres`.

## Chunking and retrieval

- `RAG_CHUNKING__CHUNK_SIZE`: positive size; default `800`.
- `RAG_CHUNKING__CHUNK_OVERLAP`: default `120`; must be smaller than `chunk_size`.
- `RAG_RETRIEVAL__TOP_K`: `1` to `100`; default `5`.
- `RAG_RETRIEVAL__SCORE_THRESHOLD`: optional value from `0` to `1`.

These are typed settings for composition. The current `IngestionPipeline()` and default API are
not constructed from them automatically.

Hybrid retrieval profiles:

- `fast`: dense retrieval, candidate multiplier `2`.
- `balanced`: dense plus lexical retrieval and RRF, candidate multiplier `4`.
- `quality`: hybrid retrieval, reranking, and expansion, candidate multiplier `8`.

## Redis and worker

- `REDIS_URL`: default in the worker is `redis://localhost:6379/0`.
- `RAG_LOG_LEVEL`: worker log level; default `INFO`.

When `REDIS_URL` is visible to the API, it uses `RedisJobQueue`; otherwise, it uses a process-local
`InMemoryJobQueue`.

Compose configures `redis://redis:6379/0` and enables append-only Redis persistence.

## Logging

Typed settings:

- `RAG_LOGGING__LEVEL`: `CRITICAL`, `ERROR`, `WARNING`, `INFO`, or `DEBUG`.
- `RAG_LOGGING__JSON`: boolean; maps to the internal `json_output` field.

The standalone worker currently uses `RAG_LOG_LEVEL` separately.

## Telemetry

Install:

```powershell
uv sync --extra observability
```

Set:

```dotenv
OTEL_SERVICE_NAME=prod-rag-api
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318/v1/traces
```

Tracing is enabled only if the endpoint is set and required OpenTelemetry packages are installed.
The URL must be an OTLP/HTTP trace endpoint reachable from the API process.

`/metrics` exposes the built-in job-state gauge whether or not the observability extra is
installed. It requires the admin token.

## Loader-related settings and system tools

- `EMBED_MODEL_ID`: tokenizer/model identifier passed to Docling `HybridChunker`; default
  `sentence-transformers/all-MiniLM-L6-v2`.
- LibreOffice `soffice`: required for some legacy `.doc` conversions.
- `python-magic-bin`: installed automatically on Windows by the project dependency marker.

The first Docling or local-model run may download model artifacts. Prepare cache storage and
network access in controlled environments.

## Docker Compose variables

The Compose file consumes:

- `API_PORT`, default `8000`.
- `RAG_API_TOKEN`, default `change-me`.
- `RAG_ADMIN_TOKEN`, default `change-me`.
- `RAG_VECTOR_STORE_PROVIDER`, default `memory`.
- `RAG_VECTOR_STORE_URL`, default empty.
- `POSTGRES_DB`, default `rag`.
- `POSTGRES_USER`, default `rag`.
- `POSTGRES_PASSWORD`, default `rag-local-only`.
- `QDRANT_IMAGE`, default `qdrant/qdrant:v1.15.4`.

Notice that Compose uses the un-nested host variable `RAG_VECTOR_STORE_PROVIDER` and maps it to the
container variable `RAG_VECTOR_STORE__PROVIDER`.

Inspect the fully resolved Compose configuration before starting:

```powershell
docker compose --profile workers --profile pgvector config
```

Do not paste resolved output into tickets because it may include secrets.

## Factory-only API controls

These options have safe code defaults but are not environment variables:

- `rate_limit_per_minute=120`.
- `max_body_bytes=10485760` (10 MiB).

Set them when constructing the app:

```python
from rag_app.api import create_app

app = create_app(
    rate_limit_per_minute=60,
    max_body_bytes=5 * 1024 * 1024,
)
```

## Runtime wiring status

Currently automatic:

- FastAPI with in-memory document/query service.
- Redis job queue when `REDIS_URL` is set.
- API/admin bearer tokens from process environment.
- OpenTelemetry setup when its endpoint is set.

Currently requires explicit Python composition:

- Hosted or local LLM.
- Hosted or local embeddings.
- Qdrant or pgvector as the API’s retrieval store.
- `IndexingService` after file ingestion.
- `HybridRetriever`, reranker, and expansion hooks.
- LangGraph RAG workflow and PostgreSQL checkpointer.
- Persistent document registry shared by worker and API.

Until that composition layer is added, a worker job validates/loads/chunks its source but does not
make the resulting content queryable through the API.

## Production configuration checklist

- Replace all example passwords and tokens.
- Keep data services on private networks; expose only the API through TLS.
- Store secrets in a managed secret store, not `.env`.
- Enforce tenant and ACL filters server-side.
- Set resource limits, ingestion quotas, body limits, and upstream rate limits.
- Use durable upload/object storage accessible to workers.
- Select exactly one embedding model and matching vector dimension per index version.
- Back up PostgreSQL/Qdrant and Redis according to recovery objectives.
- Pin container images by digest after validation.
- Disable development reload.
- Complete the composition layer and run integration plus evaluation gates before production use.
