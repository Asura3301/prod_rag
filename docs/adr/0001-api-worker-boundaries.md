# ADR-0001: Separate API, service, and queue boundaries

- Status: Accepted
- Date: 2026-07-10

## Context

RAG query traffic is latency-sensitive while parsing and indexing are long-running, failure-prone
operations. Tests must run without infrastructure, and production adapters must remain replaceable.

## Decision

Expose a FastAPI application factory that accepts `RAGService` and `JobQueue` protocols. Provide
in-memory defaults for local use and a lazy-import Redis queue for distributed workers. Keep the
worker handler registry independent of HTTP and domain implementation.

## Consequences

The application is testable without external services and can scale API and worker processes
independently. Protocol adapters add explicit integration work, and process-local defaults are not
durable or suitable for multi-replica production deployments.
