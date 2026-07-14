FROM python:3.14-slim AS runtime

ARG RAG_EXTRAS="qdrant,pgvector,observability"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system rag && useradd --system --gid rag --create-home rag
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
COPY migrations ./migrations
RUN pip install ".[${RAG_EXTRAS}]"

USER rag
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]

CMD ["uvicorn", "rag_app.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
