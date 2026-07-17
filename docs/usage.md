# Usage Guide

This guide describes the functional Windows-local runtime and the lower-level demo interfaces. Run
commands from the repository root in PowerShell.

## End-to-end local command sequence

Install the two required extras:

```powershell
uv sync --extra qdrant --extra local-models
```

Start local infrastructure:

```powershell
docker compose --profile qdrant up -d redis qdrant
```

Load `gpt-oss-20b` in LM Studio, start its server on
`http://127.0.0.1:1234/v1`, and validate dependencies:

```powershell
uv run rag doctor
```

Run these long-lived commands in separate PowerShell windows:

```powershell
uv run rag serve
```

```powershell
uv run rag worker
```

Then enqueue a source under the configured `data/documents` root and query it:

```powershell
uv run rag enqueue data/documents/<file> --wait
uv run rag query "What does the document say?"
```

All commands load settings and API tokens from `.env`. On first service or worker startup,
Sentence Transformers may need time to download and initialize `BAAI/bge-m3`.

## CLI reference

Show all commands:

```powershell
uv run rag --help
```

### `rag doctor`

```powershell
uv run rag doctor
```

Checks that Sentence Transformers is installed, LM Studio advertises `gpt-oss-20b`, Redis and
Qdrant are reachable, and the ingestion/SQLite parent directories exist. It does not perform a
complete embedding, indexing, and generation test.

### `rag serve`

```powershell
uv run rag serve
uv run rag serve --host 127.0.0.1 --port 8080
uv run rag serve --reload
```

Use `--reload` only during development. The command starts the environment-driven local runtime:

- LM Studio at `127.0.0.1:1234/v1` with model `gpt-oss-20b`;
- Sentence Transformers `BAAI/bge-m3` with 1024-dimensional dense embeddings;
- Qdrant at `127.0.0.1:6333`;
- Redis at `127.0.0.1:6379`;
- SQLite at `.data/rag.sqlite3`.

The exact values come from `.env`.

### `rag worker`

```powershell
uv run rag worker
```

The worker consumes Redis `ingest` jobs, requires every source to resolve inside
`RAG_INGESTION_ROOT`, loads and chunks documents, embeds them, indexes them into Qdrant, and writes
document records to the shared SQLite database. It retries failed jobs and eventually marks
unrecoverable work as `dead_letter`.

The API and worker are separate processes and must remain running together for asynchronous
ingestion.

### `rag enqueue`

```powershell
uv run rag enqueue data/documents/<file> --wait
uv run rag enqueue data/documents --wait
```

Options:

- `--api-url`: default `http://127.0.0.1:8000`.
- `--api-token`: overrides `RAG_API_TOKEN` loaded from `.env`.
- `--idempotency-key`: optional stable de-duplication key.
- `--timeout`: HTTP timeout, default 60 seconds.
- `--wait`: poll until `succeeded`, `dead_letter`, or `cancelled`.

The CLI resolves the submitted source to an absolute path. The worker then verifies it remains
inside the configured ingestion root. A successful waited job is queryable through the local API.

### `rag query`

```powershell
uv run rag query "What is the refund policy?"
uv run rag query "Summarize the handbook" --top-k 8 --timeout 120
```

Options:

- `--api-url`: default `http://127.0.0.1:8000`.
- `--api-token`: overrides `RAG_API_TOKEN` loaded from `.env`.
- `--top-k`: number of requested retrieval results, default `5`.
- `--timeout`: request timeout, default 60 seconds.

The API performs hybrid retrieval and runs the LangGraph generation workflow against LM Studio.
Responses contain `answer`, `citations`, and `latency_ms`.

### `rag ingest`

```powershell
uv run rag ingest data/documents/<file>
uv run rag ingest data/documents
```

This standalone diagnostic command validates, loads, chunks, and prints a JSON summary. Unlike
`rag enqueue`, it does not submit a Redis job or index the resulting chunks into the running local
runtime.

### `rag evaluate`

```powershell
uv run rag evaluate --dataset evals/golden_dataset.jsonl
```

This command inspects the dataset and reports available metrics. It does not produce model
predictions by itself. Replace starter data with reviewed domain examples before making quality
claims.

## HTTP API

The interactive API documentation is available at <http://127.0.0.1:8000/docs> after
`rag serve` starts.

Set reusable PowerShell values:

```powershell
$baseUrl = "http://127.0.0.1:8000"
$apiHeaders = @{ Authorization = "Bearer local-api-token" }
$adminHeaders = @{ Authorization = "Bearer local-admin-token" }
```

Use the values actually configured in `.env`.

### Health

```powershell
Invoke-RestMethod "$baseUrl/health/live"
Invoke-RestMethod "$baseUrl/health/ready"
```

Readiness becomes successful after SQLite initialization, BGE dimension validation, and Qdrant
collection setup complete.

### Ingestion jobs

The CLI is the simplest local path, but the equivalent API request is:

```powershell
$job = Invoke-RestMethod `
  -Method Post `
  -Uri "$baseUrl/api/v1/ingestion/jobs" `
  -Headers $apiHeaders `
  -ContentType "application/json" `
  -Body (@{
    source = (Resolve-Path "data/documents/handbook.pdf").Path
    metadata = @{ category = "handbook" }
    idempotency_key = "handbook-v1"
  } | ConvertTo-Json -Depth 4)
```

List and inspect jobs:

```powershell
Invoke-RestMethod -Uri "$baseUrl/api/v1/ingestion/jobs" -Headers $apiHeaders
Invoke-RestMethod -Uri "$baseUrl/api/v1/ingestion/jobs/$($job.id)" -Headers $apiHeaders
```

Cancellation is best effort:

```powershell
Invoke-RestMethod `
  -Method Delete `
  -Uri "$baseUrl/api/v1/ingestion/jobs/$($job.id)" `
  -Headers $apiHeaders
```

### Direct text documents

Direct API text insertion is also persistent in the `rag serve` local runtime:

```powershell
$created = Invoke-RestMethod `
  -Method Post `
  -Uri "$baseUrl/api/v1/documents" `
  -Headers $apiHeaders `
  -ContentType "application/json" `
  -Body (@{
    source = "manual://refund-policy"
    text = "Refund requests are accepted within 30 days of purchase."
    metadata = @{ category = "policy" }
  } | ConvertTo-Json -Depth 4)
```

List, retrieve, or delete:

```powershell
Invoke-RestMethod -Uri "$baseUrl/api/v1/documents" -Headers $apiHeaders
Invoke-RestMethod -Uri "$baseUrl/api/v1/documents/$($created.id)" -Headers $apiHeaders
Invoke-RestMethod -Method Delete -Uri "$baseUrl/api/v1/documents/$($created.id)" -Headers $apiHeaders
```

Document metadata lives in SQLite and vectors live in Qdrant. Removing one store independently can
leave local state inconsistent.

### Query and SSE

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "$baseUrl/api/v1/query" `
  -Headers $apiHeaders `
  -ContentType "application/json" `
  -Body (@{
    query = "How long is the refund period?"
    top_k = 5
    filters = @{ category = "policy" }
  } | ConvertTo-Json -Depth 4)
```

For server-sent events:

```powershell
curl.exe -N -X POST "$baseUrl/api/v1/query/stream" `
  -H "Authorization: Bearer local-api-token" `
  -H "Content-Type: application/json" `
  -d '{\"query\":\"How long is the refund period?\",\"top_k\":5,\"filters\":{}}'
```

The current SSE endpoint emits a completed answer as `token` events followed by `citations` and
`done`; it is not provider-level token streaming.

### Metrics and debug state

```powershell
Invoke-WebRequest -Uri "$baseUrl/metrics" -Headers $adminHeaders |
  Select-Object -ExpandProperty Content
Invoke-RestMethod -Uri "$baseUrl/debug/state" -Headers $adminHeaders
```

These routes require `RAG_ADMIN_TOKEN`; they return `503` when it is empty.

## Document loaders

Local routing is extension based:

- Docling: `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.html`, `.xhtml`, `.md`.
- Unstructured: `.doc`, `.ppt`, `.xls`, `.rtf`, `.epub`, `.eml`, `.msg`, `.odt`, `.odp`,
  `.ods`, `.rst`, `.org`.
- Plain text/source: `.txt`, `.log`, `.py`, `.js`, `.ts`, `.sh`, `.yaml`, `.yml`, `.ini`,
  `.cfg`, `.ipynb`.
- Structured: `.csv` and `.json`.

Some legacy Office formats require LibreOffice and `soffice.exe` on `PATH`. PDF loading falls back
to PyPDF when Docling fails.

The default JSON loader schema is `.[].content`, so the expected shape is:

```json
[
  {"content": "First document"},
  {"content": "Second document"}
]
```

### Web pages

Use the project wrapper `WebDocumentLoader`:

```python
from rag_app.ingestion import WebDocumentLoader

documents = WebDocumentLoader().load("https://example.com")
print(documents[0].metadata)
```

The wrapper delegates fetching to LangChain's `WebBaseLoader` after URL validation.

Only absolute public HTTP(S) URLs are accepted. Production deployments still need DNS
rebinding/redirect defenses, controlled egress, response limits, and content scanning.

### File safety

The local worker confines sources to the ingestion root. File validation also checks regular-file
status, allowed extension, a default 100 MiB limit, and basic MIME/extension consistency.

Parsing libraries are not malware scanners. Treat untrusted uploads as hostile and isolate parsing
in production.

## Data ownership and reset

The local flow persists different state in different places:

- `.data/rag.sqlite3`: document registry and chunk identifiers.
- Qdrant `qdrant-data` volume: dense/sparse vectors and payloads.
- Redis `redis-data` volume: asynchronous job state.
- Sentence Transformers cache: downloaded `BAAI/bge-m3` artifacts.

Back up or reset these as a coordinated set. For a disposable reset:

```powershell
docker compose --profile qdrant down --volumes
Remove-Item .data\rag.sqlite3* -ErrorAction SilentlyContinue
```

## Injectable/demo API

`uv run rag serve` uses `rag_app.runtime.composition.create_runtime_app` and is the default local
runtime.

`rag_app.api.create_app` remains independently usable:

```python
from rag_app.api import create_app

app = create_app()
```

With no injected service or queue, this lower-level factory uses in-memory demo implementations.
Tests and custom applications can inject their own adapters. Do not launch it directly and assume
that `.env` automatically selects the Qdrant/SQLite local service.

## Production limitations

The local command flow demonstrates end-to-end behavior, not a production topology:

- API and worker share a local SQLite file and host document paths.
- Qdrant and Redis are loopback-bound and unauthenticated by default.
- LM Studio is desktop model serving.
- Rate limiting is process-local, SSE is not provider-token streaming, and the sparse embedding
  implementation is deterministic hashing.
- Tenant/ACL context, service authentication, TLS, durable uploads, scanning, backups, distributed
  coordination, observability, resource controls, and high availability require deployment work.

Run integration tests and reviewed domain evaluations before promoting a different composition.
