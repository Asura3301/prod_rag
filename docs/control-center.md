# Project Control Center

The Project Control Center is the supported local operator interface for starting, stopping,
inspecting, and troubleshooting the complete `prod-rag` runtime. It provides both a Textual
terminal dashboard and scriptable CLI commands. The same orchestration layer powers both surfaces,
so a service started from the dashboard can be inspected or stopped from the CLI and vice versa.

The default service graph is:

```text
LM Studio Desktop (launch-only) ─┐
Redis ───────────────────────────┼──> Backend API
Qdrant ─────────────────────────┘
Redis ───────────┐
Qdrant ──────────┴──> Ingestion Worker
```

SQLite is embedded in the API and worker processes, not managed as a separate daemon. The terminal
dashboard is the project frontend; this repository does not currently contain a browser frontend.

## Technology choice

The control center uses Python + Textual (with Rich rendering). This keeps orchestration, models,
diagnostics, and UI in the same typed Python runtime as the RAG application, while still providing
responsive layouts, keyboard navigation, tables, tabs, colors, and non-blocking async workers on
Windows, Linux, and macOS. `psutil` supplies host/process telemetry, `httpx` performs bounded health
checks, and Pydantic + PyYAML validate declarative manifests. Electron/React would add a separate
Node build, packaging, IPC, and desktop security boundary without improving this terminal-first
operator workflow; it remains an option only if a true graphical desktop product is required.

## Installation

Install the prerequisites:

- Python 3.12 or newer;
- [uv](https://docs.astral.sh/uv/);
- Docker Desktop with Linux containers and the `docker compose` plugin;
- [LM Studio](https://lmstudio.ai/) Desktop installed.

Verify the external tools from a new terminal:

```powershell
uv --version
docker compose version
```

Install the project, Qdrant adapter, and local embedding provider:

```powershell
uv sync --extra qdrant --extra local-models
```

Create the application configuration if it does not exist:

```powershell
Copy-Item .env.example .env
```

Do not commit `.env`. The control center never prints its contents.

Set `RAG_ADMIN_TOKEN` to a non-empty local secret if the AI Engine view should display request-rate
and latency metrics. The dashboard reads that token only to call the API's protected `/metrics`
endpoint and never renders or persists it; when admin metrics are disabled it shows `N/A`.

## LM Studio Desktop startup

The `lm_studio` service does one thing: it opens the visible LM Studio Desktop application. The
same launchers can be run directly:

```powershell
.\scripts\control-center\start_lm_studio.ps1
```

```sh
sh scripts/control-center/start_lm_studio.sh
```

The launcher is idempotent when the desktop process is already running. On Windows it checks the
standard per-user and Program Files locations. For a custom installation, set this optional value
in `.env` or the parent process environment:

```dotenv
LM_STUDIO_EXE=C:\Tools\LM Studio\LM Studio.exe
```

The control center does not call `lms`, manage the daemon, load/unload a model, start/stop the API
server, or close LM Studio on **Stop all**. Configure those choices in LM Studio itself.
`RAG_LLM__MODEL` and `RAG_LLM__BASE_URL` remain application settings and must match the model/server
you configure there.

## Start the complete project

From the repository root on Windows:

```powershell
.\scripts\control-center\start_all.ps1
```

On macOS, Linux, WSL, or Git Bash:

```sh
chmod +x scripts/control-center/*.sh
./scripts/control-center/start_all.sh
```

The start operation validates the configuration, checks dependencies, follows dependency order,
waits for each service's readiness contract, and reports a non-zero exit code if any required
service fails. The API's first startup can take several minutes while BGE-M3 is downloaded and
initialized; the default startup timeout allows ten minutes.

The equivalent CLI command is:

```powershell
uv run rag services start --all
```

Open the interactive dashboard:

```powershell
.\scripts\control-center\dashboard.ps1
```

or:

```powershell
uv run rag dashboard
```

The dashboard presents live service state, process IDs and uptime, CPU/RAM/GPU information,
per-service logs, diagnostics, and AI-engine information. Unsupported GPU or inference throughput
metrics are shown as unavailable rather than synthesized.

## Service management

Manage the whole dependency graph:

```powershell
uv run rag services status --all
uv run rag services stop --all
uv run rag services restart --all
```

Or use the wrappers:

```powershell
.\scripts\control-center\status.ps1
.\scripts\control-center\restart_all.ps1
.\scripts\control-center\stop_all.ps1
```

Manage one or more named services:

```powershell
uv run rag services start redis qdrant
uv run rag services restart api
uv run rag services stop worker
```

Perform one bounded recovery pass for failed services that were started and still owned by the
control center:

```powershell
uv run rag services recover
```

Recovery respects dependency order and re-runs required preflight checks. It is intentionally not
an unbounded background restart loop, so a persistent failure remains visible for diagnosis.

Starting a service also starts missing dependencies. Stopping all services uses reverse dependency
order so consumers exit before Redis and Qdrant. LM Studio Desktop is deliberately left open.
Process services receive a graceful
termination request before the configured timeout; forceful termination is the last resort.
Lifecycle mutations are protected by a cross-process lock, so concurrent CLI and dashboard actions
cannot race over the same PID or container state.

For automation, request JSON output:

```powershell
uv run rag services status --all --json
uv run rag services check --json
```

## Logs

Managed process output and sanitized LM Studio lifecycle events are stored under
`.data/control-center/logs`:

```text
.data/control-center/logs/
|-- api.log
|-- worker.log
`-- lm-studio.log
```

Redis and Qdrant remain separate live streams, but they are read directly with
`docker compose logs`; Docker owns their persistence and rotation. The control center does not
silently duplicate unbounded container output into `redis.log` or `qdrant.log` files.

The dashboard log screen follows the selected service. To inspect a bounded tail without opening
the dashboard:

```powershell
uv run rag services logs api --lines 200
uv run rag services logs worker --lines 100
```

Runtime logs and PID metadata are ignored local state. Do not put API tokens, model keys, prompts,
retrieved document text, or private corpora in service commands or manifests.

## System checks

Run the preflight diagnostics independently:

```powershell
uv run rag services check
```

Checks cover the Python runtime, `uv`, service commands, Docker/Compose, configured environment
requirements, LM Studio, GPU visibility, and CUDA tooling when applicable.
The checked `uv` environment includes every extra listed in `required_uv_extras` in
`ops/control-center/settings.yaml`; the committed topology requires both `qdrant` and
`local-models`, matching the API/worker runtime rather than only the base package.
Optional GPU/CUDA checks do not block CPU mode unless `require_gpu` or `require_cuda` is enabled in
the main settings file. Failures include a suggested corrective action.

Application-level dependency checks remain available through:

```powershell
uv run rag doctor
```

`rag doctor` probes the configured embedding package, LM Studio model, Redis, Qdrant, ingestion
root, and SQLite path. Use both checks when diagnosing an end-to-end indexing or query failure.

## Configuration

The root control-center configuration is
`ops/control-center/settings.yaml`. Service manifests are loaded in deterministic filename order
from `ops/control-center/services`.

To generate a fresh extensible control-center configuration at a chosen in-repository path, run:

```powershell
uv run rag services init --config ops/control-center/settings.yaml
```

Initialization is non-destructive: if the settings file already exists, nothing is changed. Use
`--force` only when you intentionally want to replace generated settings and service manifests;
review or back up local customizations first. Application secrets and `.env` are never generated or
overwritten by this command.

```text
ops/control-center/
|-- settings.yaml
`-- services/
    |-- api.yaml
    |-- lm_studio.yaml
    |-- qdrant.yaml
    |-- redis.yaml
    `-- worker.yaml
```

The main settings define runtime/log directories and global lifecycle timeouts. A service manifest
defines:

- `name`, `display_name`, `description`, and `enabled`;
- `driver`: `process`, `docker_compose`, `lm_studio`, or `external`;
- `dependencies`, which must form an acyclic graph;
- a `health` contract (`process`, `http`, `tcp`, or `command`);
- a log file and driver-specific fields.

Commands are YAML lists and are executed without a command shell. This prevents quoting ambiguity
and shell interpolation. Environment placeholders use `${NAME}` for required values and
`${NAME:-default}` for safe defaults. Values may come from the process environment or the project
`.env`; the process environment wins. Never place a secret default in a committed manifest.

### Add a process service

Create `ops/control-center/services/frontend.yaml`:

```yaml
name: frontend
display_name: Frontend
description: Local browser development server
driver: process
enabled: true
dependencies: [api]
log_file: .data/control-center/logs/frontend.log

command: [npm, run, dev]
cwd: frontend
environment:
  NODE_ENV: development

health:
  type: http
  url: http://127.0.0.1:3000
  expected_statuses: [200]
  timeout_seconds: 3
  interval_seconds: 2
  retries: 60
```

Then rerun `rag services check` and start the service. Process identity includes more than a reused
PID, so the control center will not intentionally terminate an unrelated process after stale state
or a machine reboot.

### Add a Compose service

Use `driver: docker_compose`, then set `compose_file`, `compose_service`, and any required
`profiles`. The driver uses `docker compose up -d`, `stop`, `restart`, `ps`, and `logs` for only the
configured service. It does not delete volumes during ordinary shutdown.

### Use a different configuration

All direct commands accept `--config`:

```powershell
uv run rag dashboard --config ops/control-center/settings.yaml
uv run rag services status --all --config ops/control-center/settings.yaml
```

PowerShell wrappers accept a `-Config` parameter. POSIX wrappers read
`RAG_CONTROL_CENTER_CONFIG`:

```powershell
.\scripts\control-center\start_all.ps1 -Config ops/control-center/settings.yaml
```

```sh
RAG_CONTROL_CENTER_CONFIG=ops/control-center/settings.yaml ./scripts/control-center/start_all.sh
```

## Troubleshooting

### LM Studio Desktop is not found

Install LM Studio in its standard location, or set `LM_STUDIO_EXE` to the full desktop executable
path. Then run `start_lm_studio.ps1` directly to verify the launcher.

### The desktop opens but queries fail

Load the intended model and start the local API server in LM Studio. Keep the served model
identifier aligned with `RAG_LLM__MODEL` and the server address aligned with
`RAG_LLM__BASE_URL`. The launcher intentionally does not change either setting.

### CUDA or GPU is unavailable

Choose GPU offload or CPU mode in LM Studio. To require NVIDIA CUDA in the dashboard preflight,
install a compatible NVIDIA driver/toolkit, verify `nvidia-smi`, and only then enable strict GPU or
CUDA checks. Do not enable a strict requirement on CPU-only hosts.

### Docker is unavailable

Start Docker Desktop, wait for the engine to become ready, and verify:

```powershell
docker version
docker compose version
```

If Redis or Qdrant uses an overridden host port, export `REDIS_PORT` or `QDRANT_PORT` for the
control-center process and update the corresponding URL in `.env`.

### API remains in `starting`

Inspect `.data/control-center/logs/api.log`. On first use, allow time and disk space for BGE-M3.
Also run `uv run rag doctor`. A Qdrant collection created with an incompatible embedding dimension
must be deliberately migrated or reindexed under a new collection name.

### A service has stale PID state

Run `rag services status --all`. The process driver validates the recorded process identity before
reporting it as managed. If no managed process exists, a subsequent start replaces stale runtime
metadata. Do not manually edit state while lifecycle operations are active.

### Ports are already in use

Stop the existing owner or deliberately change the host/port in both the service manifest and
`.env`. The important defaults are API `8000`, LM Studio `1234`, Redis `6379`, and Qdrant `6333`.

## Local-runtime boundary

This control center is designed for the canonical single-host development runtime. It does not turn
the current SQLite/file-backed topology into a distributed production deployment. Redis and Qdrant
have loopback bindings but no local TLS or credentials; LM Studio is a developer inference server;
and application authentication, tenant isolation, backups, and observability remain separate
deployment concerns.
