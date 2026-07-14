CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS document_registry (
    document_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    status TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, source_uri, version)
);

CREATE INDEX IF NOT EXISTS document_registry_tenant_source_idx
    ON document_registry (tenant_id, source_uri);

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    state TEXT NOT NULL,
    payload JSONB NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ingestion_jobs_state_idx
    ON ingestion_jobs (state, created_at);
