# Production RAG Template

A production-oriented RAG codebase with a functional Windows-local runtime. The default
`rag serve` command composes LM Studio, Sentence Transformers, Qdrant, Redis, and SQLite into an
end-to-end API; ingestion jobs handled by `rag worker` become queryable through that API.

## Windows-local quickstart

Prerequisites:

- Python 3.12 and [uv](https://docs.astral.sh/uv/).
- Docker Desktop using Linux containers.
- [LM Studio](https://lmstudio.ai/) with `gpt-oss-20b` loaded and its OpenAI-compatible server
  listening on `http://127.0.0.1:1234/v1`.

From the repository root in PowerShell:

```powershell
uv sync --extra qdrant --extra local-models
Copy-Item .env.example .env
docker compose --profile qdrant up -d redis qdrant
uv run rag doctor
```

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

Tokens and all other `RAG_*` values are loaded from `.env`; exporting them manually is not
required. Do not commit `.env`.

Start the API and worker in separate PowerShell windows:

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
