"""Production checkpointer construction kept separate from graph assembly."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


@contextmanager
def postgres_checkpointer(dsn: str) -> Iterator[Any]:
    """Yield an initialized LangGraph Postgres checkpointer.

    Callers own the context lifetime and should keep it open for the lifetime of
    the compiled graph. Checkpoint retention is deployment-specific and is
    documented in ``docs/operations.md``.
    """

    if not dsn:
        raise ValueError("A PostgreSQL DSN is required")
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:
        raise RuntimeError(
            "Install the 'pgvector' extra to use the Postgres checkpointer"
        ) from exc
    with PostgresSaver.from_conn_string(dsn) as checkpointer:
        checkpointer.setup()
        yield checkpointer


__all__ = ["postgres_checkpointer"]
