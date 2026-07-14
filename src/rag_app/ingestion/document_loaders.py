"""Validated, dependency-tolerant document loading.

Heavy parsers are imported only when selected.  This keeps the ingestion
package importable in workers that only handle plain text.
"""

from __future__ import annotations

import logging
import mimetypes
import os
from collections.abc import Iterable
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

from langchain_community.document_loaders import TextLoader
from langchain_core.documents import Document

logger = logging.getLogger(__name__)

DOCLING_EXTS = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".xhtml", ".md"}
UNSTRUCTURED_EXTS = {
    ".doc",
    ".ppt",
    ".xls",
    ".rtf",
    ".epub",
    ".eml",
    ".msg",
    ".odt",
    ".odp",
    ".ods",
    ".rst",
    ".org",
}
TEXT_EXTS = {
    ".txt",
    ".log",
    ".py",
    ".js",
    ".ts",
    ".sh",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".ipynb",
}
STRUCTURED_EXTS = {".csv", ".json"}
ALLOWED_EXTENSIONS = DOCLING_EXTS | UNSTRUCTURED_EXTS | TEXT_EXTS | STRUCTURED_EXTS
DEFAULT_MAX_FILE_SIZE = 100 * 1024 * 1024
METADATA_VERSION = 1


class DocumentValidationError(ValueError):
    """Raised when an ingestion target violates the configured safety policy."""


class OptionalLoaderUnavailable(RuntimeError):
    """Raised when an optional parser required for a file is unavailable."""


def validate_file_path(
    file_path: str | os.PathLike[str],
    *,
    allowed_root: str | os.PathLike[str] | None = None,
    allowed_extensions: set[str] | frozenset[str] = ALLOWED_EXTENSIONS,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    check_mime: bool = True,
) -> Path:
    """Resolve and validate a regular local file without opening it."""
    raw = os.fspath(file_path)
    if not raw or "\x00" in raw:
        raise DocumentValidationError("File path is empty or contains a NUL byte")
    parsed = urlparse(raw)
    if parsed.scheme and len(parsed.scheme) > 1:
        raise DocumentValidationError(
            f"Local loaders do not accept the {parsed.scheme!r} URL scheme"
        )
    path = Path(raw).expanduser().resolve(strict=True)
    if not path.is_file():
        raise DocumentValidationError(f"Not a regular file: {path}")
    if allowed_root is not None:
        root = Path(allowed_root).expanduser().resolve(strict=True)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise DocumentValidationError(f"File is outside allowed root: {path}") from exc
    extension = path.suffix.lower()
    if extension not in allowed_extensions:
        raise DocumentValidationError(f"Unsupported file extension: {extension or '<none>'}")
    size = path.stat().st_size
    if size > max_file_size:
        raise DocumentValidationError(f"File exceeds {max_file_size} byte limit: {size} bytes")
    if check_mime:
        guessed, _ = mimetypes.guess_type(path.name)
        if guessed is not None and not _mime_matches_extension(guessed, extension):
            raise DocumentValidationError(
                f"MIME type {guessed!r} is inconsistent with extension {extension!r}"
            )
    return path


def validate_web_url(url: str) -> str:
    """Allow public HTTP(S) URLs and reject obvious SSRF targets."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise DocumentValidationError("Only absolute http(s) URLs are supported")
    if parsed.username or parsed.password:
        raise DocumentValidationError("URLs containing credentials are not accepted")
    hostname = (parsed.hostname or "").rstrip(".").casefold()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise DocumentValidationError("Localhost URLs are not accepted")
    try:
        address = ip_address(hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise DocumentValidationError(
                "Private, loopback, and reserved IP URLs are not accepted"
            )
    return url


def _mime_matches_extension(mime: str, extension: str) -> bool:
    if extension in TEXT_EXTS | {".csv", ".json", ".md", ".rst", ".org"}:
        return mime.startswith("text/") or mime in {
            "application/json",
            "application/x-ipynb+json",
            "application/octet-stream",
        }
    expected = {
        ".pdf": {"application/pdf"},
        ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        ".pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation"},
        ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    }
    return extension not in expected or mime in expected[extension]


def _normalize_source(path: Path) -> str:
    return path.as_posix()


def _tag(docs: Iterable[Document], path: Path, loader_name: str) -> list[Document]:
    """Return documents with stable normalized source metadata."""
    resolved = path.expanduser().resolve()
    base = {
        "source": _normalize_source(resolved),
        "source_type": "file",
        "filename": resolved.name,
        "extension": resolved.suffix.lower(),
        "suffix": resolved.suffix.lower(),  # retained for compatibility
        "loader": loader_name,
        "metadata_version": METADATA_VERSION,
    }
    tagged: list[Document] = []
    for document in docs:
        metadata = dict(document.metadata or {})
        metadata.update(base)
        tagged.append(Document(page_content=document.page_content, metadata=metadata))
    return tagged


def _validated(file_path: str | Path) -> Path:
    return validate_file_path(file_path)


class DoclingDocumentLoader:
    def __init__(self, embed_model_id: str | None = None) -> None:
        self.embed_model_id = embed_model_id

    def load(self, file_path: str | Path) -> list[Document]:
        path = _validated(file_path)
        try:
            from docling.chunking import HybridChunker
            from langchain_docling import DoclingLoader
            from langchain_docling.loader import ExportType
        except ImportError as exc:
            raise OptionalLoaderUnavailable(
                "Docling support is unavailable; install langchain-docling and docling-core"
            ) from exc
        model_id = self.embed_model_id or os.getenv("EMBED_MODEL_ID")
        chunker = (
            HybridChunker(tokenizer=model_id)  # type: ignore[arg-type]
            if model_id
            else HybridChunker()
        )
        try:
            docs = DoclingLoader(
                file_path=str(path),
                export_type=ExportType.DOC_CHUNKS,
                chunker=chunker,
            ).load()
        except Exception as exc:
            raise RuntimeError(f"Docling could not parse {path.name}: {exc}") from exc
        tagged = _tag(docs, path, "docling")
        logger.info("Loaded %d document(s) with Docling from %s", len(tagged), path)
        return tagged


class UnstructuredDocumentLoader:
    def load(self, file_path: str | Path) -> list[Document]:
        path = _validated(file_path)
        try:
            from langchain_unstructured import UnstructuredLoader
        except ImportError as exc:
            raise OptionalLoaderUnavailable(
                "Unstructured support is unavailable; install langchain-unstructured[local]"
            ) from exc
        try:
            docs = UnstructuredLoader(file_path=str(path)).load()
        except Exception as exc:
            raise RuntimeError(f"Unstructured could not parse {path.name}: {exc}") from exc
        tagged = _tag(docs, path, "unstructured")
        logger.info("Loaded %d document(s) with Unstructured from %s", len(tagged), path)
        return tagged


class CSVDocumentLoader:
    def __init__(self, source_column: str | None = None, encoding: str | None = None):
        self.source_column = source_column
        self.encoding = encoding

    def load(self, file_path: str | Path) -> list[Document]:
        path = _validated(file_path)
        from langchain_community.document_loaders import CSVLoader

        docs = CSVLoader(
            file_path=str(path),
            source_column=self.source_column,
            encoding=self.encoding,
        ).load()
        return _tag(docs, path, "csv")


class JSONDocumentLoader:
    def __init__(self, jq_schema: str = ".[].content", text_content: bool = False):
        self.jq_schema = jq_schema
        self.text_content = text_content

    def load(self, file_path: str | Path) -> list[Document]:
        path = _validated(file_path)
        from langchain_community.document_loaders import JSONLoader

        docs = JSONLoader(
            file_path=str(path),
            jq_schema=self.jq_schema,
            text_content=self.text_content,
        ).load()
        return _tag(docs, path, "json")


class WebDocumentLoader:
    def load(self, urls: str | Iterable[str]) -> list[Document]:
        url_list = [urls] if isinstance(urls, str) else list(urls)
        if not url_list:
            return []
        safe_urls = [validate_web_url(url) for url in url_list]
        from langchain_community.document_loaders import WebBaseLoader

        docs = WebBaseLoader(safe_urls).load()
        normalized: list[Document] = []
        for doc in docs:
            metadata = dict(doc.metadata or {})
            metadata.update(
                {
                    "source": str(metadata.get("source", "")),
                    "source_type": "web",
                    "loader": "web",
                    "metadata_version": METADATA_VERSION,
                }
            )
            normalized.append(Document(page_content=doc.page_content, metadata=metadata))
        return normalized


class DirectoryDocumentLoader:
    def __init__(self, glob: str = "**/*.*", show_progress: bool = True, recursive: bool = True):
        self.glob = glob
        self.show_progress = show_progress
        self.recursive = recursive

    def load(self, dir_path: str | Path) -> list[Document]:
        path = Path(dir_path).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise DocumentValidationError(f"Not a directory: {path}")
        docs: list[Document] = []
        iterator = path.rglob("*") if self.recursive else path.glob("*")
        for file_path in sorted(iterator, key=lambda item: item.as_posix()):
            if not file_path.is_file() or not self.glob_matches(file_path):
                continue
            try:
                docs.extend(route_loader(file_path, allowed_root=path))
            except Exception as exc:
                logger.warning("Skipping %s: %s", file_path, exc)
        return docs

    def glob_matches(self, file_path: Path) -> bool:
        return file_path.match(self.glob) or file_path.name == self.glob


class FallbackPDFLoader:
    def load(self, file_path: str | Path) -> list[Document]:
        path = _validated(file_path)
        from langchain_community.document_loaders import PyPDFLoader

        docs = PyPDFLoader(str(path)).load()
        return _tag(docs, path, "pypdf")


def load_text_file(file_path: str | Path | None = None) -> list[Document]:
    """Load a text file; the argument is required (legacy helper retained)."""
    if file_path is None:
        raise TypeError("load_text_file() requires a file_path")
    path = _validated(file_path)
    return _tag(TextLoader(str(path), encoding="utf-8").load(), path, "text")


def route_loader(
    file_path: str | Path,
    *,
    allowed_root: str | Path | None = None,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
) -> list[Document]:
    """Validate a local file and dispatch it to the appropriate loader."""
    path = validate_file_path(file_path, allowed_root=allowed_root, max_file_size=max_file_size)
    extension = path.suffix.lower()
    if extension == ".csv":
        return CSVDocumentLoader().load(path)
    if extension == ".json":
        return JSONDocumentLoader().load(path)
    if extension in DOCLING_EXTS:
        try:
            return DoclingDocumentLoader().load(path)
        except Exception as exc:
            if extension == ".pdf":
                logger.warning("Docling failed for %s; trying PyPDF: %s", path, exc)
                return FallbackPDFLoader().load(path)
            raise
    if extension in UNSTRUCTURED_EXTS:
        return UnstructuredDocumentLoader().load(path)
    if extension in TEXT_EXTS:
        return _tag(TextLoader(str(path), encoding="utf-8").load(), path, "text")
    raise DocumentValidationError(f"No loader configured for extension {extension!r}")


__all__ = [
    "ALLOWED_EXTENSIONS",
    "CSVDocumentLoader",
    "DEFAULT_MAX_FILE_SIZE",
    "DirectoryDocumentLoader",
    "DoclingDocumentLoader",
    "DocumentValidationError",
    "FallbackPDFLoader",
    "JSONDocumentLoader",
    "OptionalLoaderUnavailable",
    "TextLoader",
    "UnstructuredDocumentLoader",
    "WebDocumentLoader",
    "_tag",
    "load_text_file",
    "route_loader",
    "validate_file_path",
    "validate_web_url",
]
