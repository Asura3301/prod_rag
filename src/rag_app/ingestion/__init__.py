"""Production document ingestion API."""

from .chunking import ChunkingStrategy, StructureAwareChunker, TextChunker
from .document_loaders import (
    CSVDocumentLoader,
    DirectoryDocumentLoader,
    DoclingDocumentLoader,
    DocumentValidationError,
    FallbackPDFLoader,
    JSONDocumentLoader,
    OptionalLoaderUnavailable,
    UnstructuredDocumentLoader,
    WebDocumentLoader,
    route_loader,
)
from .models import (
    IngestedDocument,
    IngestionItemResult,
    IngestionResult,
    IngestionStatus,
)
from .pipeline import IngestionPipeline
from .registry import InMemoryDocumentRegistry
from .sources import DocumentSource, LocalDirectorySource, LocalFileSource, SourceItem

__all__ = [
    "CSVDocumentLoader",
    "ChunkingStrategy",
    "DirectoryDocumentLoader",
    "DoclingDocumentLoader",
    "DocumentSource",
    "DocumentValidationError",
    "FallbackPDFLoader",
    "InMemoryDocumentRegistry",
    "IngestedDocument",
    "IngestionItemResult",
    "IngestionPipeline",
    "IngestionResult",
    "IngestionStatus",
    "JSONDocumentLoader",
    "LocalDirectorySource",
    "LocalFileSource",
    "OptionalLoaderUnavailable",
    "SourceItem",
    "StructureAwareChunker",
    "TextChunker",
    "UnstructuredDocumentLoader",
    "WebDocumentLoader",
    "route_loader",
]
