# AGENTS.md

## Purpose and maintenance contract

This is the repository-level orientation guide for coding agents. Read it before changing code.
It describes the implementation that is currently wired, not only the intended architecture in the
documentation.

Keep this file current in the same change whenever a major update changes any of the following:

- runtime entry points or process boundaries;
- ingestion, indexing, retrieval, or generation flow;
- persistence ownership or data consistency rules;
- public API/CLI contracts;
- provider support or required configuration;
- security invariants, deployment topology, or test commands;
- major directories or the responsibility of a package.

For small fixes, formatting, dependency bumps, and internal refactors that do not change those
facts, no update is necessary. For a major update, revise the affected sections, remove obsolete
warnings, add new paths/commands, and update the verification date below. Validate statements
against code and tests; do not copy an outdated design document into this file.

Last verified against the repository: **2026-07-22**.

## Project in one paragraph

`prod-rag` is a Python 3.12 production-oriented RAG template with a Textual terminal control center.
Its canonical Windows-local runtime uses a FastAPI API process and a separate Redis-backed worker
process. Both compose local Sentence Transformers embeddings (BGE-M3 by default), Qdrant
dense+sparse vectors, a shared SQLite document registry, and an OpenAI-compatible LM Studio chat
model. The control center opens LM Studio Desktop, manages Redis/Qdrant containers and the host
API/worker, and monitors the local machine while preserving the same canonical runtime entry
points. File ingestion is queued, validated, loaded, chunked, embedded, indexed, and registered;
queries run hybrid dense/lexical retrieval followed by a bounded LangGraph workflow that enforces
evidence-backed citations or abstains. In-memory implementations remain available for deterministic
tests, examples, and custom composition.

## Start here

Use these as the primary navigation points:

- `README.md`: short Windows-local quickstart and high-level status.
- `src/rag_app/main.py`: `rag` CLI and all user-facing command entry points.
- `src/rag_app/runtime/composition.py`: canonical environment-driven composition root.
- `src/rag_app/api/app.py`: injectable FastAPI boundary and route/middleware definitions.
- `src/rag_app/runtime/service.py`: persistent local service joining indexing, retrieval, generation,
  and SQLite.
- `src/rag_app/runtime/worker.py`: bridge from ingestion jobs to persistent indexing.
- `src/rag_app/control_center/orchestrator.py`: dependency-aware lifecycle facade shared by the
  dashboard and headless service commands.
- `ops/control-center/settings.yaml` and `ops/control-center/services/*.yaml`: canonical local
  control-center topology and per-service lifecycle/health contracts.
- `docs/control-center.md`: installation, service manifests, LM Studio Desktop launcher, scripts, and
  troubleshooting for the terminal dashboard.
- `docs/getting-started.md`, `docs/configuration.md`, and `docs/troubleshooting.md`: authoritative
  local operator workflow.
- `tests/unit/test_runtime.py`: smallest offline end-to-end examples of the real service and worker
  flow.

When prose and executable behavior conflict, use this priority: current code and tests, this file,
then the remaining docs. Fix the stale documentation as part of a behavior-changing update.

## Canonical runtime versus demo factories

This distinction is essential:

- `uv run rag serve` is the canonical API command. It starts Uvicorn with
  `rag_app.runtime.composition:create_runtime_app`, loads `Settings`, builds `LocalRAGService`, and
  injects `RedisJobQueue` into the FastAPI factory.
- `uv run rag worker` is the canonical worker command. It calls
  `rag_app.runtime.composition.run_local_worker`, builds the same local adapters, and consumes Redis
  `ingest` jobs.
- `rag_app.api.app.create_app()` is a lower-level dependency-injection boundary. With no injected
  service it uses `InMemoryRAGService`; with no queue it chooses Redis only when a compatible Redis
  environment variable exists, otherwise `InMemoryJobQueue`.
- `uvicorn rag_app.api.app:create_app --factory` therefore does **not** create the persistent local
  RAG runtime. Do not use it as a substitute for `rag serve` unless the in-memory demo is intended.
- `rag ingest` is only a synchronous loader/chunker diagnostic. It does not enqueue, embed, write
  Qdrant, or make content queryable. Use `rag enqueue ... --wait` for real ingestion.
- `uv run rag dashboard` is the interactive local operator surface. `rag services
  start|stop|restart|status|recover|check|logs` uses the same `ControlCenter` facade without a UI.
  Thin PowerShell/POSIX wrappers live in `scripts/control-center/`; do not put lifecycle policy in
  those wrappers.
- `rag services init` creates an extensible default YAML topology without overwriting existing
  files unless `--force` is explicit. The committed `ops/control-center` topology is authoritative
  for this repository and generated defaults must stay behaviorally identical.

## Active architecture

### Project control center

`rag_app.control_center.ControlCenter` is the single orchestration API used by both Textual and the
headless CLI. It loads a validated, acyclic service graph from YAML, starts dependencies in
topological order, stops dependents in reverse order, runs required preflight checks before
mutation, waits on bounded health probes, and performs graceful shutdown. A one-shot
`restart_failed()` recovery pass exists; there is deliberately no unbounded automatic retry loop.

The default drivers and ownership rules are:

- `process`: launches `rag serve`/`rag worker` without a command shell, redirects each process to a
  dedicated log, and persists PID, process creation time, and a one-way command fingerprint. Never
  kill a PID unless its recorded process identity still matches.
- `docker_compose`: operates only the configured Redis or Qdrant Compose service and reads status
  and logs from Docker. Normal stop does not delete volumes.
- `lm_studio`: launch-only adapter. Start invokes the platform launcher under
  `scripts/control-center/start_lm_studio.*`, status checks the desktop process, and stop leaves the
  visible application running. It does not call `lms`, manage the daemon/server, or load/unload a
  model. Operators configure the OpenAI-compatible endpoint inside LM Studio.
- `external`: health-checks a dependency but does not claim lifecycle ownership.

Control settings are in `ops/control-center/settings.yaml`; individual services are in
`ops/control-center/services/`. Paths are project-root relative. `${NAME}` and
`${NAME:-default}` interpolation uses `.env` with process-environment precedence, but expanded
values and secrets must never appear in UI, logs, state, or validation errors. Runtime ownership
state and logs live under `.data/control-center/` and are disposable local operational data.

The UI lives in `control_center/ui/` and polls the facade through Textual workers so lifecycle,
Docker, HTTP, and telemetry calls never block terminal input. Its Config view intentionally shows
only manifest structure. System/AI telemetry is best effort: psutil supplies CPU/RAM,
`nvidia-smi` supplies real GPU/VRAM data when present, and the protected API metrics endpoint
supplies query rate and observed query latency. Missing telemetry is shown as unavailable, never
fabricated.

### Process and storage topology

```text
Client/CLI
    -> FastAPI API process
       -> Redis (ingestion jobs and idempotency records)
       -> SQLite (document CRUD/metadata)
       -> Qdrant (query vectors and chunk payloads)
       -> LM Studio/OpenAI-compatible LLM (answer generation)

Redis
    -> Worker process
       -> local file/directory loaders under RAG_INGESTION_ROOT
       -> embeddings -> Qdrant
       -> document/chunk registry -> SQLite
```

The API and worker are independent processes but must use the same `.env`, Redis database, Qdrant
collection, SQLite file, ingestion root, embedding model, and embedding dimension. Each process
constructs its own embedding provider and in-memory embedding cache. The local model may therefore
be loaded once per process.

### API lifecycle and routes

`create_runtime_app()` builds the service and queue, then delegates HTTP concerns to
`api/app.py`. FastAPI lifespan calls optional `startup()`/`shutdown()` hooks and closes the queue.
`LocalRAGService.startup()` initializes SQLite, probes the real embedding dimension, creates the
Qdrant collection if absent, and only then reports ready.

Public endpoints:

- `GET /health/live`: process liveness only.
- `GET /health/ready`: service readiness.
- `POST/GET/DELETE /api/v1/ingestion/jobs...`: enqueue, inspect, list, or cancel jobs.
- `POST/GET/DELETE /api/v1/documents...`: synchronous text indexing and document CRUD.
- `POST /api/v1/query`: retrieve and generate a complete answer.
- `POST /api/v1/query/stream`: SSE facade. It computes the full answer first, then emits words as
  `token` events, followed by `citations` and `done`; it is not provider-token streaming.
- `GET /metrics` and `GET /debug/state`: admin-token protected operational endpoints. Metrics
  include ingestion-job gauges plus process-local completed-query count, rolling requests/minute,
  and last/average query latency used by the AI Engine dashboard.

All `/api/*` routes share optional bearer-token authentication, a process-local per-IP rate limit,
a `Content-Length` body-size check, and `X-Request-ID`. If `RAG_API_TOKEN` is empty, API bearer auth
is disabled. If `RAG_ADMIN_TOKEN` is empty, admin endpoints return 503.

### Asynchronous ingestion flow

1. `POST /api/v1/ingestion/jobs` enqueues kind `ingest`. The default idempotency key is
   `ingest:{source}`; a caller may supply a key.
2. `RedisJobQueue` stores job JSON and an idempotency mapping, then puts the job ID on the ready
   list. Jobs transition `pending -> running -> succeeded`; failures retry up to three attempts and
   then become `dead_letter`. Cancellation is best effort and does not interrupt a running parser.
3. `LocalIngestionHandler` resolves the source with `strict=True` and rejects anything outside the
   resolved `RAG_INGESTION_ROOT`. Directories are recursively discovered in deterministic order.
4. `IngestionPipeline` hashes the file before and after loading, routes to the appropriate loader,
   applies `StructureAwareChunker`, assigns deterministic document/content/chunk metadata, and uses
   a process-local `InMemoryDocumentRegistry` for within-worker idempotency/versioning.
5. The handler passes loaded chunks to `LocalRAGService.index_ingested_document()`.
6. `IndexingService` creates dense embeddings and hash-based sparse embeddings, upserts Qdrant
   named vectors (`dense` and `sparse`), and returns stored record IDs.
7. `SQLiteDocumentRepository` upserts the document, content hash, version, full extracted text,
   metadata, and current vector record IDs. Stale vector IDs from an older version are deleted.

Redis idempotency keys currently have no TTL or reset endpoint. Replaying an old source with the
same key returns the original job even after content changes; use an intentional new idempotency key
when reprocessing is required.

### Direct text indexing flow

`POST /api/v1/documents` bypasses the worker and calls `LocalRAGService.add_document()` in the API
process. It normalizes and hashes text, skips an unchanged source, versions a changed source,
chunks it, indexes it, writes SQLite, and removes stale vectors. This route accepts text, not a file
upload.

### Loader routing and safety

Loader selection lives in `ingestion/document_loaders.py`; heavy parser imports are intentionally
lazy.

- Docling: `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.html`, `.xhtml`, `.md`.
- Unstructured: `.doc`, `.ppt`, `.xls`, `.rtf`, `.epub`, `.eml`, `.msg`, `.odt`, `.odp`, `.ods`,
  `.rst`, `.org`.
- Plain UTF-8 text: `.txt`, `.log`, `.py`, `.js`, `.ts`, `.sh`, `.yaml`, `.yml`, `.ini`, `.cfg`,
  `.ipynb`.
- Structured loaders: `.csv`, `.json`.
- PDF falls back from Docling to PyPDF.

Local files are restricted by extension, regular-file status, optional allowed root, MIME/extension
consistency, and a default 100 MiB limit. A standalone `WebDocumentLoader` rejects obvious SSRF
targets, but the current ingestion-job API accepts local file/directory paths only; web ingestion is
not wired into `LocalIngestionHandler`.

### Retrieval flow

`LocalRAGService.query()` creates a LangGraph workflow per request and wraps `HybridRetriever` in
`GraphRetrieverAdapter`. The active adapter always requests the `balanced` profile using the API's
`top_k` and client filters.

Balanced retrieval does the following:

1. embed the query;
2. run dense Qdrant search and Qdrant sparse search concurrently;
3. prefilter both branches using metadata filters;
4. combine rankings with reciprocal rank fusion (RRF, rank constant 60);
5. optionally expand context if hooks were injected (none are injected by local composition);
6. deduplicate by record ID and content hash;
7. apply optional context budgets and final `top_k`.

The retrieval package also implements `fast` (dense only) and `quality` (hybrid, reranking,
expansion-capable) profiles, BM25 scan fallback, adjacent/parent expansion hooks, and a cross-encoder
reranker. These are tested extension points but are not selected by the current API composition.

Filters come from the client in the current API. The retrieval layer supports tenant/ACL matching,
but the server does not yet derive trusted tenant/principal context. Do not claim multi-tenant
isolation until server-side authorization injects those filters.

### Generation flow

`generation/workflow.py` compiles this finite graph:

```text
validate request -> normalize query -> retrieve -> grade evidence
    -> evidence found: build context -> generate -> validate citations -> finalize
    -> no evidence: one LLM query rewrite -> retrieve again -> abstain if still empty
```

The graph permits exactly one rewrite, caps evidence/chunk/context sizes, requires generated
citations like `[C1]` to reference supplied evidence, normalizes full-width citation brackets, and
replaces missing/invalid citations with a deterministic abstention. Terminal state clears raw
evidence/context bodies. `generation/persistence.py` can build a Postgres checkpointer, but the local
runtime compiles the graph without one.

## Persistence and identity ownership

- Redis: job JSON, ready/dead lists, and job idempotency mappings.
- Qdrant: chunk text, metadata, original application record ID, and dense/sparse vectors. Arbitrary
  application IDs are deterministically converted to Qdrant UUIDs while `_record_id` preserves the
  original value.
- SQLite (`.data/rag.sqlite3` by default): one row per unique source, full extracted text, metadata,
  content hash, version, and vector record IDs. WAL and a 30-second busy timeout support the host
  API/worker pair; this is not a distributed registry.
- `InMemoryDocumentRegistry`: transient loader/chunker result cache inside one worker process. It is
  not the durable source of truth; `SQLiteDocumentRepository` is.
- `data/documents`: recommended local ingestion root. Runtime data, `.env`, test-generated fixtures,
  SQLite files, caches, and distributions are ignored by Git.

Changing embedding model/dimension, chunk identity rules, collection name, or metadata schema is a
data migration/reindexing event. Do not reuse an incompatible Qdrant collection. Indexing and
registry writes are not transactional across Qdrant and SQLite, so partial failures can require a
controlled reconciliation or full reindex of disposable local data.

## Configuration model

`core/config.py::Settings` is the canonical schema. It loads `.env` and process environment with
prefix `RAG_`; nested fields use `__`. Process environment overrides `.env`. `get_settings()` is
cached, so tests that mutate environment must clear the cache as existing tests do.

Important groups:

- application: `RAG_RUNTIME_MODE`, `RAG_ENVIRONMENT`, `RAG_DEBUG`, API/admin tokens;
- persistence: `RAG_REDIS_URL`, `RAG_SQLITE_PATH`, `RAG_INGESTION_ROOT`;
- LLM: `RAG_LLM__PROVIDER`, model, key, base URL, timeout, retries, temperature, tokens;
- embeddings: provider, model, dimensions, batch size;
- vector store: provider, URL/key, index name, namespace, timeout;
- chunking, retrieval, and logging nested models.

Control-center configuration is a separate schema in `control_center/models.py`; do not add
service lifecycle fields to `core/config.py::Settings`. `load_control_center_config()` combines the
root YAML and deterministic per-service manifests, resolves paths, expands environment placeholders,
and validates driver-specific fields/dependencies. `ControlCenter.from_path()` is the normal
composition boundary. Keep generated defaults, committed manifests, docs, and artifact tests in
sync whenever this schema or default topology changes. `required_uv_extras` controls which extras
the preflight passes to `uv sync --check`; keep it aligned with the import packages and commands
needed by the configured API/worker runtime.

Current composition support is narrower than the available adapters:

- runtime mode: only `local`; `distributed` raises an error;
- embeddings in local composition: local/Hugging Face/Sentence Transformers or deterministic hash;
- vector store in local composition: Qdrant or memory;
- LLM factory: local, OpenAI, OpenAI-compatible, or Anthropic;
- PgVector, OpenAI-compatible embeddings, Postgres checkpointer, and the PostgreSQL registry
  migration exist but are not wired into `build_local_service()`.

Do not assume every declared setting is active. At this verification point,
`RAG_VECTOR_STORE__NAMESPACE`, vector-store timeout, and `RAG_RETRIEVAL__TOP_K` are not consumed by
the local composition. API requests use the schema default `top_k=5`. The score threshold is passed
to evidence grading **after RRF**, so it is compared with fused rank scores, not raw cosine scores;
a cosine-style value such as `0.7` would normally reject all fused evidence.

Never read or print `.env` in diagnostics, tests, or agent reports. Use `.env.example` and
`docs/configuration.md` for names and safe sample values.

## Repository structure

```text
.
|-- src/rag_app/                 installable application package
|   |-- main.py                  argparse CLI: runtime commands plus dashboard/services control
|   |-- api/
|   |   |-- app.py               FastAPI factory, middleware, routes, health/admin endpoints
|   |   |-- models.py            public Pydantic request/response schemas
|   |   `-- service.py           RAGService protocol + in-memory demo service
|   |-- runtime/
|   |   |-- composition.py       canonical settings-to-adapters composition root
|   |   |-- service.py           LocalRAGService + graph/retriever adapter
|   |   |-- repository.py        async wrapper around durable SQLite document registry
|   |   |-- worker.py            local-source ingestion handler and indexing bridge
|   |   `-- doctor.py            LM Studio/Redis/Qdrant/filesystem dependency checks
|   |-- control_center/
|   |   |-- orchestrator.py      dependency graph, preflight, lifecycle, health, recovery facade
|   |   |-- config.py            YAML/env loading and non-destructive default initialization
|   |   |-- models.py            service/config/status/result/telemetry contracts
|   |   |-- drivers.py           process/Compose lifecycle plus launch-only LM Studio adapter
|   |   |-- health.py            bounded process/HTTP/TCP/command readiness probes
|   |   |-- state.py             atomic ownership metadata and interprocess lifecycle lock
|   |   |-- diagnostics.py       host checks plus CPU/RAM/GPU/API inference telemetry
|   |   `-- ui/                  Textual dashboard, styling, protocol, presentation helpers
|   |-- ingestion/
|   |   |-- document_loaders.py  validation, loader routing, metadata normalization
|   |   |-- sources.py           deterministic file/directory discovery abstractions
|   |   |-- chunking.py          structure-aware and paragraph/window chunkers
|   |   |-- pipeline.py          hashing, stable IDs, versioning, orchestration
|   |   |-- registry.py          thread-safe in-process latest-source registry
|   |   `-- models.py            typed ingestion results and records
|   |-- indexing/
|   |   |-- embeddings.py        dense/sparse providers, batching, in-memory cache
|   |   |-- vector_stores.py     memory, Qdrant, and PgVector adapters + filters
|   |   `-- service.py           chunks -> embeddings -> VectorRecords -> store
|   |-- retrieval/
|   |   |-- engine.py            profiles, hybrid search, RRF, expansion, budgets, diagnostics
|   |   `-- rerankers.py         identity and lazy cross-encoder rerankers
|   |-- generation/
|   |   |-- workflow.py          bounded citation-validating LangGraph workflow
|   |   |-- state.py             versioned graph state/response TypedDicts
|   |   |-- prompts.py           grounded answer/rewrite prompts and abstention text
|   |   `-- persistence.py       optional Postgres LangGraph checkpointer
|   |-- workers/
|   |   |-- queue.py             job model/state machine; memory and Redis queues
|   |   `-- runner.py            generic claim/dispatch/complete/fail loop
|   |-- llm/
|   |   |-- config.py            provider-neutral runtime LLM config adapter
|   |   `-- factory.py           lazy ChatOpenAI/ChatAnthropic construction
|   |-- evaluation/
|   |   |-- metrics.py           retrieval/citation/latency metrics
|   |   `-- runner.py            golden JSONL loading, reports, regression gates
|   |-- domain/                  validated provider-neutral models/protocols
|   `-- core/                    settings, errors, IDs, logging, retry, telemetry
|-- tests/
|   |-- unit/                    default offline behavior tests; loader suite is marked slow
|   |-- e2e/                     in-process FastAPI API/queue contract tests
|   |-- integration/             opt-in Redis/Qdrant/PgVector contracts
|   `-- data/                    manual/generated loader fixtures (normally Git-ignored)
|-- evals/golden_dataset.jsonl   illustrative evaluation cases, not a quality claim
|-- examples/                    no-service local pipeline and minimal HTTP client
|-- scripts/run_worker.py        container/script shim to runtime worker_main
|-- scripts/control-center/      thin cross-platform dashboard/start/stop/restart/status wrappers
|-- ops/control-center/          canonical control settings and declarative service manifests
|-- migrations/001_registry.sql  future PostgreSQL registry/jobs schema; currently unwired
|-- docs/                        setup, usage, architecture, ADRs, security, ops, evaluation
|-- .github/workflows/           cross-platform CI, security scans, tagged releases
|-- pyproject.toml               packaging, dependencies/extras, CLI, pytest/ruff/mypy config
|-- uv.lock                      reproducible dependency lock
|-- docker-compose.yml           local service profiles and aspirational full stack
|-- Dockerfile                   image build; see known mismatch below
|-- .env.example                 safe canonical local configuration template
`-- README.md                    project overview and quickstart
```

`domain/` is a parallel provider-neutral model/protocol layer and is currently exercised mainly by
its own tests. The active local runtime contracts live in `api/service.py`, `indexing/`,
`retrieval/`, and `runtime/`. Do not assume the domain protocols are already the service wiring.

Package `__init__.py` files intentionally re-export public names. When adding/removing a public
adapter, update the corresponding exports and tests.

## Development and verification

Use `uv`; do not hand-edit `uv.lock`.

```powershell
uv sync --all-extras
uv run python -m compileall -q src scripts
uv run ruff check src tests scripts examples
uv run mypy src/rag_app
uv run pytest -q
```

`pyproject.toml` defaults pytest to `not slow and not integration and not legacy`. This default
includes unit tests and in-process API e2e tests without requiring LM Studio, Redis, or Qdrant.

Useful targeted commands:

```powershell
uv run pytest -q tests/unit/test_runtime.py
uv run pytest -q tests/e2e/test_api.py
uv run pytest -q tests/unit/test_control_center_core.py tests/unit/test_control_center_artifacts.py tests/unit/test_control_center_ui.py
uv run pytest -q -m slow tests/unit/test_loaders.py
uv run pytest -q -m integration tests/integration/test_redis_queue.py
uv run pytest -q -m integration tests/integration/test_vector_store_contract.py
```

Integration vector-store tests require `RAG_TEST_QDRANT_URL` or `RAG_TEST_PGVECTOR_DSN` and skip
when unavailable. Redis integration defaults to local Redis. Slow/legacy loader tests can require
large parser/model dependencies, network access, or LibreOffice. Prefer deterministic hash
embeddings, fake LLMs, in-memory vector storage, temporary SQLite, and injected queues in ordinary
unit tests.

For behavior changes, run the narrowest relevant tests while iterating and the full default suite,
Ruff, and mypy before handoff. Add tests at the boundary being changed:

- API/auth/job contract -> `tests/e2e/test_api.py`;
- runtime composition/persistence -> `tests/unit/test_runtime.py`;
- file ingestion/loaders -> ingestion and loader tests;
- vector providers -> indexing unit tests plus the opt-in contract test;
- retrieval/reranking -> `tests/unit/test_retrieval.py`;
- graph/prompt/citation rules -> `tests/unit/test_generation.py`;
- settings -> `tests/unit/test_config.py`;
- evaluation rules -> `tests/unit/test_evaluation.py`.
- service lifecycle/config/LM ownership -> control-center core and artifact tests;
- Textual presentation/actions -> control-center UI tests using a fake facade and Textual's
  headless test harness.

## Change guidelines and invariants

- Preserve dependency injection at the FastAPI/service, queue/worker, embeddings/store, and
  retriever/LLM boundaries. Do not bury concrete service creation inside route handlers.
- Keep heavy/optional dependencies lazily imported so core modules and offline tests remain usable.
- Keep blocking parsing, SQLite operations, and local-model encoding off the event loop via
  `asyncio.to_thread` or an equivalent boundary.
- Preserve stable metadata keys across ingestion/indexing/retrieval: `source`, `document_id`,
  `chunk_id`, `chunk_index`, `document_version`, `content_hash`, and optional tenant/ACL fields.
- Treat changes to stable IDs, metadata, chunking version, or embedding dimensions as migrations;
  test update/delete/reindex behavior, not only first insertion.
- Keep retrieval filters enforced before fusion in every search branch. Client-provided filters are
  not authorization; trusted tenant/ACL context must be server-derived when implemented.
- Treat document text and retrieved context as untrusted. Do not let evidence invoke tools or
  override system policy, and never log corpora, prompts, bearer tokens, or model keys.
- Keep generation loops bounded and preserve deterministic abstention on insufficient evidence or
  invalid citations.
- Maintain job state transitions, retry limits, idempotency, and cancellation semantics consistently
  across both memory and Redis queues. Queue contract changes require tests for both.
- Update API schemas, examples, operator docs, and evaluation fixtures when public contracts change.
- New optional providers need an explicit dependency extra, lazy import, configuration validation,
  composition wiring, package export, unit tests, and an opt-in integration contract where useful.
- Do not commit `.env`, `.data`, downloaded models, private corpora, generated indexes, or secrets.
- Keep the dashboard and CLI on the same `ControlCenter` facade; scripts must remain thin. Run
  blocking subprocess/filesystem/resource probes off Textual's event path and bound every command
  and health wait.
- Preserve lifecycle ownership. Status and diagnostics are read-only; they must not launch LM
  Studio, start containers, or mutate service state except to reconcile stale internal metadata.
  Never stop an external/pre-existing process or container that the control center did not claim,
  and never close LM Studio Desktop from the control center.
- Treat a service manifest or command as untrusted operator input: execute argument lists without a
  shell, keep paths inside the intended project where required, redact credentials/URL query data,
  and never persist environment values. LM model/server configuration belongs to the desktop app;
  do not add implicit downloads or `lms` lifecycle calls to the launch-only driver.

## Common change map

- Add a file type: update loader extension sets/router, source discovery, loader dependencies,
  fixtures/tests, and usage/security docs.
- Change chunking or identity: update `ingestion/chunking.py`, `ingestion/pipeline.py`, direct-text
  chunking in `runtime/service.py`, schema/chunking versions, update/reindex tests, and migration
  guidance.
- Add an embedding/vector provider: implement the protocol, wire `core/config.py` and
  `runtime/composition.py`, add an extra if optional, test dimensions/filters/delete, and document
  collection migration requirements.
- Change query quality: update retrieval tests and the golden evaluation path; distinguish raw
  dense scores, lexical scores, RRF scores, and reranker scores.
- Change graph behavior/prompts: update generation state/version if persisted shape changes, bounded
  routing tests, citation tests, evaluation expectations, and security docs.
- Change job semantics: keep memory and Redis adapters aligned, update API job responses, worker
  behavior, operations docs, and retry/dead-letter tests.
- Change API routes/auth: update `api/models.py`, `api/app.py`, e2e tests, examples, and docs; retain
  request IDs and avoid exposing secrets in errors.
- Add distributed runtime: implement a real shared registry/source strategy and composition instead
  of relaxing the current `runtime_mode` guard around host-local SQLite/files.
- Add/change a managed service: update its manifest/schema, dependency and reverse-stop tests,
  health contract, diagnostics, log behavior, operator docs, generated defaults, and dashboard
  assumptions. Driver changes require ownership/failure/timeout tests.

## Known gaps and repository mismatches

These are current facts to account for, not instructions to hide or work around them silently:

- `docs/project-architecture-detailed.mmd` is partly stale. It says ingestion stops before indexing
  and that the graph lacks an async retriever bridge. `LocalIngestionHandler` and
  `GraphRetrieverAdapter` now close both gaps. It also presents the uninjected API factory more
  prominently than the canonical `rag serve` composition.
- The Dockerfile starts `rag_app.api.app:create_app`, so the API image uses the in-memory service
  rather than `create_runtime_app`. Its default extras omit `local-models`, while the worker command
  requires Sentence Transformers. Compose also does not currently mount a shared ingestion/SQLite
  volume into API and worker. The documented, functional local flow runs API/worker on the Windows
  host and only Redis/Qdrant in containers.
- PgVector, PostgreSQL registry tables, Postgres graph checkpoints, quality reranking/expansion,
  OpenAI-compatible embeddings, and `distributed` mode are implemented pieces or roadmap elements,
  not the currently composed end-to-end runtime.
- The current API accepts client filters but does not derive tenant/ACL filters from authenticated
  identity. Tokens are shared bearer secrets, rate limiting is process-local, and local service
  ports are unauthenticated development defaults. This is not production multi-tenancy.
- SSE is post-completion word splitting, not true incremental LLM streaming.
- The repository license file is MIT, while `pyproject.toml` declares Apache-2.0 and README labels
  the link as MIT. Resolve this deliberately with project-owner input; do not guess the intended
  license during an unrelated change.

When one of these gaps is fixed, update the relevant code/tests/docs and remove or rewrite the item
here in the same change.
