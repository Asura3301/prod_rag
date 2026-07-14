"""Deterministic identifiers and content hashes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def content_hash(content: str | bytes, *, algorithm: str = "sha256") -> str:
    """Return a stable hexadecimal digest for text or bytes."""

    data = content.encode("utf-8") if isinstance(content, str) else content
    try:
        digest = hashlib.new(algorithm)
    except ValueError as exc:
        raise ValueError(f"unsupported hash algorithm: {algorithm}") from exc
    digest.update(data)
    return digest.hexdigest()


def deterministic_id(*parts: object, namespace: str = "rag") -> str:
    """Hash length-framed parts so the same inputs always produce the same ID."""

    digest = hashlib.sha256()
    for part in (namespace, *parts):
        encoded = _stable_bytes(part)
        digest.update(len(encoded).to_bytes(8, byteorder="big"))
        digest.update(encoded)
    return digest.hexdigest()


def document_id(source: str, content: str | bytes) -> str:
    return deterministic_id(source, content_hash(content), namespace="document")


def chunk_id(document_id: str, index: int, content: str | bytes) -> str:
    if index < 0:
        raise ValueError("chunk index must be non-negative")
    return deterministic_id(
        document_id,
        index,
        content_hash(content),
        namespace="chunk",
    )


def _stable_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, Mapping | list | tuple):
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    return str(value).encode("utf-8")
