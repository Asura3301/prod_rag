"""Composition helpers for the end-to-end local runtime."""

from .composition import (
    build_local_service,
    create_runtime_app,
    run_local_worker,
    worker_main,
)
from .doctor import DoctorCheck, run_doctor
from .repository import SQLiteDocumentRepository, StoredDocument
from .service import GraphRetrieverAdapter, LocalRAGService
from .worker import LocalIngestionHandler

__all__ = [
    "GraphRetrieverAdapter",
    "DoctorCheck",
    "LocalIngestionHandler",
    "LocalRAGService",
    "SQLiteDocumentRepository",
    "StoredDocument",
    "build_local_service",
    "create_runtime_app",
    "run_doctor",
    "run_local_worker",
    "worker_main",
]
