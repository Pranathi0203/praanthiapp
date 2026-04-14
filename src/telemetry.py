import contextvars
import json
import logging
import os
from typing import Any

from opentelemetry import trace
from opentelemetry.trace.status import Status, StatusCode

from config import (
    APPINSIGHTS_CONNECTION_STRING,
    DATADOG_SERVICE,
    DATADOG_VERSION,
    DD_TRACE_ENABLED,
)

# Shared context var — lives here so all modules can read it without importing main
_request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

DD_TRACE_ENABLED = DD_TRACE_ENABLED  # re-export

if DD_TRACE_ENABLED:
    try:
        import ddtrace.auto  # noqa: F401
        from ddtrace import tracer as dd_tracer
    except ImportError:  # pragma: no cover - optional until Datadog is configured
        dd_tracer = None
else:  # pragma: no cover - exercised via env var in deployed environments
    dd_tracer = None

try:
    from applicationinsights import TelemetryClient as _TelemetryClient
except ImportError:  # pragma: no cover - optional until applicationinsights is installed
    _TelemetryClient = None

logger = logging.getLogger(__name__)

_ai_client: Any = None


def _compact_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value is not None and value != ""}


def get_request_id() -> str:
    return _request_id_context.get("")


def get_trace_id() -> str:
    span = trace.get_current_span()
    if not span:
        return get_datadog_trace_id()
    span_context = span.get_span_context()
    if not span_context or not span_context.is_valid:
        return get_datadog_trace_id()
    return format(span_context.trace_id, "032x")


def get_datadog_log_context() -> dict[str, str]:
    if dd_tracer is None:
        return {}
    try:
        return {key: str(value) for key, value in dd_tracer.get_log_correlation_context().items() if value}
    except Exception:  # pragma: no cover - defensive for telemetry integrations
        return {}


def get_datadog_trace_id() -> str:
    datadog_context = get_datadog_log_context()
    return datadog_context.get("dd.trace_id", "")


def _parse_ikey(connection_string: str) -> str:
    for part in connection_string.split(";"):
        if part.lower().startswith("instrumentationkey="):
            return part.split("=", 1)[1]
    return connection_string


def _get_ai_client() -> Any:
    global _ai_client
    if _TelemetryClient is None or not APPINSIGHTS_CONNECTION_STRING:
        return None
    if _ai_client is None:
        _ai_client = _TelemetryClient(_parse_ikey(APPINSIGHTS_CONNECTION_STRING))
    return _ai_client


def log_event(level: int, event_name: str, **fields: Any):
    payload = _compact_fields(
        {
            "event": event_name,
            "environment": os.getenv("ENV", "local"),
            "request_id": get_request_id(),
            "trace_id": get_trace_id(),
            "service": DATADOG_SERVICE,
            "service_version": DATADOG_VERSION,
            "log_level": logging.getLevelName(level),
            **fields,
            **get_datadog_log_context(),
        }
    )
    logger.log(level, event_name, extra={"custom_dimensions": payload})
    tc = _get_ai_client()
    if tc:
        tc.context.operation.id = payload.get("trace_id") or payload.get("request_id", "")
        tc.track_event(event_name, properties={k: str(v) for k, v in payload.items()})
        tc.flush()


def setup_telemetry(app=None):
    if DD_TRACE_ENABLED:
        if dd_tracer is None:
            logger.warning("DD_TRACE_ENABLED is true but ddtrace is not installed.")
            return
        logger.info(
            "Datadog tracing enabled for service=%s env=%s version=%s",
            DATADOG_SERVICE,
            DATADOG_ENV if 'DATADOG_ENV' in dir() else os.getenv("DD_ENV", "local"),
            DATADOG_VERSION or "unset",
        )
        return

    if not APPINSIGHTS_CONNECTION_STRING:
        logger.warning("Application Insights connection string not set.")
        return

    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor(
        connection_string=APPINSIGHTS_CONNECTION_STRING,
        logging_level=logging.INFO,
    )
    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)


def record_exception_to_telemetry(exc: Exception, **fields: Any):
    log_event(logging.ERROR, "application_exception", error_type=type(exc).__name__, error=str(exc), **fields)
    tc = _get_ai_client()
    if tc:
        try:
            tc.context.operation.id = get_trace_id() or get_request_id()
            tc.track_exception(
                type=type(exc),
                value=exc,
                tb=exc.__traceback__,
                properties={k: str(v) for k, v in _compact_fields(fields).items()},
            )
            tc.flush()
        except Exception as telemetry_exc:
            logging.getLogger(__name__).warning(
                "Application Insights exception tracking failed: %s",
                telemetry_exc,
            )
    span = trace.get_current_span()
    if span and span.is_recording():
        span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR))


def get_http_log_level(status_code: int) -> int:
    if status_code >= 500:
        return logging.ERROR
    if status_code >= 400:
        return logging.WARNING
    return logging.INFO


def record_http_failure_to_telemetry(
    request,
    *,
    status_code: int,
    detail: Any,
    event_name: str,
    **fields: Any,
):
    log_event(
        get_http_log_level(status_code),
        event_name,
        status_code=status_code,
        method=request.method,
        path=request.url.path,
        detail=detail if isinstance(detail, str) else json.dumps(detail),
        **fields,
    )
