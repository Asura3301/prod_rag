"""Command-line entry point for the RAG template."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "rag_app.api.app:create_app",
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

    response = httpx.post(
        f"{args.api_url.rstrip('/')}/api/v1/query",
        json={"query": args.query, "top_k": args.top_k, "filters": {}},
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
    query.add_argument("--api-url", default="http://localhost:8000")
    query.add_argument("--top-k", type=int, default=5)
    query.add_argument("--timeout", type=float, default=60.0)
    query.set_defaults(handler=_query)

    evaluate = subcommands.add_parser("evaluate", help="Inspect an evaluation dataset")
    evaluate.add_argument("--dataset", default="evals/golden_dataset.jsonl")
    evaluate.set_defaults(handler=_evaluate)
    return parser


def cli(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


def main() -> None:
    raise SystemExit(cli())


if __name__ == "__main__":
    main()
