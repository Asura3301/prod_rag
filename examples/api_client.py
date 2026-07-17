"""Minimal standard-library client for the RAG HTTP API."""

from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

BASE_URL = os.getenv("RAG_API_URL", "http://localhost:8000")
API_TOKEN = os.getenv("RAG_API_TOKEN")


def post(path: str, payload: dict[str, object]) -> dict[str, object]:
    headers = {"Content-Type": "application/json"}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"
    request = Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


if __name__ == "__main__":
    document = post(
        "/api/v1/documents",
        {
            "source": "memory://handbook",
            "text": "Refunds are available within thirty days.",
            "metadata": {"category": "policy"},
        },
    )
    print("document:", json.dumps(document, indent=2))
    answer = post("/api/v1/query", {"query": "What is the refund policy?", "top_k": 5})
    print("answer:", json.dumps(answer, indent=2))
