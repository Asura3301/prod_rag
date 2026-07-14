"""Optional OpenTelemetry setup with no mandatory vendor dependency."""

from __future__ import annotations

from typing import Any


def configure_telemetry(
    *,
    service_name: str,
    endpoint: str | None = None,
    fastapi_app: Any | None = None,
) -> bool:
    """Configure OTLP tracing when observability extras are installed.

    Returns ``False`` when telemetry packages are unavailable, allowing the
    application to run without a hosted tracing backend.
    """

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return False

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)

    if fastapi_app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        except ImportError:
            return True
        FastAPIInstrumentor.instrument_app(fastapi_app)
    return True


__all__ = ["configure_telemetry"]
