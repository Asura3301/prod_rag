from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.documents import Document

from rag_app.ingestion import (
    DocumentValidationError,
    IngestionPipeline,
    IngestionStatus,
    InMemoryDocumentRegistry,
    LocalDirectorySource,
    StructureAwareChunker,
    TextChunker,
    route_loader,
)
from rag_app.ingestion.document_loaders import validate_file_path, validate_web_url


def test_route_loader_normalizes_text_metadata(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("Production RAG ingestion.", encoding="utf-8")

    documents = route_loader(path)

    assert [document.page_content for document in documents] == [
        "Production RAG ingestion."
    ]
    assert documents[0].metadata == {
        "source": path.resolve().as_posix(),
        "source_type": "file",
        "filename": "notes.txt",
        "extension": ".txt",
        "suffix": ".txt",
        "loader": "text",
        "metadata_version": 1,
    }


def test_file_validation_rejects_unsupported_and_outside_root(tmp_path: Path) -> None:
    unsupported = tmp_path / "payload.exe"
    unsupported.write_bytes(b"not executable")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()

    with pytest.raises(DocumentValidationError, match="Unsupported"):
        validate_file_path(unsupported)
    with pytest.raises(DocumentValidationError, match="outside allowed root"):
        validate_file_path(outside, allowed_root=allowed_root)


def test_file_validation_enforces_size_limit(tmp_path: Path) -> None:
    path = tmp_path / "large.txt"
    path.write_text("12345", encoding="utf-8")

    with pytest.raises(DocumentValidationError, match="exceeds"):
        validate_file_path(path, max_file_size=4)


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://example.com/file.txt", "https://u:p@example.com"],
)
def test_web_validation_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(DocumentValidationError):
        validate_web_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://127.0.0.1/admin",
        "http://10.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/admin",
    ],
)
def test_web_validation_rejects_ssrf_targets(url: str) -> None:
    with pytest.raises(DocumentValidationError):
        validate_web_url(url)


def test_structure_aware_chunker_preserves_headings() -> None:
    chunker = StructureAwareChunker(fallback=TextChunker(chunk_size=80, overlap=0))
    document = Document(
        page_content="# Alpha\n\nAlpha text.\n\n## Beta\n\nBeta text.",
        metadata={},
    )

    chunks = chunker.split(document)

    assert chunks == ["# Alpha\n\nAlpha text.", "## Beta\n\nBeta text."]


def test_pipeline_is_idempotent_and_ids_are_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "document.txt"
    path.write_text("# Intro\n\nSame content.", encoding="utf-8")
    registry = InMemoryDocumentRegistry()
    pipeline = IngestionPipeline(registry=registry)

    first = pipeline.ingest(path)
    first_record = registry.get(path.resolve().as_posix())
    second = pipeline.ingest(path)
    second_record = registry.get(path.resolve().as_posix())

    assert first.ingested_count == 1
    assert second.unchanged_count == 1
    assert first_record is not None
    assert second_record == first_record
    assert first_record.version == 1
    assert first_record.content_id == second_record.content_id
    assert (
        first_record.chunks[0].metadata["content_id"] == first_record.content_id
    )
    assert first_record.chunks[0].metadata["chunk_id"] == (
        second_record.chunks[0].metadata["chunk_id"]
    )


def test_pipeline_versions_changed_content(tmp_path: Path) -> None:
    path = tmp_path / "versioned.txt"
    path.write_text("version one", encoding="utf-8")
    registry = InMemoryDocumentRegistry()
    pipeline = IngestionPipeline(registry=registry)
    first = pipeline.ingest(path)
    first_record = registry.get(path.resolve().as_posix())

    path.write_text("version two", encoding="utf-8")
    second = pipeline.ingest(path)
    second_record = registry.get(path.resolve().as_posix())

    assert first.items[0].status is IngestionStatus.INGESTED
    assert second.items[0].status is IngestionStatus.INGESTED
    assert first_record is not None and second_record is not None
    assert first_record.document_id == second_record.document_id
    assert first_record.content_id != second_record.content_id
    assert first_record.content_hash != second_record.content_hash
    assert second_record.version == 2
    assert (
        first_record.chunks[0].metadata["chunk_id"]
        != second_record.chunks[0].metadata["chunk_id"]
    )


def test_directory_source_is_sorted_and_filters_extensions(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("B", encoding="utf-8")
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    (tmp_path / "ignored.bin").write_bytes(b"ignored")

    items = list(LocalDirectorySource(tmp_path).items())

    assert [item.path.name for item in items] == ["a.txt", "b.txt"]


def test_pipeline_returns_typed_failure_without_raising(tmp_path: Path) -> None:
    path = tmp_path / "broken.txt"
    path.write_text("content", encoding="utf-8")

    def broken_loader(_: str | Path) -> list[Document]:
        raise RuntimeError("parser unavailable")

    result = IngestionPipeline(loader=broken_loader).ingest(path)

    assert result.failed_count == 1
    assert result.items[0].status is IngestionStatus.FAILED
    assert result.items[0].error == "RuntimeError: parser unavailable"
