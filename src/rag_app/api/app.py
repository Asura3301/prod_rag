"""FastAPI application factory for the RAG service."""

from __future__ import annotations

import json
import os
import secrets
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from rag_app.core import configure_telemetry
from rag_app.workers import InMemoryJobQueue, Job, JobQueue, RedisJobQueue

from .models import (
    DocumentCreateRequest,
    DocumentResponse,
    IngestionRequest,
    JobResponse,
    QueryRequest,
    QueryResponse,
)
from .service import InMemoryRAGService, RAGService


def _job_response(job: Job) -> JobResponse:
    return JobResponse(**asdict(job))


def create_app(
    *,
    service: RAGService | None = None,
    queue: JobQueue | None = None,
    admin_token: str | None = None,
    api_token: str | None = None,
    rate_limit_per_minute: int = 120,
    max_body_bytes: int = 10 * 1024 * 1024,
) -> FastAPI:
    """Create an app with injectable adapters and in-memory defaults."""
    if rate_limit_per_minute < 1:
        raise ValueError("rate_limit_per_minute must be positive")
    if max_body_bytes < 1:
        raise ValueError("max_body_bytes must be positive")
    rag_service = service if service is not None else InMemoryRAGService()
    redis_url = os.getenv("REDIS_URL")
    job_queue = queue or (RedisJobQueue(redis_url) if redis_url else InMemoryJobQueue())
    expected_admin_token = admin_token or os.getenv("RAG_ADMIN_TOKEN")
    expected_api_token = api_token or os.getenv("RAG_API_TOKEN")
    request_times: dict[str, deque[float]] = defaultdict(deque)

    app = FastAPI(
        title="Production RAG API",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.state.rag_service = rag_service
    app.state.job_queue = job_queue
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        configure_telemetry(
            service_name=os.getenv("OTEL_SERVICE_NAME", "prod-rag-api"),
            endpoint=otlp_endpoint,
            fastapi_app=app,
        )

    @app.middleware("http")
    async def security_middleware(request: Request, call_next: Any) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid4().hex
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdecimal() and int(content_length) > max_body_bytes:
            return JSONResponse(
                {"detail": "request body is too large"},
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                headers={"X-Request-ID": request_id},
            )
        if request.url.path.startswith("/api/"):
            authorization = request.headers.get("authorization", "")
            if expected_api_token:
                scheme, _, token = authorization.partition(" ")
                if scheme.lower() != "bearer" or not secrets.compare_digest(
                    token, expected_api_token
                ):
                    return JSONResponse(
                        {"detail": "invalid API token"},
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        headers={
                            "WWW-Authenticate": "Bearer",
                            "X-Request-ID": request_id,
                        },
                    )
            client = request.client.host if request.client else "unknown"
            now = time.monotonic()
            window = request_times[client]
            while window and now - window[0] >= 60:
                window.popleft()
            if len(window) >= rate_limit_per_minute:
                return JSONResponse(
                    {"detail": "rate limit exceeded"},
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    headers={"Retry-After": "60", "X-Request-ID": request_id},
                )
            window.append(now)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    async def require_admin(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        if not expected_admin_token:
            raise HTTPException(status_code=503, detail="administrative endpoints are disabled")
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(token, expected_admin_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid administrative token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/health/live", tags=["health"])
    async def liveness() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready", tags=["health"])
    async def readiness(response: Response) -> dict[str, str]:
        if not await rag_service.ready():
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "not_ready"}
        return {"status": "ready"}

    @app.post(
        "/api/v1/ingestion/jobs",
        response_model=JobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["ingestion"],
    )
    async def create_ingestion_job(request: IngestionRequest) -> JobResponse:
        key = request.idempotency_key or f"ingest:{request.source}"
        job = await job_queue.enqueue(
            "ingest",
            {"source": request.source, "metadata": request.metadata},
            idempotency_key=key,
        )
        return _job_response(job)

    @app.get("/api/v1/ingestion/jobs", response_model=list[JobResponse], tags=["ingestion"])
    async def list_ingestion_jobs() -> list[JobResponse]:
        return [_job_response(job) for job in await job_queue.list()]

    @app.get("/api/v1/ingestion/jobs/{job_id}", response_model=JobResponse, tags=["ingestion"])
    async def get_ingestion_job(job_id: str) -> JobResponse:
        if not (job := await job_queue.get(job_id)):
            raise HTTPException(status_code=404, detail="job not found")
        return _job_response(job)

    @app.delete("/api/v1/ingestion/jobs/{job_id}", response_model=JobResponse, tags=["ingestion"])
    async def cancel_ingestion_job(job_id: str) -> JobResponse:
        try:
            return _job_response(await job_queue.cancel(job_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc

    @app.get("/api/v1/documents", response_model=list[DocumentResponse], tags=["documents"])
    async def list_documents() -> list[DocumentResponse]:
        return await rag_service.list_documents()

    @app.post(
        "/api/v1/documents",
        response_model=DocumentResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["documents"],
    )
    async def create_document(request: DocumentCreateRequest) -> DocumentResponse:
        return await rag_service.add_document(request.source, request.text, request.metadata)

    @app.get("/api/v1/documents/{document_id}", response_model=DocumentResponse, tags=["documents"])
    async def get_document(document_id: str) -> DocumentResponse:
        if not (document := await rag_service.get_document(document_id)):
            raise HTTPException(status_code=404, detail="document not found")
        return document

    @app.delete("/api/v1/documents/{document_id}", status_code=204, tags=["documents"])
    async def delete_document(document_id: str) -> Response:
        if not await rag_service.delete_document(document_id):
            raise HTTPException(status_code=404, detail="document not found")
        return Response(status_code=204)

    @app.post("/api/v1/query", response_model=QueryResponse, tags=["query"])
    async def query(request: QueryRequest) -> QueryResponse:
        return await rag_service.query(request.query, request.top_k, request.filters)

    @app.post("/api/v1/query/stream", tags=["query"])
    async def stream_query(request: QueryRequest) -> StreamingResponse:
        async def events() -> AsyncIterator[str]:
            result = await rag_service.query(request.query, request.top_k, request.filters)
            for token in result.answer.split():
                yield _sse("token", {"text": f"{token} "})
            yield _sse(
                "citations",
                {"citations": [citation.model_dump() for citation in result.citations]},
            )
            yield _sse("done", {"latency_ms": result.latency_ms})

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/debug/state", dependencies=[Depends(require_admin)], tags=["operations"])
    async def debug_state() -> dict[str, Any]:
        jobs = await job_queue.list()
        return {
            "service": type(rag_service).__name__,
            "queue": type(job_queue).__name__,
            "jobs_by_state": _count_states(jobs),
        }

    @app.get("/metrics", dependencies=[Depends(require_admin)], tags=["operations"])
    async def metrics() -> Response:
        jobs = await job_queue.list()
        states = _count_states(jobs)
        body = [
            "# HELP rag_jobs_total Number of ingestion jobs by state.",
            "# TYPE rag_jobs_total gauge",
            *(f'rag_jobs_total{{state="{state}"}} {count}' for state, count in states.items()),
        ]
        return Response("\n".join(body) + "\n", media_type="text/plain; version=0.0.4")

    return app


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _count_states(jobs: list[Job]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job.state.value] = counts.get(job.state.value, 0) + 1
    return counts
