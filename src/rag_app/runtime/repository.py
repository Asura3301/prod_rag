"""Small durable document registry for the local Windows runtime."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class StoredDocument:
    id: str
    source: str
    text: str
    metadata: dict[str, Any]
    created_at: datetime
    content_hash: str
    version: int
    chunk_ids: tuple[str, ...]


class SQLiteDocumentRepository:
    """SQLite repository shared by the host API and worker processes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL UNIQUE,
                    text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK (version > 0),
                    chunk_ids_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS documents_source_idx ON documents(source)"
            )

    async def upsert(self, document: StoredDocument) -> None:
        await asyncio.to_thread(self._upsert, document)

    def _upsert(self, document: StoredDocument) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO documents (
                    id, source, text, metadata_json, created_at,
                    content_hash, version, chunk_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    id=excluded.id,
                    text=excluded.text,
                    metadata_json=excluded.metadata_json,
                    created_at=excluded.created_at,
                    content_hash=excluded.content_hash,
                    version=excluded.version,
                    chunk_ids_json=excluded.chunk_ids_json
                """,
                (
                    document.id,
                    document.source,
                    document.text,
                    json.dumps(document.metadata, ensure_ascii=False),
                    document.created_at.astimezone(UTC).isoformat(),
                    document.content_hash,
                    document.version,
                    json.dumps(document.chunk_ids),
                ),
            )

    async def get(self, document_id: str) -> StoredDocument | None:
        return await asyncio.to_thread(self._get_one, "id", document_id)

    async def get_by_source(self, source: str) -> StoredDocument | None:
        return await asyncio.to_thread(self._get_one, "source", source)

    def _get_one(self, field: str, value: str) -> StoredDocument | None:
        if field not in {"id", "source"}:
            raise ValueError("Unsupported lookup field")
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT * FROM documents WHERE {field} = ?", (value,)
            ).fetchone()
        return self._from_row(row) if row is not None else None

    async def list_documents(self) -> list[StoredDocument]:
        return await asyncio.to_thread(self._list)

    def _list(self) -> list[StoredDocument]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM documents ORDER BY created_at DESC"
            ).fetchall()
        return [self._from_row(row) for row in rows]

    async def delete(self, document_id: str) -> StoredDocument | None:
        return await asyncio.to_thread(self._delete, document_id)

    def _delete(self, document_id: str) -> StoredDocument | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if row is None:
                return None
            connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        return self._from_row(row)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> StoredDocument:
        return StoredDocument(
            id=str(row["id"]),
            source=str(row["source"]),
            text=str(row["text"]),
            metadata=dict(json.loads(row["metadata_json"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            content_hash=str(row["content_hash"]),
            version=int(row["version"]),
            chunk_ids=tuple(map(str, json.loads(row["chunk_ids_json"]))),
        )


__all__ = ["SQLiteDocumentRepository", "StoredDocument"]
