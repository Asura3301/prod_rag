"""Thread-safe in-memory registry for idempotent ingestion."""

from __future__ import annotations

from threading import RLock

from .models import IngestedDocument


class InMemoryDocumentRegistry:
    """Stores the latest immutable record for each source."""

    def __init__(self) -> None:
        self._records: dict[str, IngestedDocument] = {}
        self._lock = RLock()

    def get(self, source_id: str) -> IngestedDocument | None:
        with self._lock:
            return self._records.get(source_id)

    def is_current(self, source_id: str, content_hash: str) -> bool:
        record = self.get(source_id)
        return record is not None and record.content_hash == content_hash

    def next_version(self, source_id: str) -> int:
        record = self.get(source_id)
        return 1 if record is None else record.version + 1

    def register(self, record: IngestedDocument) -> tuple[IngestedDocument, bool]:
        """Insert/update atomically; return the effective record and change flag."""
        with self._lock:
            current = self._records.get(record.source_id)
            if current is not None and current.content_hash == record.content_hash:
                return current, False
            expected_version = 1 if current is None else current.version + 1
            if record.version != expected_version:
                raise ValueError(
                    f"Expected version {expected_version} for {record.source_id}, "
                    f"received {record.version}"
                )
            self._records[record.source_id] = record
            return record, True

    def all(self) -> tuple[IngestedDocument, ...]:
        with self._lock:
            return tuple(self._records[key] for key in sorted(self._records))

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


__all__ = ["InMemoryDocumentRegistry"]
