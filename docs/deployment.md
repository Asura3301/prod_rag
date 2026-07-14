# Deployment

## Topology

Run API and worker as separate, non-root containers. Scale API replicas independently from
workers. Use Redis for job coordination and one supported vector backend (pgvector or Qdrant).
Place all data services on private networks and use managed equivalents for production.

## Configuration

Required production configuration includes model credentials, data-store URLs, tenant/auth
policy, and `RAG_ADMIN_TOKEN`. Store secrets in the platform secret manager, not environment
files committed to source control. The Compose defaults are local-development values.

## Rollout

1. Apply backward-compatible database migrations before the application rollout.
2. Deploy workers, then API instances behind readiness-aware load balancing.
3. Run smoke queries against a canary and verify citation quality and latency.
4. Increase traffic gradually while watching error, queue depth, dead-letter, and p95 latency.
5. Roll back application images on regression; do not roll back destructive migrations.

Use `/health/live` only for process restart decisions and `/health/ready` for traffic admission.
Protect `/metrics` at the network layer in addition to its bearer token.

## Local profiles

`docker compose up` starts API and Redis. Add `--profile workers`, then either
`--profile pgvector` or `--profile qdrant`. Volumes persist local data. Back up and test restore
procedures separately before any production launch.
