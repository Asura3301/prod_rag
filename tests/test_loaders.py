"""
Smoke-tests for every document loader in src/rag_app/ingestion/document_loaders.py.

Auto-generated fixtures (no user action needed):
  .txt  .csv  .json  .html  .md  .ipynb

Manual fixtures — place real files in  tests/data/manual/ :
  sample.pdf   sample.docx  sample.pptx  sample.xlsx
  sample.doc   sample.rtf   sample.epub  sample.eml

Run:
  uv run python tests/test_loaders.py
"""

import json
import os
import sys
import traceback
from pathlib import Path

# ── make the src package importable without installing the project ──────────
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rag_app.ingestion.document_loaders import (  # noqa: E402
    CSVDocumentLoader,
    DirectoryDocumentLoader,
    DoclingDocumentLoader,
    FallbackPDFLoader,
    JSONDocumentLoader,
    TextLoader,
    UnstructuredDocumentLoader,
    WebDocumentLoader,
    _tag,
    route_loader,
)
from langchain_core.documents import Document

# ── paths ───────────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
GENERATED = DATA_DIR / "generated"
MANUAL = DATA_DIR / "manual"

GENERATED.mkdir(parents=True, exist_ok=True)
MANUAL.mkdir(parents=True, exist_ok=True)

# ── colour output ───────────────────────────────────────────────────────────
GREEN = "\033[92m"
RED   = "\033[91m"
CYAN  = "\033[96m"
RESET = "\033[0m"
BOLD  = "\033[1m"

results: list[tuple[str, bool, str]] = []


def _header(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*60}{RESET}")


def _ok(name: str, docs: list[Document]) -> None:
    print(f"{GREEN}  PASS{RESET}  {name} — {len(docs)} doc(s)")
    for d in docs[:2]:
        preview = d.page_content[:120].replace("\n", " ")
        loader  = d.metadata.get("loader", "?")
        print(f"         loader={loader!r}  {preview!r}")
    results.append((name, True, ""))


def _fail(name: str, exc: Exception) -> None:
    msg = f"{type(exc).__name__}: {exc}"
    print(f"{RED}  FAIL{RESET}  {name} — {msg}")
    print(f"         {''.join(traceback.format_exception(exc)[-3:]).strip()}")
    results.append((name, False, msg))


def _require(path: Path, name: str) -> bool:
    """Return True if the file exists; otherwise print a skip notice."""
    if path.exists():
        return True
    print(f"  SKIP  {name} — place a real file at:\n"
          f"         {path}\n"
          f"         Then re-run the tests.")
    results.append((name, None, "missing fixture"))
    return False


# ════════════════════════════════════════════════════════════════════════════
# Fixture generators — create minimal valid files for formats we can generate
# ════════════════════════════════════════════════════════════════════════════

def _make_txt() -> Path:
    p = GENERATED / "sample.txt"
    p.write_text(
        "Первая строка тестового файла.\nВторая строка.\nTesting TextLoader.\n",
        encoding="utf-8",
    )
    return p


def _make_csv() -> Path:
    p = GENERATED / "sample.csv"
    p.write_text(
        "id,title,body\n"
        "1,First,This is the first row of the CSV file.\n"
        "2,Second,This is the second row with some content.\n"
        "3,Third,Another record for testing purposes.\n",
        encoding="utf-8",
    )
    return p


def _make_json() -> Path:
    p = GENERATED / "sample.json"
    data = [
        {"content": "First JSON record — information about document loaders."},
        {"content": "Second JSON record — testing JSONLoader with jq schema."},
        {"content": "Third JSON record — verifying metadata tagging."},
    ]
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _make_html() -> Path:
    p = GENERATED / "sample.html"
    p.write_text(
        """<!DOCTYPE html>
<html lang="ru">
<head><meta charset="UTF-8"><title>Test Page</title></head>
<body>
  <h1>Тестовая страница</h1>
  <p>This HTML file is used to verify that DoclingDocumentLoader can parse HTML.</p>
  <p>Paragraph two with <strong>bold text</strong> and a second sentence.</p>
  <h2>Section 2</h2>
  <p>More content here for chunking tests.</p>
</body>
</html>
""",
        encoding="utf-8",
    )
    return p


def _make_md() -> Path:
    p = GENERATED / "sample.md"
    p.write_text(
        """# Test Document

## Introduction

This Markdown file tests `DoclingDocumentLoader` with `.md` extension.

## Section One

Content under section one with some **bold** and *italic* text.

## Section Two

- item alpha
- item beta
- item gamma

## Conclusion

End of the test Markdown document.
""",
        encoding="utf-8",
    )
    return p


def _make_ipynb() -> Path:
    p = GENERATED / "sample.ipynb"
    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"}},
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": ["# Test Notebook\n", "This notebook tests the Docling IPYNB loader.\n"],
                "id": "cell1",
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": ["x = 42\nprint(f'Answer is {x}')"],
                "id": "cell2",
            },
        ],
    }
    p.write_text(json.dumps(nb, indent=2), encoding="utf-8")
    return p


def _make_docx() -> Path:
    """Generate a minimal .docx file using python-docx if available."""
    p = GENERATED / "sample.docx"
    if p.exists():
        return p
    try:
        from docx import Document as DocxDocument  # python-docx
        doc = DocxDocument()
        doc.add_heading("Test DOCX Document", 0)
        doc.add_paragraph("This DOCX was auto-generated for loader smoke tests.")
        doc.add_heading("Section 1", level=1)
        doc.add_paragraph("Content of section one. Second sentence for chunking.")
        doc.add_heading("Section 2", level=1)
        doc.add_paragraph("Content of section two. Additional text for testing.")
        doc.save(str(p))
    except ImportError:
        return None  # signal: cannot generate, need manual file
    return p


def _make_xlsx() -> Path:
    """Generate a minimal .xlsx file using openpyxl."""
    p = GENERATED / "sample.xlsx"
    if p.exists():
        return p
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.append(["id", "name", "description"])
        ws.append([1, "Alpha", "First row of spreadsheet data."])
        ws.append([2, "Beta",  "Second row of spreadsheet data."])
        ws.append([3, "Gamma", "Third row of spreadsheet data."])
        wb.save(str(p))
    except ImportError:
        return None
    return p


# ════════════════════════════════════════════════════════════════════════════
# Test functions — one per loader
# ════════════════════════════════════════════════════════════════════════════

def test_text_loader() -> None:
    _header("TextLoader  (.txt)")
    path = _make_txt()
    try:
        loader = TextLoader(str(path), encoding="utf-8")
        docs   = loader.load()
        docs   = _tag(docs, path, "text")
        assert docs, "No documents returned"
        _ok("TextLoader", docs)
    except Exception as exc:
        _fail("TextLoader", exc)


def test_docling_html() -> None:
    _header("DoclingDocumentLoader  (.html)")
    path = _make_html()
    try:
        docs = DoclingDocumentLoader().load(path)
        assert docs
        _ok("DoclingDocumentLoader [html]", docs)
    except Exception as exc:
        _fail("DoclingDocumentLoader [html]", exc)


def test_docling_md() -> None:
    _header("DoclingDocumentLoader  (.md)")
    path = _make_md()
    try:
        docs = DoclingDocumentLoader().load(path)
        assert docs
        _ok("DoclingDocumentLoader [md]", docs)
    except Exception as exc:
        _fail("DoclingDocumentLoader [md]", exc)


def test_ipynb_via_text_loader() -> None:
    # Docling does NOT support .ipynb in current versions (not in allowed formats list).
    # Notebooks are plain JSON so TextLoader reads them verbatim; route_loader handles this.
    _header("TextLoader  (.ipynb — notebooks via route_loader)")
    path = _make_ipynb()
    try:
        docs = route_loader(path)
        assert docs
        _ok("route_loader [ipynb → text]", docs)
    except Exception as exc:
        _fail("route_loader [ipynb → text]", exc)


def test_docling_docx_generated() -> None:
    _header("DoclingDocumentLoader  (.docx — auto-generated)")
    path = _make_docx()
    if path is None:
        print("  SKIP  DoclingDocumentLoader [docx generated]"
              " — install python-docx to auto-generate, or place sample.docx in tests/data/manual/")
        results.append(("DoclingDocumentLoader [docx generated]", None, "python-docx not installed"))
        return
    try:
        docs = DoclingDocumentLoader().load(path)
        assert docs
        _ok("DoclingDocumentLoader [docx generated]", docs)
    except Exception as exc:
        _fail("DoclingDocumentLoader [docx generated]", exc)


def test_docling_xlsx_generated() -> None:
    _header("DoclingDocumentLoader  (.xlsx — auto-generated)")
    path = _make_xlsx()
    if path is None:
        print("  SKIP  DoclingDocumentLoader [xlsx generated]"
              " — install openpyxl to auto-generate, or place sample.xlsx in tests/data/manual/")
        results.append(("DoclingDocumentLoader [xlsx generated]", None, "openpyxl not installed"))
        return
    try:
        docs = DoclingDocumentLoader().load(path)
        assert docs
        _ok("DoclingDocumentLoader [xlsx generated]", docs)
    except Exception as exc:
        _fail("DoclingDocumentLoader [xlsx generated]", exc)


# ── Manual fixtures ──────────────────────────────────────────────────────────

def test_docling_pdf() -> None:
    _header("DoclingDocumentLoader  (.pdf) + FallbackPDFLoader")
    path = MANUAL / "sample.pdf"
    if not _require(path, "DoclingDocumentLoader [pdf]"):
        return
    try:
        docs = DoclingDocumentLoader().load(path)
        assert docs
        _ok("DoclingDocumentLoader [pdf]", docs)
    except Exception as exc:
        _fail("DoclingDocumentLoader [pdf]", exc)
        # Try fallback
        try:
            docs = FallbackPDFLoader().load(path)
            assert docs
            _ok("FallbackPDFLoader [pdf fallback]", docs)
        except Exception as exc2:
            _fail("FallbackPDFLoader [pdf fallback]", exc2)


def test_fallback_pdf_direct() -> None:
    _header("FallbackPDFLoader  (PyPDFLoader — direct)")
    path = MANUAL / "sample.pdf"
    if not _require(path, "FallbackPDFLoader [pdf direct]"):
        return
    try:
        docs = FallbackPDFLoader().load(path)
        assert docs
        _ok("FallbackPDFLoader [pdf direct]", docs)
    except Exception as exc:
        _fail("FallbackPDFLoader [pdf direct]", exc)


def test_docling_pptx() -> None:
    _header("DoclingDocumentLoader  (.pptx)")
    path = MANUAL / "sample.pptx"
    if not _require(path, "DoclingDocumentLoader [pptx]"):
        return
    try:
        docs = DoclingDocumentLoader().load(path)
        assert docs
        _ok("DoclingDocumentLoader [pptx]", docs)
    except Exception as exc:
        _fail("DoclingDocumentLoader [pptx]", exc)


def test_docling_docx_manual() -> None:
    _header("DoclingDocumentLoader  (.docx — manual)")
    path = MANUAL / "sample.docx"
    if not _require(path, "DoclingDocumentLoader [docx manual]"):
        return
    try:
        docs = DoclingDocumentLoader().load(path)
        assert docs
        _ok("DoclingDocumentLoader [docx manual]", docs)
    except Exception as exc:
        _fail("DoclingDocumentLoader [docx manual]", exc)


def test_unstructured_doc() -> None:
    _header("UnstructuredDocumentLoader  (.doc — legacy Word)")
    path = MANUAL / "sample.doc"
    if not _require(path, "UnstructuredDocumentLoader [doc]"):
        return
    try:
        docs = UnstructuredDocumentLoader().load(path)
        assert docs
        _ok("UnstructuredDocumentLoader [doc]", docs)
    except FileNotFoundError as exc:
        if "soffice" in str(exc):
            print(f"  SKIP  UnstructuredDocumentLoader [doc] — LibreOffice not installed.\n"
                  f"         Install from https://www.libreoffice.org/ and ensure 'soffice' is in PATH.")
            results.append(("UnstructuredDocumentLoader [doc]", None, "LibreOffice (soffice) not installed"))
        else:
            _fail("UnstructuredDocumentLoader [doc]", exc)
    except Exception as exc:
        _fail("UnstructuredDocumentLoader [doc]", exc)


def test_unstructured_rtf() -> None:
    _header("UnstructuredDocumentLoader  (.rtf)")
    path = MANUAL / "sample.rtf"
    if not _require(path, "UnstructuredDocumentLoader [rtf]"):
        return
    try:
        docs = UnstructuredDocumentLoader().load(path)
        assert docs
        _ok("UnstructuredDocumentLoader [rtf]", docs)
    except Exception as exc:
        _fail("UnstructuredDocumentLoader [rtf]", exc)


def test_unstructured_epub() -> None:
    _header("UnstructuredDocumentLoader  (.epub)")
    path = MANUAL / "sample.epub"
    if not _require(path, "UnstructuredDocumentLoader [epub]"):
        return
    try:
        docs = UnstructuredDocumentLoader().load(path)
        assert docs
        _ok("UnstructuredDocumentLoader [epub]", docs)
    except Exception as exc:
        _fail("UnstructuredDocumentLoader [epub]", exc)


def test_unstructured_eml() -> None:
    _header("UnstructuredDocumentLoader  (.eml)")
    path = MANUAL / "sample.eml"
    if not _require(path, "UnstructuredDocumentLoader [eml]"):
        return
    try:
        docs = UnstructuredDocumentLoader().load(path)
        assert docs
        _ok("UnstructuredDocumentLoader [eml]", docs)
    except Exception as exc:
        _fail("UnstructuredDocumentLoader [eml]", exc)


# ── CSV & JSON ───────────────────────────────────────────────────────────────

def test_csv_loader() -> None:
    _header("CSVDocumentLoader  (.csv)")
    path = _make_csv()
    try:
        docs = CSVDocumentLoader().load(path)
        assert docs
        _ok("CSVDocumentLoader", docs)
    except Exception as exc:
        _fail("CSVDocumentLoader", exc)


def test_json_loader() -> None:
    _header("JSONDocumentLoader  (.json)")
    path = _make_json()
    try:
        docs = JSONDocumentLoader(jq_schema=".[].content", text_content=True).load(path)
        assert docs
        _ok("JSONDocumentLoader", docs)
    except Exception as exc:
        _fail("JSONDocumentLoader", exc)


# ── Web ──────────────────────────────────────────────────────────────────────

def test_web_loader() -> None:
    _header("WebDocumentLoader  (live URL)")
    url = "https://example.com"
    try:
        docs = WebDocumentLoader().load(url)
        assert docs
        _ok("WebDocumentLoader", docs)
    except Exception as exc:
        _fail("WebDocumentLoader", exc)


# ── Directory ────────────────────────────────────────────────────────────────

def test_directory_loader() -> None:
    _header("DirectoryDocumentLoader  (generated/ folder, txt+csv+json)")
    # ensure at least a few generated files exist
    _make_txt()
    _make_csv()
    _make_json()
    try:
        docs = DirectoryDocumentLoader(glob="**/*.*", recursive=True).load(GENERATED)
        assert docs
        _ok("DirectoryDocumentLoader", docs)
    except Exception as exc:
        _fail("DirectoryDocumentLoader", exc)


# ── route_loader integration ─────────────────────────────────────────────────

def test_route_loader_txt() -> None:
    _header("route_loader  — auto-dispatch .txt")
    path = _make_txt()
    try:
        docs = route_loader(path)
        assert docs
        _ok("route_loader [txt]", docs)
    except Exception as exc:
        _fail("route_loader [txt]", exc)


def test_route_loader_csv() -> None:
    _header("route_loader  — auto-dispatch .csv")
    path = _make_csv()
    try:
        docs = route_loader(path)
        assert docs
        _ok("route_loader [csv]", docs)
    except Exception as exc:
        _fail("route_loader [csv]", exc)


def test_route_loader_json() -> None:
    _header("route_loader  — auto-dispatch .json")
    path = _make_json()
    try:
        docs = route_loader(path)
        assert docs
        _ok("route_loader [json]", docs)
    except Exception as exc:
        _fail("route_loader [json]", exc)


# ════════════════════════════════════════════════════════════════════════════
# Main runner
# ════════════════════════════════════════════════════════════════════════════

ALL_TESTS = [
    # ── Auto-generated (no user files needed) ──
    test_text_loader,
    test_csv_loader,
    test_json_loader,
    test_docling_html,
    test_docling_md,
    test_ipynb_via_text_loader,
    test_docling_docx_generated,
    test_docling_xlsx_generated,
    test_web_loader,
    test_directory_loader,
    # ── route_loader integration ──
    test_route_loader_txt,
    test_route_loader_csv,
    test_route_loader_json,
    # ── Manual fixtures (tests/data/manual/) ──
    test_docling_pdf,
    test_fallback_pdf_direct,
    test_docling_pptx,
    test_docling_docx_manual,
    test_unstructured_doc,
    test_unstructured_rtf,
    test_unstructured_epub,
    test_unstructured_eml,
]


def main() -> None:
    print(f"\n{BOLD}=== Document Loader Smoke Tests ==={RESET}")
    print(f"generated fixtures : {GENERATED}")
    print(f"manual fixtures    : {MANUAL}")

    for fn in ALL_TESTS:
        fn()

    # ── Summary ──────────────────────────────────────────────────────────────
    passed  = [r for r in results if r[1] is True]
    failed  = [r for r in results if r[1] is False]
    skipped = [r for r in results if r[1] is None]

    print(f"\n{BOLD}{'═'*60}")
    print(f"  SUMMARY:  "
          f"{GREEN}{len(passed)} passed{RESET}  "
          f"{RED}{len(failed)} failed{RESET}  "
          f"{len(skipped)} skipped")
    print(f"{'═'*60}{RESET}")

    if failed:
        print(f"\n{RED}Failed tests:{RESET}")
        for name, _, msg in failed:
            print(f"  • {name}: {msg}")

    if skipped:
        print(f"\nSkipped (missing manual fixture):")
        for name, _, msg in skipped:
            print(f"  • {name}: {msg}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
