"""Structure-aware chunking interfaces with a dependency-free fallback."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from langchain_core.documents import Document


@runtime_checkable
class ChunkingStrategy(Protocol):
    """Converts one loaded document into ordered text chunks."""

    @property
    def version(self) -> str:
        ...

    def split(self, document: Document) -> list[str]:
        ...


@dataclass(frozen=True, slots=True)
class TextChunker:
    """Paragraph-aware text chunker with bounded character fallback."""

    chunk_size: int = 1_000
    overlap: int = 100
    version: str = "text-v1"

    def __post_init__(self) -> None:
        if self.chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        if not 0 <= self.overlap < self.chunk_size:
            raise ValueError("overlap must satisfy 0 <= overlap < chunk_size")

    def split(self, document: Document) -> list[str]:
        text = _normalize_text(document.page_content)
        if not text:
            return []
        paragraphs = re.split(r"\n\s*\n", text)
        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if len(paragraph) > self.chunk_size:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(self._window(paragraph))
                continue
            candidate = f"{current}\n\n{paragraph}" if current else paragraph
            if len(candidate) <= self.chunk_size:
                current = candidate
            else:
                chunks.append(current)
                prefix = current[-self.overlap :] if self.overlap else ""
                current = f"{prefix}\n\n{paragraph}".strip()
                if len(current) > self.chunk_size:
                    chunks.extend(self._window(current))
                    current = ""
        if current:
            chunks.append(current)
        return [chunk for chunk in chunks if chunk]

    def _window(self, text: str) -> list[str]:
        step = self.chunk_size - self.overlap
        return [
            text[start : start + self.chunk_size].strip()
            for start in range(0, len(text), step)
            if text[start : start + self.chunk_size].strip()
        ]


@dataclass(frozen=True, slots=True)
class StructureAwareChunker:
    """Keeps Markdown-like sections together, then applies text fallback."""

    fallback: TextChunker = TextChunker()
    version: str = "structure-v1"

    def split(self, document: Document) -> list[str]:
        text = _normalize_text(document.page_content)
        if not text:
            return []
        sections = _markdown_sections(text)
        if len(sections) <= 1:
            return self.fallback.split(document)
        chunks: list[str] = []
        for section in sections:
            chunks.extend(
                self.fallback.split(
                    Document(page_content=section, metadata=document.metadata)
                )
            )
        return chunks


def _normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _markdown_sections(text: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if re.match(r"^\s{0,3}#{1,6}\s+\S", line) and current:
            sections.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current).strip())
    return [section for section in sections if section]


__all__ = ["ChunkingStrategy", "StructureAwareChunker", "TextChunker"]
