# Production RAG Template

A production-oriented RAG codebase with a functional Windows-local runtime and a Textual project
control center. The dashboard manages Redis, Qdrant, the API, and the ingestion worker from one
terminal; for LM Studio it only opens the desktop application and reports its process state. The
underlying `rag serve` command
still composes LM Studio, Sentence Transformers, Qdrant, Redis, and SQLite into an end-to-end API;
ingestion jobs handled by `rag worker` become queryable through that API.

## Windows-local quickstart

Prerequisites:

- Python 3.12 and [uv](https://docs.astral.sh/uv/).
- Docker Desktop using Linux containers.
- [LM Studio](https://lmstudio.ai/) installed. Configure the model and local API server in the
  desktop application; the control center only opens the application.

From the repository root in PowerShell:

```powershell
uv sync --extra qdrant --extra local-models
Copy-Item .env.example .env
uv run rag services check --all
```

Start everything with the interactive dashboard:

```powershell
uv run rag dashboard
```

Use **Start all** in the dashboard, or start the full stack without a UI:

```powershell
.\scripts\control-center\start_all.ps1
```

To open only LM Studio Desktop:

```powershell
.\scripts\control-center\start_lm_studio.ps1
```

The startup sequence validates the host, opens the visible LM Studio Desktop application, starts
Redis and Qdrant through Docker Compose, then starts the API and worker in dependency order. It does
not load/unload models, start/stop the LM Studio API server, or close the desktop application.

Use the canonical nested environment names in `.env`. For the local flow, verify at least:

```dotenv
RAG_RUNTIME_MODE=local
RAG_API_TOKEN=local-api-token
RAG_ADMIN_TOKEN=local-admin-token
RAG_REDIS_URL=redis://127.0.0.1:6379/0
RAG_SQLITE_PATH=.data/rag.sqlite3
RAG_INGESTION_ROOT=data/documents

RAG_LLM__PROVIDER=local
RAG_LLM__MODEL=gpt-oss-20b
RAG_LLM__BASE_URL=http://127.0.0.1:1234/v1

RAG_EMBEDDINGS__PROVIDER=local
RAG_EMBEDDINGS__MODEL=BAAI/bge-m3
RAG_EMBEDDINGS__DIMENSIONS=1024

RAG_VECTOR_STORE__PROVIDER=qdrant
RAG_VECTOR_STORE__URL=http://127.0.0.1:6333
RAG_VECTOR_STORE__INDEX_NAME=documents
```

Tokens and runtime `RAG_*` values are loaded from `.env`; exporting them manually is not required.
The control-center YAML supports `${NAME:-default}` placeholders but does not print `.env` values.
Do not commit `.env`.

## Project control center

The dark Textual UI provides an overview, per-service state and PID, live log tails, CPU/RAM/GPU
telemetry, AI model/VRAM details, diagnostics, and the active non-secret configuration. Status
colors are consistent throughout: green is running, yellow is transitioning, red is unhealthy or
failed, and gray is stopped.

Useful headless commands:

```powershell
uv run rag services status --all
uv run rag services start lm_studio redis qdrant
uv run rag services restart api worker
uv run rag services recover
uv run rag services logs api --lines 200
uv run rag services stop --all
```

Runtime state and service logs live under `.data/control-center/` and are intentionally ignored by
Git. Service definitions are declarative YAML files under `ops/control-center/services/`; adding a
new process or Compose service does not require changing the UI.

Create a fresh extensible configuration non-destructively with `uv run rag services init`. Existing
files are preserved unless `--force` is explicitly supplied.

You can still start the API and worker manually in separate PowerShell windows:

```powershell
uv run rag serve
```

```powershell
uv run rag worker
```

Place a supported file under `data/documents`, then use a third window:

```powershell
uv run rag enqueue data/documents/<file> --wait
uv run rag query "What does the document say?"
```

The first API or worker startup can take time while Sentence Transformers downloads and initializes
`BAAI/bge-m3`. That model produces 1024-dimensional embeddings, so the Qdrant collection must use
dimension `1024`.

Redis is published only on `127.0.0.1:6379` by default and Qdrant only on
`127.0.0.1:6333`. SQLite document metadata is stored in `.data/rag.sqlite3`; vectors are persisted
in the Qdrant volume. The worker only accepts sources inside the configured
`data/documents` ingestion root.

## Runtime entry points

`uv run rag serve` is the canonical API entry point. It calls the environment-driven composition
root and uses the local persistent runtime.

`rag_app.api.create_app` remains a dependency-injectable factory for tests, custom composition, and
the in-memory demo. Calling it directly without injected adapters does **not** select the local
Qdrant/SQLite runtime.

## Documentation

- [Project control center](docs/control-center.md): dashboard, service manifests, LM Studio
  automation, scripts, and troubleshooting.
- [Getting started](docs/getting-started.md): complete Windows-local setup and first ingestion.
- [Configuration](docs/configuration.md): canonical environment schema and ports.
- [Usage](docs/usage.md): CLI, API, loaders, and local-runtime behavior.
- [Troubleshooting](docs/troubleshooting.md): diagnostics for LM Studio, BGE, Redis, Qdrant,
  SQLite, and ingestion.
- [Architecture](docs/architecture.md), [deployment](docs/deployment.md),
  [security](docs/security.md), and [evaluation](docs/evaluation.md): broader design material.

## Future roadmap

Planned updates will extend the functional Windows-local workflow toward distributed production
deployments:

- Add a shared PostgreSQL document registry so API and worker instances can run on separate hosts.
- Provide secured Redis and Qdrant configurations with TLS, authentication, and network isolation.
- Support scalable model-serving providers alongside LM Studio for replicated inference.
- Add distributed rate limiting, stronger tenant and ACL enforcement, and production-grade sparse
  embeddings.
- Introduce managed secrets, automated backups, malware scanning, resource limits, and high
  availability.
- Expand OpenTelemetry observability and domain-specific evaluation and regression suites.

## Validation

```powershell
uv run pytest -q
uv run ruff check src tests scripts examples
uv run mypy src/rag_app
```

The starter evaluation data is illustrative only. Replace it with reviewed, domain-specific cases
before making quality claims.

## License

[MIT](LICENSE)

Contributions follow [CONTRIBUTING.md](CONTRIBUTING.md). Report vulnerabilities according to
[SECURITY.md](SECURITY.md).
