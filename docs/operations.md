# Operations Runbook

## Signals

Monitor request rate/errors/duration, query p95 latency, queue depth and oldest age, retry and
dead-letter rates, index freshness, citation coverage, model errors, and data-store saturation.
Alert on sustained symptoms rather than single failures.

## Triage

- **Readiness failing:** check configured retrieval/model dependencies and recent rollout.
- **Queue growing:** inspect worker capacity, poison jobs, Redis availability, and downstream
  throttling. Scale only after ruling out a systemic failure.
- **Dead letters:** preserve payload metadata, identify the deterministic failure, deploy a fix,
  then replay with a new controlled idempotency key.
- **Latency regression:** compare retrieval, reranking, and generation timings; reduce traffic or
  roll back before changing quality parameters.
- **Citation regression:** stop rollout and compare corpus/index versions against the golden set.

Cancellation is best effort: a running handler must cooperate with cancellation for immediate
termination. Avoid deleting dead-letter records until incident analysis and retention obligations
are complete.
