import os
import tempfile
from pathlib import Path
from typing import Iterable

from langchain_core.documents import Document
from langchain_docling.loaders import DoclingLoader, ExportType
from langchain_unstructured import UnstructuredLoader
from docling.chunking import HybridChunker

from langchain_community.document_loaders import (
    CSVLoader,
    JSONLoader,
    WebBaseLoader,
    DirectoryLoader,
    TextLoader,
    PyPDFLoader,
)

from dotenv import load_dotenv

load_dotenv()

#----------------------------------------------------------
# Configuration
FILE_PATH = os.getenv("FILE_PATH")
EXPORT_TYPE = ExportType.DOC_CHUNKS
EMBED_MODEL_ID = os.getenv("EMBED_MODEL_ID")

#----------------------------------------------------------
# Routing tables — which loader handles which file extension.
# Docling is preferred for rich-layout documents (PDF, modern Office, HTML, MD, notebooks).
# Unstructured is the fallback for legacy/binary formats Docling cannot parse.
DOCLING_EXTS = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".xhtml", ".md", ".ipynb"}
UNSTRUCTURED_EXTS = {
    ".doc", ".ppt", ".xls", ".rtf", ".epub", ".eml", ".msg",
    ".odt", ".odp", ".ods", ".rst", ".org",
}

#----------------------------------------------------------
# Metadata helper
def _tag(docs: Iterable[Document], path: Path, loader_name: str) -> list[Document]:
    tagged: list[Document] = []
    for doc in docs:
        doc.metadata.update(
            {
                "source": str(path),
                "filename": path.name,
                "suffix": path.suffix.lower(),
                "loader": loader_name,
            }
        )
        tagged.append(doc)
    return tagged


#----------------------------------------------------------
# Test Text Loader
def load_text_file():
    # Create temporary text file for demonstration
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as temp_file:
        temp_file.write(b"This is a test file.\nThis file is used to test the text loader.")
        temp_file_path = temp_file.name

    try:
        # Load the text file using TextLoader
        loader = TextLoader(temp_file_path)
        documents = loader.load()

        print(f"Loaded {len(documents)} document(s)")
        print(f"Content preview: {documents[0].page_content[:100]}...")
        print(f"Metadata: {documents[0].metadata}")

    finally:
        # Delete the temporary file
        os.remove(temp_file_path)


#----------------------------------------------------------
# Docling DocumentLoader — rich-layout documents (PDF, DOCX, PPTX, XLSX, HTML, MD, IPYNB)
class DoclingDocumentLoader:
    def load(self, file_path: str | Path) -> list[Document]:
        path = Path(file_path)

        loader = DoclingLoader(
            file_path=str(path),
            export_type=EXPORT_TYPE,
            chunker=HybridChunker(tokenizer=EMBED_MODEL_ID),
        )
        docs = loader.load()
        docs = _tag(docs, path, "docling")

        print(f"[docling] Loaded {len(docs)} document(s) from {path.name}")
        for doc in docs[:3]:
            print(f"  {doc.page_content[:100]!r}")
        return docs


#----------------------------------------------------------
# Unstructured Document Loader — legacy/binary & email/ebook formats
class UnstructuredDocumentLoader:
    def load(self, file_path: str | Path) -> list[Document]:
        path = Path(file_path)

        loader = UnstructuredLoader(file_path=str(path))
        docs = loader.load()
        docs = _tag(docs, path, "unstructured")

        print(f"[unstructured] Loaded {len(docs)} document(s) from {path.name}")
        for doc in docs[:3]:
            print(f"  {doc.page_content[:100]!r}")
        return docs


#----------------------------------------------------------
# CSV Loader — one document per row, preserving row context.
# source_column is optional; set it to a column name to use its value as source.
class CSVDocumentLoader:
    def __init__(self, source_column: str | None = None, encoding: str | None = None):
        self.source_column = source_column
        self.encoding = encoding

    def load(self, file_path: str | Path) -> list[Document]:
        path = Path(file_path)

        loader = CSVLoader(
            file_path=str(path),
            source_column=self.source_column,
            encoding=self.encoding,
        )
        docs = loader.load()
        docs = _tag(docs, path, "csv")

        print(f"[csv] Loaded {len(docs)} row(s) from {path.name}")
        for doc in docs[:3]:
            print(f"  row={doc.metadata.get('row')} {doc.page_content[:100]!r}")
        return docs


#----------------------------------------------------------
# JSON Loader — extracts nested content via a jq schema.
# Default jq_schema ".[].content" assumes a list of objects with a "content" key.
# Override for your actual JSON shape, e.g. '.messages[].text'.
class JSONDocumentLoader:
    def __init__(self, jq_schema: str = ".[].content", text_content: bool = False):
        self.jq_schema = jq_schema
        self.text_content = text_content

    def load(self, file_path: str | Path) -> list[Document]:
        path = Path(file_path)

        loader = JSONLoader(
            file_path=str(path),
            jq_schema=self.jq_schema,
            text_content=self.text_content,
        )
        docs = loader.load()
        docs = _tag(docs, path, "json")

        print(f"[json] Loaded {len(docs)} item(s) from {path.name}")
        for doc in docs[:3]:
            print(f"  {doc.page_content[:100]!r}")
        return docs


#----------------------------------------------------------
# Web Loader — fetches and parses a live URL (or list of URLs).
class WebDocumentLoader:
    def load(self, urls: str | Iterable[str]) -> list[Document]:
        url_list = [urls] if isinstance(urls, str) else list(urls)

        loader = WebBaseLoader(url_list)
        docs = loader.load()

        # WebBaseLoader already sets "source" = url; we enrich with loader tag.
        for doc in docs:
            doc.metadata.update({"loader": "web"})

        print(f"[web] Loaded {len(docs)} page(s) from {len(url_list)} URL(s)")
        for doc in docs[:3]:
            print(f"  {doc.metadata.get('source')} -> {doc.page_content[:80]!r}")
        return docs


#----------------------------------------------------------
# Directory Loader — batch-loads a folder using a loader-per-extension.
# Uses a glob pattern (e.g. "**/*.pdf") and a loader class factory.
class DirectoryDocumentLoader:
    def __init__(self, glob: str = "**/*.*", show_progress: bool = True, recursive: bool = True):
        self.glob = glob
        self.show_progress = show_progress
        self.recursive = recursive

    def load(self, dir_path: str | Path) -> list[Document]:
        path = Path(dir_path)

        # Simple strategy: route each file through the dispatcher below.
        docs: list[Document] = []
        for file in path.rglob("*") if self.recursive else path.glob("*"):
            if not file.is_file():
                continue
            if not self.glob_matches(file):
                continue
            try:
                docs.extend(route_loader(file))
            except Exception as exc:  # noqa: BLE001 — skip unreadable files in batch mode
                print(f"[directory] skipped {file.name}: {exc}")

        print(f"[directory] Loaded {len(docs)} document(s) from {path}")
        return docs

    def glob_matches(self, file_path: Path) -> bool:
        # Minimal glob support: only the suffix part of self.glob is checked.
        pattern = self.glob.split("*")[-1]
        return file_path.name.endswith(pattern) if pattern else True


#----------------------------------------------------------
# Fallback PDF Loader — used when Docling fails on a tricky PDF
class FallbackPDFLoader:
    def load(self, file_path: str | Path) -> list[Document]:
        path = Path(file_path)

        loader = PyPDFLoader(str(path))
        docs = loader.load()
        docs = _tag(docs, path, "pypdf")

        print(f"[pypdf] Loaded {len(docs)} page(s) from {path.name}")
        for doc in docs[:3]:
            print(f"  page={doc.metadata.get('page')} {doc.page_content[:100]!r}")
        return docs


#----------------------------------------------------------
# Dispatcher — pick the right loader by file extension.
# Order of preference: specialized (CSV/JSON) > Docling > Unstructured > Text fallback.
def route_loader(file_path: str | Path) -> list[Document]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(path)

    ext = path.suffix.lower()

    if ext == ".csv":
        return CSVDocumentLoader().load(path)
    if ext == ".json":
        return JSONDocumentLoader().load(path)

    if ext in DOCLING_EXTS:
        try:
            return DoclingDocumentLoader().load(path)
        except Exception as exc:  # noqa: BLE001 — fall back for PDFs Docling chokes on
            if ext == ".pdf":
                print(f"[route] Docling failed on {path.name} ({exc}); using PyPDFLoader")
                return FallbackPDFLoader().load(path)
            raise

    if ext in UNSTRUCTURED_EXTS:
        return UnstructuredDocumentLoader().load(path)

    # Plain text and anything else -> TextLoader, then Unstructured as last resort.
    if ext in {".txt", ".log", ".py", ".js", ".ts", ".sh", ".yaml", ".yml", ".ini", ".cfg"}:
        docs = TextLoader(str(path), encoding="utf-8").load()
        docs = _tag(docs, path, "text")
        return docs

    return UnstructuredDocumentLoader().load(path)


#----------------------------------------------------------
if __name__ == "__main__":
    docs = route_loader(FILE_PATH)
    print(docs)
    for doc in docs:
        print(doc.page_content)
        print(doc.metadata)
        print("-"*100)

