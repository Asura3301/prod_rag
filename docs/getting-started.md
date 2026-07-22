# Getting Started

This guide starts the functional local runtime on Windows. Run commands from the repository root
in PowerShell.

## 1. Prerequisites

Install:

- Git.
- Python 3.12 or newer.
- [uv](https://docs.astral.sh/uv/getting-started/installation/).
- Docker Desktop using Linux containers.
- [LM Studio](https://lmstudio.ai/).

Verify the command-line tools:

```powershell
git --version
python --version
uv --version
docker --version
docker compose version
lms --version
```

If needed, install Python through uv:

```powershell
uv python install 3.12
```

## 2. Install the required Python extras

The local runtime needs the Qdrant client and Sentence Transformers:

```powershell
uv sync --extra qdrant --extra local-models
```

The first use of `BAAI/bge-m3` may download a large model and initialize it. Startup can therefore
take noticeably longer the first time. Keep network access, cache permissions, and sufficient disk
space available.

## 3. Prepare LM Studio

In LM Studio:

1. Install and open LM Studio once.
2. Download `openai/gpt-oss-20b` (or another model that fits the machine).
3. Load the model and configure/start the local API server in LM Studio.

The control center only opens the visible desktop application. It does not manage the daemon,
models, or API server. Keep the identifier served by LM Studio aligned with `RAG_LLM__MODEL`.

## 4. Create `.env`

Copy the template:

```powershell
Copy-Item .env.example .env
```

Use this canonical local schema. Nested models use double underscores (`__`):

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

RAG_VECTOR_STORE__PROVIDER=qdrant
RAG_VECTOR_STORE__URL=http://127.0.0.1:6333
RAG_VECTOR_STORE__API_KEY=
RAG_VECTOR_STORE__INDEX_NAME=documents
RAG_VECTOR_STORE__NAMESPACE=default

RAG_CHUNKING__CHUNK_SIZE=800
RAG_CHUNKING__CHUNK_OVERLAP=120
RAG_RETRIEVAL__TOP_K=5
RAG_LOGGING__LEVEL=INFO
RAG_LOGGING__JSON=true
```

`rag serve`, `rag worker`, `rag doctor`, `rag enqueue`, and `rag query` load tokens and settings
from `.env`. You do not need to copy tokens into each PowerShell session. Never commit `.env`, and
replace the example token values before sharing the machine or exposing the API.

The 1024 dimension is part of the index contract for `BAAI/bge-m3`. If an existing Qdrant
collection was created with a different dimension, create a new collection name or intentionally
delete the disposable local collection before retrying.

## 5. Start the project control center

```powershell
uv run rag dashboard
```

Use **Start all**. For non-interactive startup use:

```powershell
.\scripts\control-center\start_all.ps1
```

This opens LM Studio Desktop, then starts Redis, Qdrant, the API, and the worker in dependency
order. LM Studio model/server readiness remains under your control in the desktop application.
Continue at step 10 after the startup summary reports success.

The remaining steps document the equivalent manual workflow for diagnostics and recovery.

## 6. Start Redis and Qdrant manually

```powershell
docker compose --profile qdrant up -d redis qdrant
docker compose --profile qdrant ps
```

The Compose file publishes:

- Redis container port `6379` as `127.0.0.1:6379` by default.
- Qdrant container port `6333` as `127.0.0.1:6333` by default.

Override the host ports with `REDIS_PORT` or `QDRANT_PORT`, and update the corresponding URLs in
`.env`. The loopback bindings intentionally prevent access from other hosts.

## 7. Run diagnostics

```powershell
uv run rag doctor
```

Every check should print `[OK]`. The command verifies:

- the `sentence-transformers` package;
- LM Studio connectivity and the `gpt-oss-20b` model identifier;
- Redis and Qdrant connectivity;
- the ingestion-root directory;
- the parent directory for SQLite.

The command creates missing ingestion and SQLite parent directories, but it does not download BGE,
send a generation request, or prove that a document can be indexed. Complete the following
end-to-end steps as well.

## 8. Start the API manually

Open a dedicated PowerShell window:

```powershell
uv run rag serve
```

The API listens on <http://127.0.0.1:8000>. On first startup, Sentence Transformers may download
and initialize `BAAI/bge-m3`; wait for Uvicorn to report that startup is complete.

Verify:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

The canonical `rag serve` entry point builds the local runtime: BGE-M3 embeddings, Qdrant vectors,
SQLite document metadata, LM Studio generation, and a Redis job queue. This differs from calling
`rag_app.api.create_app()` directly, whose uninjected defaults remain an in-memory demo for tests
and custom composition.

## 9. Start the worker manually

Open another dedicated PowerShell window:

```powershell
uv run rag worker
```

Keep both `rag serve` and `rag worker` running. They use the same Redis queue, Qdrant collection,
SQLite file, embedding model, and `.env` configuration.

## 10. Enqueue a document and query it

Put a supported file under `data/documents`, for example:

```text
data/documents/handbook.pdf
```

The worker rejects files and directories outside the resolved `RAG_INGESTION_ROOT`.

In a third PowerShell window:

```powershell
uv run rag enqueue data/documents/<file> --wait
```

`--wait` polls the API until the job succeeds, is cancelled, or reaches the dead-letter state. A
successful job has been loaded, chunked, embedded, written to Qdrant, and registered in SQLite.

Query the indexed content:

```powershell
uv run rag query "What does the document say?"
```

The CLI reads `RAG_API_TOKEN` from `.env` and calls the running API. Responses contain an answer,
citations, and latency.

## 11. Stop local infrastructure

When services were started by the control center, stop them gracefully with:

```powershell
.\scripts\control-center\stop_all.ps1
```

For the manual workflow, stop the API and worker with `Ctrl+C`, then:

```powershell
docker compose --profile qdrant down
```

This keeps Redis and Qdrant volumes. Remove local service data only when intentional:

```powershell
docker compose --profile qdrant down --volumes
Remove-Item .data\rag.sqlite3* -ErrorAction SilentlyContinue
```

## Production limitations

This flow is intended for one Windows development machine:

- SQLite and source files are host-local and require API/worker filesystem consistency.
- Redis and Qdrant have loopback-only host ports but no local TLS or credentials.
- LM Studio is not a hardened, replicated inference service.
- The API rate limiter is process-local; sparse retrieval uses hash sparse embeddings.
- Production deployments still need centralized secrets, authenticated service networking,
  durable source storage, malware scanning, tenant/ACL enforcement, backups, observability,
  resource limits, high availability, and domain evaluation.

Do not infer production readiness solely from a successful local ingestion and query.

## Next steps

- [Configuration reference](configuration.md)
- [Usage guide](usage.md)
- [Troubleshooting](troubleshooting.md)
- [Qdrant quickstart](https://qdrant.tech/documentation/quickstart/)
- [Docker Compose profiles](https://docs.docker.com/compose/how-tos/profiles/)
- [Sentence Transformers](https://sbert.net/)
