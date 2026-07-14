"""Minimal standard-library client for the RAG HTTP API."""

from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

BASE_URL = os.getenv("RAG_API_URL", "http://localhost:8000")


def post(path: str, payload: dict[str, object]) -> dict[str, object]:
    request = Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


if __name__ == "__main__":
    job = post(
        "/api/v1/ingestion/jobs",
        {"source": "file:///data/handbook.pdf", "idempotency_key": "handbook-v1"},
    )
    print("job:", json.dumps(job, indent=2))
    answer = post("/api/v1/query", {"query": "Summarize the handbook", "top_k": 5})
    print("answer:", json.dumps(answer, indent=2))
