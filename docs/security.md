# Security

## Controls

- Terminate TLS at a trusted ingress and restrict allowed origins and hosts there.
- Keep administrative endpoints disabled or set a high-entropy `RAG_ADMIN_TOKEN`; rotate it
  through the deployment secret manager.
- Add end-user authentication and tenant authorization before exposing document or query routes.
- Treat retrieved text as untrusted input. Do not allow it to override system instructions or
  trigger tools without policy checks.
- Validate source schemes, size, media type, archive depth, and network destinations in the
  production ingestion adapter. Scan uploads before parsing.
- Encrypt stores and backups, use per-service credentials, and apply retention/deletion policy.
- Do not log document text, prompts, bearer tokens, or model credentials. Use request IDs and
  redacted structured metadata.
- Pin production images by digest and review dependency/container scan findings.

## Threat model highlights

Primary risks are cross-tenant retrieval, prompt injection, malicious documents, server-side
request forgery during URL ingestion, data leakage through logs/citations, denial of service, and
queue replay. Tenant-scoped filters must be enforced by the server-side adapter—not accepted
solely from client input. Idempotency limits accidental replay but is not authorization.

Rate limits, maximum query/top-k sizes, ingestion quotas, worker timeouts, and dead-letter alerts
should be configured at deployment. See the root `SECURITY.md` for vulnerability reporting.
