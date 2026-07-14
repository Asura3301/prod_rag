"""Source abstractions for deterministic ingestion discovery."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .document_loaders import (
    ALLOWED_EXTENSIONS,
    DEFAULT_MAX_FILE_SIZE,
    validate_file_path,
)


@dataclass(frozen=True, slots=True)
class SourceItem:
    """A validated item emitted by an ingestion source."""

    path: Path
    source_id: str


@runtime_checkable
class DocumentSource(Protocol):
    """Protocol implemented by discoverable document sources."""

    def items(self) -> Iterator[SourceItem]:
        """Yield validated source items in deterministic order."""
        ...


@dataclass(frozen=True, slots=True)
class LocalFileSource:
    path: str | Path
    allowed_root: str | Path | None = None
    max_file_size: int = DEFAULT_MAX_FILE_SIZE

    def items(self) -> Iterator[SourceItem]:
        path = validate_file_path(
            self.path,
            allowed_root=self.allowed_root,
            max_file_size=self.max_file_size,
        )
        yield SourceItem(path=path, source_id=path.as_posix())


@dataclass(frozen=True, slots=True)
class LocalDirectorySource:
    path: str | Path
    recursive: bool = True
    glob: str = "**/*"
    max_file_size: int = DEFAULT_MAX_FILE_SIZE

    def items(self) -> Iterator[SourceItem]:
        root = Path(self.path).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"Not a directory: {root}")
        iterator = root.rglob("*") if self.recursive else root.glob("*")
        candidates = (
            candidate
            for candidate in iterator
            if candidate.is_file()
            and candidate.suffix.lower() in ALLOWED_EXTENSIONS
            and (candidate.match(self.glob) or candidate.name == self.glob)
        )
        for candidate in sorted(candidates, key=lambda item: item.as_posix()):
            path = validate_file_path(
                candidate,
                allowed_root=root,
                max_file_size=self.max_file_size,
            )
            yield SourceItem(path=path, source_id=path.as_posix())


def as_source(value: DocumentSource | str | Path) -> DocumentSource:
    """Coerce a path into the corresponding local source."""
    if isinstance(value, DocumentSource):
        return value
    path = Path(value).expanduser()
    if path.is_dir():
        return LocalDirectorySource(path)
    return LocalFileSource(path)


__all__ = [
    "DocumentSource", "LocalDirectorySource", "LocalFileSource", "SourceItem",
    "as_source",
]
