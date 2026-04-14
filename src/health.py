import os
import time
from typing import Any

from config import (
    APPINSIGHTS_CONNECTION_STRING,
    CONTOSO_DEVICE_CONNECTION_STRING,
    LITWARE_DEVICE_CONNECTION_STRING,
    REDIS_URL,
)
from telemetry import get_request_id, get_trace_id
from db import get_admin_db_url, get_conn
from auth_helpers import get_redis_client
from iot_helpers import IoTHubDeviceClient

try:
    import redis as _redis_module
except ImportError:  # pragma: no cover
    _redis_module = None


def check_database_health() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        db_url = get_admin_db_url()
        with get_conn(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
    except Exception as exc:
        return {
            "status": "unhealthy",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": str(exc),
        }

    return {
        "status": "healthy",
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def check_redis_health() -> dict[str, Any]:
    if not REDIS_URL:
        return {"status": "not_configured"}
    if _redis_module is None:
        return {"status": "unhealthy", "error": "redis dependency is not installed"}

    started = time.perf_counter()
    try:
        redis_client = get_redis_client()
        if redis_client is None:
            return {"status": "unhealthy", "error": "redis client could not be created"}
        redis_client.ping()
    except Exception as exc:
        return {
            "status": "unhealthy",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": str(exc),
        }

    return {
        "status": "healthy",
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def check_iot_health() -> dict[str, Any]:
    orgs = {
        "contoso": bool(CONTOSO_DEVICE_CONNECTION_STRING),
        "litware": bool(LITWARE_DEVICE_CONNECTION_STRING),
    }
    missing_orgs = [org for org, configured in orgs.items() if not configured]
    if len(missing_orgs) == len(orgs):
        return {"status": "not_configured"}
    if missing_orgs:
        return {"status": "degraded", "missing_organizations": missing_orgs}
    return {"status": "healthy"}


def build_health_payload() -> tuple[dict[str, Any], int]:
    checks = {
        "database": check_database_health(),
        "redis": check_redis_health(),
        "iot": check_iot_health(),
        "telemetry": {"status": "healthy" if APPINSIGHTS_CONNECTION_STRING else "not_configured"},
    }
    unhealthy_checks = [name for name, result in checks.items() if result["status"] == "unhealthy"]
    overall_status = "healthy" if not unhealthy_checks else "unhealthy"
    status_code = 200 if overall_status == "healthy" else 503
    payload = {
        "status": overall_status,
        "environment": os.getenv("ENV", "local"),
        "request_id": get_request_id(),
        "trace_id": get_trace_id(),
        "checks": checks,
    }
    return payload, status_code
