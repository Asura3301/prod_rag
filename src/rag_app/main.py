"""Command-line entry point for the RAG template."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_TERMINAL_ESCAPE = re.compile(
    r"\x1b(?:\][^\x07]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|.)"
)
_TERMINAL_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1a\x1c-\x1f\x7f-\x9f]")


def _safe_terminal_text(value: object) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = _TERMINAL_ESCAPE.sub("", text)
    return _TERMINAL_CONTROL.sub("", text)


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "rag_app.runtime.composition:create_runtime_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def _ingest(args: argparse.Namespace) -> int:
    from rag_app.ingestion import IngestionPipeline

    result = IngestionPipeline().ingest(Path(args.source))
    print(
        json.dumps(
            {
                "ingested": result.ingested_count,
                "unchanged": result.unchanged_count,
                "failed": result.failed_count,
                "chunks": result.chunk_count,
                "items": [
                    {
                        "source": item.source_id,
                        "status": item.status.value,
                        "document_id": item.document_id,
                        "version": item.version,
                        "chunks": item.chunk_count,
                        "error": item.error,
                    }
                    for item in result.items
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return int(result.failed_count > 0)


def _query(args: argparse.Namespace) -> int:
    import httpx

    from rag_app.core import get_settings

    configured = get_settings().api_token
    api_token = (
        args.api_token
        or os.getenv("RAG_API_TOKEN")
        or (configured.get_secret_value() if configured else None)
    )
    headers = {"Authorization": f"Bearer {api_token}"} if api_token else None
    response = httpx.post(
        f"{args.api_url.rstrip('/')}/api/v1/query",
        json={"query": args.query, "top_k": args.top_k, "filters": {}},
        headers=headers,
        timeout=args.timeout,
    )
    response.raise_for_status()
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    from rag_app.evaluation import latency_summary, mean_reciprocal_rank, ndcg_at_k, recall_at_k

    path = Path(args.dataset)
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    report = {
        "examples": len(rows),
        "metrics_available": [
            recall_at_k.__name__,
            mean_reciprocal_rank.__name__,
            ndcg_at_k.__name__,
            latency_summary.__name__,
        ],
        "note": "Run application-specific targets against this dataset before scoring.",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _worker(args: argparse.Namespace) -> int:
    del args
    from rag_app.runtime import worker_main

    worker_main()
    return 0


def _enqueue(args: argparse.Namespace) -> int:
    import httpx

    from rag_app.core import get_settings

    configured = get_settings().api_token
    api_token = (
        args.api_token
        or os.getenv("RAG_API_TOKEN")
        or (configured.get_secret_value() if configured else None)
    )
    headers = {"Authorization": f"Bearer {api_token}"} if api_token else None
    source = str(Path(args.source).expanduser().resolve())
    response = httpx.post(
        f"{args.api_url.rstrip('/')}/api/v1/ingestion/jobs",
        json={"source": source, "metadata": {}, "idempotency_key": args.idempotency_key},
        headers=headers,
        timeout=args.timeout,
    )
    response.raise_for_status()
    job = response.json()
    if args.wait:
        terminal = {"succeeded", "dead_letter", "cancelled"}
        while job["state"] not in terminal:
            time.sleep(0.5)
            response = httpx.get(
                f"{args.api_url.rstrip('/')}/api/v1/ingestion/jobs/{job['id']}",
                headers=headers,
                timeout=args.timeout,
            )
            response.raise_for_status()
            job = response.json()
    print(json.dumps(job, ensure_ascii=False, indent=2))
    return int(job["state"] in {"dead_letter", "cancelled"})


def _doctor(args: argparse.Namespace) -> int:
    del args
    from rag_app.core import get_settings
    from rag_app.runtime import run_doctor

    settings = get_settings()
    checks = asyncio.run(
        run_doctor(
            llm_base_url=settings.llm.base_url or "",
            llm_model=settings.llm.model,
            redis_url=settings.redis_url,
            qdrant_url=settings.vector_store.url or "",
            ingestion_root=settings.ingestion_root,
            sqlite_path=settings.sqlite_path,
        )
    )
    for check in checks:
        print(f"[{'OK' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
    return int(not all(check.ok for check in checks))


def _dashboard(args: argparse.Namespace) -> int:
    """Run the interactive project control center."""

    from rag_app.control_center.ui import run_dashboard

    run_dashboard(args.config)
    return 0


def _as_jsonable(value: Any) -> Any:
    """Convert Pydantic/dataclass-like control-center results to JSON data."""

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _as_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_jsonable(item) for item in value]
    return value


def _status_value(snapshot: Any) -> str:
    status = getattr(snapshot, "status", "unknown")
    return str(getattr(status, "value", status))


async def _selected_service_statuses(center: Any, names: list[str]) -> list[Any]:
    return list(await asyncio.gather(*(center.status(name) for name in names)))


def _print_control_result(result: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(_as_jsonable(result), ensure_ascii=False, indent=2))
        return

    icons = {
        "running": "[OK]",
        "connected": "[OK]",
        "starting": "[..]",
        "stopping": "[..]",
        "stopped": "[--]",
        "error": "[FAIL]",
        "unhealthy": "[FAIL]",
        "unknown": "[??]",
    }
    services = getattr(result, "services", None)
    if services is None and isinstance(result, (list, tuple)) and all(
        hasattr(item, "name") and hasattr(item, "status") for item in result
    ):
        services = result
    if services is not None:
        for snapshot in services:
            status = _status_value(snapshot)
            display_name = getattr(snapshot, "display_name", None) or getattr(
                snapshot, "name", "service"
            )
            detail = getattr(snapshot, "detail", None)
            pid = getattr(snapshot, "pid", None)
            suffix = f" (PID {pid})" if pid else ""
            if detail:
                suffix += f" - {detail}"
            print(
                f"{icons.get(status, '[??]')} "
                f"{_safe_terminal_text(display_name)}: {status}{_safe_terminal_text(suffix)}"
            )
    else:
        payload = _as_jsonable(result)
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    ok = bool(item.get("ok", item.get("success", False)))
                    name = item.get("name", item.get("check", "check"))
                    detail = item.get("detail", item.get("message", ""))
                    optional = item.get("severity") == "optional"
                    label = "OK" if ok else ("WARN" if optional else "FAIL")
                    print(
                        f"[{label}] {_safe_terminal_text(name)}: {_safe_terminal_text(detail)}"
                    )
                else:
                    print(_safe_terminal_text(item))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))

    for error in getattr(result, "errors", []):
        print(f"[FAIL] {_safe_terminal_text(error)}")
    action = getattr(result, "action", None)
    if action and bool(getattr(result, "success", False)):
        print(f"[OK] {str(action).replace('_', ' ').title()} completed successfully.")


def _services(args: argparse.Namespace) -> int:
    """Run one headless control-center operation."""

    from rag_app.control_center import ControlCenter, initialize_control_center

    action = args.service_action

    if action == "init":
        paths = initialize_control_center(args.config, overwrite=args.force)
        if args.json:
            print(json.dumps({"created": [str(path) for path in paths]}, indent=2))
        elif paths:
            for path in paths:
                print(f"[OK] Created {path}")
        else:
            print("[OK] Control-center configuration already exists; nothing changed.")
        return 0

    center = ControlCenter.from_path(args.config)

    if action == "check":
        checks = asyncio.run(center.system_check())
        _print_control_result(checks, as_json=args.json)
        return int(
            any(
                not bool(getattr(item, "ok", False))
                and getattr(item, "severity", "required") == "required"
                for item in checks
            )
        )

    if action == "logs":
        lines = asyncio.run(center.tail_logs(args.service, lines=args.lines))
        if args.json:
            print(json.dumps(_as_jsonable(lines), ensure_ascii=False, indent=2))
        elif isinstance(lines, str):
            print("\n".join(_safe_terminal_text(line) for line in lines.splitlines()))
        else:
            print("\n".join(_safe_terminal_text(line) for line in lines))
        return 0

    if action == "recover":
        recovery = asyncio.run(center.restart_failed())
        _print_control_result(recovery, as_json=args.json)
        return int(not recovery.success)

    names = list(args.service)
    use_all = bool(args.all or (action == "status" and not names))
    if not use_all and not names:
        raise SystemExit("specify one or more service names, or pass --all")

    if action == "status":
        if use_all:
            snapshots = asyncio.run(center.status_all())
        else:
            snapshots = asyncio.run(_selected_service_statuses(center, names))
        _print_control_result(snapshots, as_json=args.json)
        return int(any(_status_value(snapshot) == "error" for snapshot in snapshots))

    progress_verb = {"start": "Starting", "stop": "Stopping", "restart": "Restarting"}[action]
    if use_all:
        method = getattr(center, f"{action}_all")
        if not args.json:
            print(f"[..] {progress_verb} configured services in dependency order...")
        operation = asyncio.run(method())
    else:
        method = getattr(center, action)
        if not args.json:
            print(f"[..] {progress_verb}: {', '.join(names)}...")
        operation = asyncio.run(method(names))

    _print_control_result(operation, as_json=args.json)
    return int(not operation.success)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rag", description="Production RAG template CLI")
    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="Run the FastAPI service")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(handler=_serve)

    ingest = subcommands.add_parser("ingest", help="Validate, load, and chunk a file or directory")
    ingest.add_argument("source")
    ingest.set_defaults(handler=_ingest)

    query = subcommands.add_parser("query", help="Query a running API")
    query.add_argument("query")
    query.add_argument("--api-url", default="http://127.0.0.1:8000")
    query.add_argument("--api-token", help="Bearer token; defaults to RAG_API_TOKEN")
    query.add_argument("--top-k", type=int, default=5)
    query.add_argument("--timeout", type=float, default=60.0)
    query.set_defaults(handler=_query)

    worker = subcommands.add_parser("worker", help="Run the local Redis ingestion worker")
    worker.set_defaults(handler=_worker)

    enqueue = subcommands.add_parser(
        "enqueue", help="Submit a local file or directory to the worker"
    )
    enqueue.add_argument("source")
    enqueue.add_argument("--api-url", default="http://127.0.0.1:8000")
    enqueue.add_argument("--api-token", help="Bearer token; defaults to RAG_API_TOKEN")
    enqueue.add_argument("--idempotency-key")
    enqueue.add_argument("--timeout", type=float, default=60.0)
    enqueue.add_argument("--wait", action="store_true")
    enqueue.set_defaults(handler=_enqueue)

    doctor = subcommands.add_parser("doctor", help="Check local runtime dependencies")
    doctor.set_defaults(handler=_doctor)

    dashboard = subcommands.add_parser(
        "dashboard", help="Open the interactive project control center"
    )
    dashboard.add_argument(
        "--config",
        default="ops/control-center/settings.yaml",
        help="Control-center settings YAML",
    )
    dashboard.set_defaults(handler=_dashboard)

    services = subcommands.add_parser(
        "services", help="Start, stop, inspect, and diagnose project services"
    )
    service_actions = services.add_subparsers(dest="service_action", required=True)
    for action in ("start", "stop", "restart", "status"):
        command = service_actions.add_parser(action, help=f"{action.title()} project services")
        command.add_argument("service", nargs="*", help="Service name(s) from the manifest")
        command.add_argument("--all", action="store_true", help=f"{action.title()} all services")
        command.add_argument(
            "--config",
            default="ops/control-center/settings.yaml",
            help="Control-center settings YAML",
        )
        command.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
        command.set_defaults(handler=_services)

    service_check = service_actions.add_parser("check", help="Run system preflight checks")
    service_check.add_argument("--all", action="store_true", help=argparse.SUPPRESS)
    service_check.add_argument(
        "--config", default="ops/control-center/settings.yaml", help="Control-center settings YAML"
    )
    service_check.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    service_check.set_defaults(handler=_services)

    service_logs = service_actions.add_parser("logs", help="Print the tail of a service log")
    service_logs.add_argument("service", help="Service name from the manifest")
    service_logs.add_argument("--lines", type=int, default=200, help="Number of lines to print")
    service_logs.add_argument(
        "--config", default="ops/control-center/settings.yaml", help="Control-center settings YAML"
    )
    service_logs.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    service_logs.set_defaults(handler=_services)

    service_recover = service_actions.add_parser(
        "recover", help="Restart services that failed while managed by the control center"
    )
    service_recover.add_argument(
        "--config", default="ops/control-center/settings.yaml", help="Control-center settings YAML"
    )
    service_recover.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    service_recover.set_defaults(handler=_services)

    service_init = service_actions.add_parser(
        "init", help="Create an extensible control-center configuration"
    )
    service_init.add_argument(
        "--config", default="ops/control-center/settings.yaml", help="Settings YAML to create"
    )
    service_init.add_argument(
        "--force", action="store_true", help="Replace existing generated configuration files"
    )
    service_init.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    service_init.set_defaults(handler=_services)

    evaluate = subcommands.add_parser("evaluate", help="Inspect an evaluation dataset")
    evaluate.add_argument("--dataset", default="evals/golden_dataset.jsonl")
    evaluate.set_defaults(handler=_evaluate)
    return parser


def cli(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        print(
            "\n[WARN] Interrupted. Run `rag services status --all` before retrying.",
            file=sys.stderr,
        )
        return 130
    except Exception as exc:
        from rag_app.control_center import ControlCenterConfigError, ControlCenterError

        if not isinstance(exc, (ControlCenterConfigError, ControlCenterError)):
            raise
        print(f"ERROR: {_safe_terminal_text(exc)}", file=sys.stderr)
        print(
            "Solution: review the control-center YAML or run `rag services init`.",
            file=sys.stderr,
        )
        return 2


def main() -> None:
    raise SystemExit(cli())


if __name__ == "__main__":
    main()
