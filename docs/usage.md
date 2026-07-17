# Usage Guide

This guide covers every public CLI command, HTTP route, loader group, worker, vector adapter,
retrieval profile, evaluation tool, and operational endpoint currently included in the project.

## CLI

Show all commands:

```powershell
uv run rag --help
```

### Serve

Development:

```powershell
uv run rag serve --reload
```

Custom interface and port:

```powershell
uv run rag serve --host 127.0.0.1 --port 8080
```

Do not use reload in production. The command starts one Uvicorn process; production process
replication and restart policy belong in the deployment platform.

### Ingest

```powershell
uv run rag ingest .\documents\policy.pdf
uv run rag ingest .\documents
```

This validates, loads, normalizes metadata, chunks content, and prints JSON. It does not embed or
index chunks.

### Query

```powershell
uv run rag query "What is the refund policy?" `
  --api-url http://localhost:8000 `
  --api-token local-api-token `
  --top-k 5 `
  --timeout 60
```

If `--api-token` is omitted, the command reads `RAG_API_TOKEN`.

### Evaluate

```powershell
uv run rag evaluate --dataset evals/golden_dataset.jsonl
```

This command validates/inspects the dataset and lists available metrics. It does not call the
application or calculate a report until application-specific predictions exist.

## HTTP API

Set reusable PowerShell values:

```powershell
$baseUrl = "http://localhost:8000"
$apiHeaders = @{ Authorization = "Bearer local-api-token" }
$adminHeaders = @{ Authorization = "Bearer local-admin-token" }
```

All `/api/*` routes require the API bearer token when configured. Health endpoints are public.
Administrative routes require their separate token.

### Health

```powershell
Invoke-RestMethod "$baseUrl/health/live"
Invoke-RestMethod "$baseUrl/health/ready"
```

Use liveness for process restart decisions and readiness for traffic admission.

### Create a direct text document

```powershell
$created = Invoke-RestMethod `
  -Method Post `
  -Uri "$baseUrl/api/v1/documents" `
  -Headers $apiHeaders `
  -ContentType "application/json" `
  -Body (@{
    source = "memory://refund-policy"
    text = "Refund requests are accepted within 30 days of purchase."
    metadata = @{
      tenant_id = "demo"
      category = "policy"
      acl = @("reader")
    }
  } | ConvertTo-Json -Depth 5)

$created.id
```

### List, get, and delete documents

```powershell
Invoke-RestMethod -Uri "$baseUrl/api/v1/documents" -Headers $apiHeaders

Invoke-RestMethod `
  -Uri "$baseUrl/api/v1/documents/$($created.id)" `
  -Headers $apiHeaders

Invoke-RestMethod `
  -Method Delete `
  -Uri "$baseUrl/api/v1/documents/$($created.id)" `
  -Headers $apiHeaders
```

The delete route returns HTTP `204` with no body.

### Query

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

`top_k` must be between 1 and 100. In the default API, filters perform metadata equality matching.

### Stream a query with SSE

PowerShell's standard REST cmdlet does not present SSE events conveniently. Use curl:

```powershell
curl.exe -N -X POST "$baseUrl/api/v1/query/stream" `
  -H "Authorization: Bearer local-api-token" `
  -H "Content-Type: application/json" `
  -d '{\"query\":\"How long is the refund period?\",\"top_k\":5,\"filters\":{}}'
```

Events arrive in this order: `token`, `citations`, `done`. The current API splits a completed
answer into SSE token events; it is not provider-level token streaming.

### Enqueue an ingestion job

```powershell
$job = Invoke-RestMethod `
  -Method Post `
  -Uri "$baseUrl/api/v1/ingestion/jobs" `
  -Headers $apiHeaders `
  -ContentType "application/json" `
  -Body (@{
    source = "D:\shared-documents\policy.pdf"
    metadata = @{ tenant_id = "demo" }
    idempotency_key = "policy-v1"
  } | ConvertTo-Json -Depth 4)

$job
```

The same idempotency key returns the existing job.

List and inspect:

```powershell
Invoke-RestMethod -Uri "$baseUrl/api/v1/ingestion/jobs" -Headers $apiHeaders
Invoke-RestMethod -Uri "$baseUrl/api/v1/ingestion/jobs/$($job.id)" -Headers $apiHeaders
```

Cancel:

```powershell
Invoke-RestMethod `
  -Method Delete `
  -Uri "$baseUrl/api/v1/ingestion/jobs/$($job.id)" `
  -Headers $apiHeaders
```

Cancellation is best effort and does not interrupt an already-running loader.

### Metrics and debug state

```powershell
Invoke-WebRequest -Uri "$baseUrl/metrics" -Headers $adminHeaders |
  Select-Object -ExpandProperty Content

Invoke-RestMethod -Uri "$baseUrl/debug/state" -Headers $adminHeaders
```

Metrics currently report ingestion-job counts by state. If no admin token is configured, these
routes return `503`.

## Document loaders and ingestion

Routing is extension based.

### Docling

Extensions:

- `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.html`, `.xhtml`, `.md`.

Docling produces document chunks using its `HybridChunker`. It is suitable for layout-rich modern
documents.

```powershell
uv run rag ingest .\documents\report.pdf
```

### Unstructured

Extensions:

- `.doc`, `.ppt`, `.xls`, `.rtf`, `.epub`, `.eml`, `.msg`, `.odt`, `.odp`, `.ods`, `.rst`, `.org`.

Some legacy formats require external converters. On Windows, install LibreOffice and ensure
`soffice.exe` is on `PATH`:

```powershell
soffice --version
```

### Plain text and source files

Extensions:

- `.txt`, `.log`, `.py`, `.js`, `.ts`, `.sh`, `.yaml`, `.yml`, `.ini`, `.cfg`, `.ipynb`.

Notebook files are currently loaded as raw text/JSON rather than notebook cells.

### Structured data

- `.csv` uses `CSVDocumentLoader`.
- `.json` uses `JSONDocumentLoader`; its default jq schema is `.[].content`.

The default JSON shape is therefore:

```json
[
  {"content": "First document"},
  {"content": "Second document"}
]
```

Use the loader class directly to select another jq expression:

```python
from rag_app.ingestion.document_loaders import JSONDocumentLoader

documents = JSONDocumentLoader(
    jq_schema=".records[].body",
    text_content=True,
).load("documents/data.json")
print(len(documents))
```

### Web pages

Use `WebBaseDocumentLoader` directly:

```python
from rag_app.ingestion.document_loaders import WebBaseDocumentLoader

documents = WebBaseDocumentLoader().load("https://example.com")
print(documents[0].metadata)
```

Only absolute public HTTP(S) URLs are accepted. Credentials, localhost, and literal non-global IP
addresses are rejected. Production deployments still need DNS resolution/rebinding protection,
egress policy, response-size limits, and redirect validation.

### File safety

Local file validation:

- Resolves the path and requires a regular file.
- Allows only the listed extensions.
- Rejects files larger than 100 MiB by default.
- Optionally confines files to an allowed root.
- Performs basic MIME/extension consistency checks.

Do not treat parsing libraries as malware scanners. Scan untrusted uploads before ingestion and run
parsers in isolated, resource-limited workers.

## Worker

Start Redis first. With Docker:

```powershell
docker compose up redis -d
```

Start the worker on the host:

```powershell
$env:REDIS_URL = "redis://localhost:6379/0"
uv run python scripts/run_worker.py
```

Or start API, Redis, and worker together:

```powershell
docker compose --profile workers up --build -d
```

The worker accepts `ingest` jobs, retries failures up to three attempts, and then marks a job
`dead_letter`.

Current limitation: the worker loads and chunks but does not call `IndexingService`. Its in-memory
registry is created per job. In Compose there is also no host-document bind mount, so a Windows
path submitted to the containerized worker is not accessible. Add shared object storage or a
controlled bind volume as part of production composition.

## Indexing and hybrid retrieval

Run the complete infrastructure-free example:

```powershell
uv run python examples/local_pipeline.py
```

Its construction pattern is:

```python
from rag_app.indexing import HashEmbeddingProvider, IndexingService, InMemoryVectorStore
from rag_app.retrieval import HybridRetriever

embeddings = HashEmbeddingProvider(dimensions=128)
store = InMemoryVectorStore()
indexing = IndexingService(embeddings, store)
retriever = HybridRetriever(embeddings, store)
```

Call `await store.create(128)`, `await indexing.index(chunks)`, and
`await retriever.retrieve(...)` inside an async function, as the example does.

### Qdrant adapter

Install and start:

```powershell
uv sync --extra qdrant
docker compose --profile qdrant up qdrant -d
```

The provided Compose service does not publish Qdrant to Windows. For host-side testing, add a
temporary `6333:6333` port mapping or run a separate official container:

```powershell
docker run --name prod-rag-qdrant -p 6333:6333 `
  -v prod-rag-qdrant-data:/qdrant/storage `
  qdrant/qdrant:v1.15.4
```

Use the project adapter:

```python
import asyncio

from rag_app.indexing import QdrantVectorStore, VectorRecord


async def main() -> None:
    store = QdrantVectorStore("documents", url="http://localhost:6333")
    await store.create(3)
    await store.upsert(
        [VectorRecord("example-1", [1.0, 0.0, 0.0], "Example text", {"tenant_id": "demo"})]
    )
    results = await store.query([1.0, 0.0, 0.0], filters={"tenant_id": "demo"})
    print(results[0].text)


asyncio.run(main())
```

Run the contract test:

```powershell
$env:RAG_TEST_QDRANT_URL = "http://localhost:6333"
uv run pytest -q -m integration tests/integration/test_vector_store_contract.py
```

The adapter creates named `dense` and `sparse` vectors and supports metadata payload filters.

### pgvector adapter

Install:

```powershell
uv sync --extra pgvector
```

Start a host-accessible pgvector container:

```powershell
docker run --name prod-rag-postgres -d `
  -e POSTGRES_DB=rag `
  -e POSTGRES_USER=rag `
  -e POSTGRES_PASSWORD=local-postgres-password `
  -p 5432:5432 `
  -v prod-rag-postgres-data:/var/lib/postgresql/data `
  pgvector/pgvector:pg16
```

Use the adapter:

```python
import asyncio

from rag_app.indexing import PgVectorStore, VectorRecord


async def main() -> None:
    store = PgVectorStore(
        "postgresql://rag:local-postgres-password@localhost:5432/rag",
        table="documents",
    )
    await store.create(3)
    await store.upsert(
        [VectorRecord("example-1", [1.0, 0.0, 0.0], "Example text", {"tenant_id": "demo"})]
    )
    results = await store.query([1.0, 0.0, 0.0], filters={"tenant_id": "demo"})
    print(results[0].text)


asyncio.run(main())
```

`create()` enables pgvector, creates the table, a generated full-text search column, a GIN index,
and an HNSW cosine index.

Run the contract test:

```powershell
$env:RAG_TEST_PGVECTOR_DSN = "postgresql://rag:local-postgres-password@localhost:5432/rag"
uv run pytest -q -m integration tests/integration/test_vector_store_contract.py
```

### Production embeddings

OpenAI-compatible:

```python
from rag_app.indexing import OpenAICompatibleEmbeddingProvider

embeddings = OpenAICompatibleEmbeddingProvider(
    "text-embedding-3-small",
    api_key="replace-with-secret",
)
```

Prefer injecting credentials from a secret store rather than literals.

Local Sentence Transformers:

```python
from rag_app.indexing import HuggingFaceEmbeddingProvider

embeddings = HuggingFaceEmbeddingProvider("BAAI/bge-small-en-v1.5")
```

Install `local-models` first. Model loading and download occur lazily.

### Retrieval filters

`RetrievalRequest` supports ordinary equality filters plus dedicated tenant and ACL inputs:

```python
from rag_app.retrieval import RetrievalRequest

request = RetrievalRequest(
    query="refund period",
    top_k=5,
    profile="balanced",
    tenant_id="tenant-a",
    principals=("reader",),
    filters={"language": "en"},
    max_tokens=2000,
)
```

Tenant and ACL constraints must be derived from authenticated server context in production, not
trusted from arbitrary client input.

## LangGraph generation

`build_rag_graph(retriever, llm, checkpointer=...)` compiles a bounded workflow with request
validation, query analysis, retrieval, evidence grading, one rewrite opportunity, context
construction, answer generation, citation validation, and abstention.

This graph is not connected to the default API. Compose it with a retriever and chat model, then
inject a `RAGService` implementation that invokes the graph. The graph contract and state are in
`src/rag_app/generation/workflow.py`; PostgreSQL checkpoint construction is in
`src/rag_app/generation/persistence.py`.

Do not enable the graph in production until the adapter maps authenticated tenant/ACL context,
persists index version metadata, and translates graph citations to the public API schema.

## Evaluation

Golden data is JSON Lines. Each row requires:

```json
{"id":"refund-policy","query":"How long do I have to request a refund?","relevant_document_ids":["policy-refunds"],"expected_citation_ids":["policy-refunds"],"reference_answer":"Refund requests are accepted within 30 days of purchase."}
```

Application code must produce one `EvaluationPrediction` for every example. Then:

```python
from rag_app.evaluation import (
    EvaluationPrediction,
    evaluate_predictions,
    load_golden_dataset,
)

examples = load_golden_dataset("evals/golden_dataset.jsonl")
predictions = [
    EvaluationPrediction(
        example_id=example.id,
        retrieved_document_ids=example.relevant_document_ids,
        citation_ids=example.expected_citation_ids,
        answer=example.reference_answer,
        latency_ms=10.0,
    )
    for example in examples
]
report = evaluate_predictions(examples, predictions, k=5)
print(report.to_dict())
```

Use `assert_no_regression(candidate, baseline)` to enforce quality and p95-latency tolerances.
Replace the starter data with reviewed, versioned, domain-specific cases before making quality
claims.

## Loader smoke tests

The main suite excludes slow, integration, and legacy tests:

```powershell
uv run pytest -q
```

Run the loader script:

```powershell
uv run python tests/test_loaders.py
```

It generates some fixtures under `tests/data/generated`, can request `https://example.com`, and
needs manually supplied fixtures for some formats. Legacy `.doc` tests skip when LibreOffice is
unavailable.

Select marker groups explicitly:

```powershell
uv run pytest -q -m slow
uv run pytest -q -m integration
uv run pytest -q -m legacy
```

## Pre-commit

Install hooks:

```powershell
uv run pre-commit install
```

Run all hooks:

```powershell
uv run pre-commit run --all-files
```

Hooks include Ruff checks/formatting and basic TOML, YAML, conflict, whitespace, and file-size
checks.

## OpenTelemetry

Install the optional packages and point the API at an OTLP/HTTP collector:

```powershell
uv sync --extra observability
$env:OTEL_SERVICE_NAME = "prod-rag-api"
$env:OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4318/v1/traces"
uv run rag serve
```

If the collector is in Compose, use its service hostname rather than `localhost`. Missing optional
packages disable tracing rather than preventing API startup.

## Build and release tools

Build locally:

```powershell
uv build
```

Artifacts are written under `dist/`. GitHub Actions includes CI, dependency management, and tagged
release workflows. Releases are triggered by version tags; review workflow permissions and
repository secrets before using them.

## Official references

- [FastAPI interactive API documentation](https://fastapi.tiangolo.com/tutorial/first-steps/)
- [Docker Compose profiles](https://docs.docker.com/compose/how-tos/profiles/)
- [Qdrant quickstart](https://qdrant.tech/documentation/quickstart/)
- [Qdrant sparse vectors](https://qdrant.tech/documentation/manage-data/vectors/)
- [pgvector HNSW and Docker](https://github.com/pgvector/pgvector)
- [Redis Docker installation](https://redis.io/docs/latest/operate/oss_and_stack/install/install-stack/docker/)
- [uv dependency extras](https://docs.astral.sh/uv/concepts/projects/dependencies/)
