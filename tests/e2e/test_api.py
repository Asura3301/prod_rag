import asyncio

from fastapi.testclient import TestClient

from rag_app.api import InMemoryRAGService, create_app
from rag_app.workers import InMemoryJobQueue, JobState


def test_health_and_ingestion_idempotency() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/health/live").json() == {"status": "alive"}
        assert client.get("/health/ready").json() == {"status": "ready"}

        payload = {"source": "memory://guide", "idempotency_key": "guide-v1"}
        first = client.post("/api/v1/ingestion/jobs", json=payload)
        second = client.post("/api/v1/ingestion/jobs", json=payload)
        assert first.status_code == 202
        assert second.json()["id"] == first.json()["id"]


def test_query_documents_stream_and_admin_protection() -> None:
    service = InMemoryRAGService()
    document = asyncio.run(
        service.add_document(
            "memory://policy",
            "Refunds are available within thirty days.",
            {"tenant": "demo"},
        )
    )
    with TestClient(create_app(service=service, admin_token="test-secret")) as client:
        response = client.post(
            "/api/v1/query",
            json={"query": "Refunds thirty days", "filters": {"tenant": "demo"}},
        )
        assert response.status_code == 200
        assert response.json()["citations"][0]["document_id"] == document.id

        stream = client.post("/api/v1/query/stream", json={"query": "Refunds"})
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert "event: done" in stream.text

        assert client.get("/metrics").status_code == 401
        metrics = client.get("/metrics", headers={"Authorization": "Bearer test-secret"})
        assert metrics.status_code == 200
        assert "rag_jobs_total" in metrics.text


def test_document_creation_api_auth_rate_limit_and_request_id() -> None:
    app = create_app(api_token="api-secret", rate_limit_per_minute=2)
    with TestClient(app) as client:
        assert client.get("/api/v1/documents").status_code == 401
        headers = {"Authorization": "Bearer api-secret", "X-Request-ID": "test-request"}
        created = client.post(
            "/api/v1/documents",
            headers=headers,
            json={"source": "memory://guide", "text": "Hybrid retrieval uses RRF."},
        )
        assert created.status_code == 201
        assert created.headers["X-Request-ID"] == "test-request"
        listed = client.get("/api/v1/documents", headers=headers)
        assert listed.status_code == 200
        assert listed.json()[0]["source"] == "memory://guide"
        assert client.get("/api/v1/documents", headers=headers).status_code == 429


def test_in_memory_queue_retries_dead_letters_and_cancellation() -> None:
    async def scenario() -> None:
        queue = InMemoryJobQueue()
        job = await queue.enqueue("ingest", {"source": "x"}, "x", max_attempts=2)
        assert (await queue.enqueue("ingest", {"source": "x"}, "x")).id == job.id

        first_attempt = await queue.claim(timeout=0.01)
        assert first_attempt and first_attempt.state is JobState.RUNNING
        assert (await queue.fail(job.id, "temporary")).state is JobState.PENDING
        assert await queue.claim(timeout=0.01)
        assert (await queue.fail(job.id, "permanent")).state is JobState.DEAD_LETTER

        cancellable = await queue.enqueue("ingest", {"source": "y"}, "y")
        assert (await queue.cancel(cancellable.id)).state is JobState.CANCELLED

    asyncio.run(scenario())
