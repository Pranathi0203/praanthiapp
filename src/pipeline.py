import concurrent.futures
import json
import subprocess
import time
from typing import Any

from config import (
    AZURE_POSTGRES_SERVER_NAME,
    AZURE_RESOURCE_GROUP,
    FUNCTION_APP_NAME,
    IOTHUB_NAME,
    SERVICEBUS_NAMESPACE_NAME,
    WEBAPP_NAME,
)
from telemetry import log_event
from azure_utils import ensure_azure_cli_login
from ai_analysis import build_fix_metadata, sanitize_text, get_cached_postgres_platform_action
from health import check_database_health, check_redis_health

_PIPELINE_STAGE_META = {
    "webapp": {
        "stage_name": "Web Application",
        "description": "FastAPI app serving HTTP requests, auth, REST API, and employee portal.",
        "pipeline_position": 1,
    },
    "iothub": {
        "stage_name": "IoT Hub",
        "description": "Receives device-to-cloud attendance events from employee clients via AMQP.",
        "pipeline_position": 2,
    },
    "servicebus": {
        "stage_name": "Service Bus",
        "description": "Attendance event queue decoupling IoT ingestion from background processing.",
        "pipeline_position": 3,
    },
    "functionapp": {
        "stage_name": "Function App",
        "description": "IoTHubIngress and AttendanceProcessor functions consume events and write to the database.",
        "pipeline_position": 4,
    },
    "postgresql": {
        "stage_name": "PostgreSQL Database",
        "description": "Persistent store for employee records, attendance events, and audit logs.",
        "pipeline_position": 5,
    },
    "redis": {
        "stage_name": "Redis Cache",
        "description": "Caches session tokens and tenant connection metadata for fast lookups.",
        "pipeline_position": 6,
    },
}


def _stage_message(status: str, latency_ms: float | None, error: str | None) -> str:
    if status == "healthy":
        if latency_ms is not None:
            return f"Healthy — {latency_ms:.0f}ms"
        return "Healthy"
    if status == "degraded":
        return f"Degraded — {error or 'partial connectivity'}"
    if status == "unhealthy":
        return f"Unhealthy — {error or 'not reachable'}"
    if status == "not_configured":
        return "Not configured for this environment"
    return f"Unknown — {error or 'probe failed'}"


def _build_pipeline_stage(stage_id: str, probe_result: dict[str, Any]) -> dict[str, Any]:
    meta = _PIPELINE_STAGE_META.get(stage_id, {"stage_name": stage_id, "description": "", "pipeline_position": 99})
    status = probe_result.get("status", "unknown")
    latency_ms = probe_result.get("latency_ms")
    error = probe_result.get("error")
    message = _stage_message(status, latency_ms, error)

    fix: dict[str, Any] | None = None
    issue_title = ""
    severity_class = "low"

    if status in ("unhealthy", "degraded"):
        if stage_id == "postgresql":
            postgres_state = probe_result.get("postgres_state", "")
            fix_type = "start_postgres_server" if postgres_state == "stopped" else "restart_postgres_server"
            fix = build_fix_metadata(
                fix_type,
                reason="Database is unreachable — attendance writes and employee auth will fail until the server is recovered.",
                risk="medium",
            )
            issue_title = "Database is unreachable — attendance pipeline is broken"
            severity_class = "high"
        elif stage_id == "functionapp":
            fix = build_fix_metadata(
                "restart_function_app",
                reason="The Function App processes attendance events. If stopped, events will pile up in Service Bus unprocessed.",
                risk="medium",
            )
            issue_title = "Function App is not running — attendance events are not being processed"
            severity_class = "high"
        elif stage_id == "webapp":
            fix = build_fix_metadata(
                "restart_webapp",
                reason="The web app is in a non-running state. Employees cannot log in or submit attendance events.",
                risk="low",
            )
            issue_title = "Web app is not running — employee access is unavailable"
            severity_class = "high"
        elif stage_id == "servicebus":
            fix = build_fix_metadata(
                "restart_function_app",
                reason="Service Bus is degraded — restarting the Function App reconnects its consumers.",
                risk="medium",
            )
            issue_title = "Service Bus is degraded — attendance event processing may be stalled"
            severity_class = "medium"
        elif stage_id == "iothub":
            fix = build_fix_metadata(
                "restart_function_app",
                reason="IoT Hub ingestion is failing — restarting the Function App re-establishes the Event Hub consumer.",
                risk="medium",
            )
            issue_title = "IoT Hub is degraded — punch-in/out events may not be received"
            severity_class = "medium"
        elif stage_id == "redis":
            fix = build_fix_metadata(
                "restart_webapp",
                reason="Redis is unavailable — session cache misses will cause auth slowdowns. Restarting the web app reconnects the client.",
                risk="low",
            )
            issue_title = "Redis cache is down — auth and caching degraded"
            severity_class = "low"

    return {
        "stage_id": stage_id,
        "stage_name": meta["stage_name"],
        "description": meta["description"],
        "pipeline_position": meta["pipeline_position"],
        "status": status,
        "latency_ms": latency_ms,
        "message": message,
        "issue_title": issue_title,
        "severity_class": severity_class,
        "fix": fix,
    }


def _run_az_probe(args: list[str]) -> dict | list:
    """Run an az CLI command without triggering ensure_azure_cli_login (called once before probes start)."""
    try:
        completed = subprocess.run(
            ["az", *args, "-o", "json", "--only-show-errors"],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("az CLI probe timed out after 20s") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("Azure CLI is not installed") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip() or "Unknown Azure CLI error"
        raise RuntimeError(detail) from exc
    raw = (completed.stdout or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"az CLI returned non-JSON: {raw}") from exc


def _probe_webapp_raw() -> dict[str, Any]:
    if not WEBAPP_NAME or not AZURE_RESOURCE_GROUP:
        return {"status": "not_configured"}
    started = time.perf_counter()
    try:
        result = _run_az_probe([
            "webapp", "show",
            "--resource-group", AZURE_RESOURCE_GROUP,
            "--name", WEBAPP_NAME,
            "--query", "{state:state}",
        ])
        state = sanitize_text((result or {}).get("state", "")).lower()
        ms = round((time.perf_counter() - started) * 1000, 2)
        if state == "running":
            return {"status": "healthy", "latency_ms": ms}
        return {"status": "unhealthy", "latency_ms": ms, "error": f"App state: {state or 'unknown'}"}
    except Exception as exc:
        return {"status": "unknown", "error": str(exc)}


def _probe_iothub_raw() -> dict[str, Any]:
    if not IOTHUB_NAME or not AZURE_RESOURCE_GROUP:
        return {"status": "not_configured"}
    started = time.perf_counter()
    try:
        result = _run_az_probe([
            "iot", "hub", "show",
            "--resource-group", AZURE_RESOURCE_GROUP,
            "--name", IOTHUB_NAME,
            "--query", "{state:properties.state}",
        ])
        state = sanitize_text((result or {}).get("state", "")).lower()
        ms = round((time.perf_counter() - started) * 1000, 2)
        if state == "active":
            return {"status": "healthy", "latency_ms": ms}
        return {"status": "degraded", "latency_ms": ms, "error": f"Hub state: {state or 'unknown'}"}
    except Exception as exc:
        return {"status": "unknown", "error": str(exc)}


def _probe_servicebus_raw() -> dict[str, Any]:
    if not SERVICEBUS_NAMESPACE_NAME or not AZURE_RESOURCE_GROUP:
        return {"status": "not_configured"}
    started = time.perf_counter()
    try:
        result = _run_az_probe([
            "servicebus", "namespace", "show",
            "--resource-group", AZURE_RESOURCE_GROUP,
            "--name", SERVICEBUS_NAMESPACE_NAME,
            "--query", "{status:status}",
        ])
        sbs_status = sanitize_text((result or {}).get("status", "")).lower()
        ms = round((time.perf_counter() - started) * 1000, 2)
        if sbs_status == "active":
            return {"status": "healthy", "latency_ms": ms}
        return {"status": "degraded", "latency_ms": ms, "error": f"Namespace status: {sbs_status or 'unknown'}"}
    except Exception as exc:
        return {"status": "unknown", "error": str(exc)}


def _probe_functionapp_raw() -> dict[str, Any]:
    if not FUNCTION_APP_NAME or not AZURE_RESOURCE_GROUP:
        return {"status": "not_configured"}
    started = time.perf_counter()
    try:
        result = _run_az_probe([
            "functionapp", "show",
            "--resource-group", AZURE_RESOURCE_GROUP,
            "--name", FUNCTION_APP_NAME,
            "--query", "{state:state}",
        ])
        state = sanitize_text((result or {}).get("state", "")).lower()
        ms = round((time.perf_counter() - started) * 1000, 2)
        if state == "running":
            return {"status": "healthy", "latency_ms": ms}
        return {"status": "unhealthy", "latency_ms": ms, "error": f"App state: {state or 'unknown'}"}
    except Exception as exc:
        return {"status": "unknown", "error": str(exc)}


def _probe_postgresql_raw() -> dict[str, Any]:
    result = check_database_health()
    postgres_state = ""
    if result["status"] == "unhealthy" and AZURE_POSTGRES_SERVER_NAME:
        try:
            server_info = get_cached_postgres_platform_action("show-server")
            postgres_state = str((server_info or {}).get("state") or "").lower()
        except Exception:
            pass
    result["postgres_state"] = postgres_state
    return result


def _probe_redis_raw() -> dict[str, Any]:
    return check_redis_health()


def load_pipeline_stages() -> list[dict[str, Any]]:
    """Concurrently probe all pipeline stages and return structured health objects."""
    # Login once before spawning threads — concurrent az CLI calls share the same
    # token cache files and will collide if each probe triggers its own login check.
    ensure_azure_cli_login()

    probe_map: dict[str, Any] = {
        "webapp": _probe_webapp_raw,
        "iothub": _probe_iothub_raw,
        "servicebus": _probe_servicebus_raw,
        "functionapp": _probe_functionapp_raw,
        "postgresql": _probe_postgresql_raw,
        "redis": _probe_redis_raw,
    }

    raw_results: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(probe_map)) as executor:
        future_to_id = {executor.submit(fn): sid for sid, fn in probe_map.items()}
        done, _ = concurrent.futures.wait(future_to_id, timeout=25)
        for future in future_to_id:
            sid = future_to_id[future]
            if future in done:
                try:
                    raw_results[sid] = future.result()
                except Exception as exc:
                    raw_results[sid] = {"status": "unknown", "error": str(exc)}
            else:
                raw_results[sid] = {"status": "unknown", "error": "probe timed out"}

    stages = [
        _build_pipeline_stage(sid, raw_results.get(sid, {"status": "unknown", "error": "not probed"}))
        for sid in probe_map
    ]
    stages.sort(key=lambda s: s["pipeline_position"])
    return stages
