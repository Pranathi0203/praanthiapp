import contextvars
import hashlib
import json
import logging
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from urllib.parse import urlparse, urlunparse

DD_TRACE_ENABLED = os.getenv("DD_TRACE_ENABLED", "false").lower() == "true"

if DD_TRACE_ENABLED:
    try:
        import ddtrace.auto  # noqa: F401
        from ddtrace import tracer as dd_tracer
    except ImportError:  # pragma: no cover - optional until Datadog is configured
        dd_tracer = None
else:  # pragma: no cover - exercised via env var in deployed environments
    dd_tracer = None

import psycopg
from azure.monitor.opentelemetry import configure_azure_monitor
from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.trace.status import Status, StatusCode
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

try:
    import redis
except ImportError:  # pragma: no cover - optional until infrastructure is configured
    redis = None


try:
    from applicationinsights import TelemetryClient as _TelemetryClient
except ImportError:  # pragma: no cover - optional until applicationinsights is installed
    _TelemetryClient = None

try:
    from azure.iot.device import IoTHubDeviceClient
except ImportError:  # pragma: no cover - optional until infrastructure is configured
    IoTHubDeviceClient = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.getenv("DATABASE_URL", "")
APP_SECRET = os.getenv("APP_SECRET", "change-me")
APPINSIGHTS_CONNECTION_STRING = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
APPLICATION_INSIGHTS_NAME = os.getenv("APPLICATION_INSIGHTS_NAME", os.getenv("APP_INSIGHTS_NAME", ""))
LOG_ANALYTICS_WORKSPACE_ID = os.getenv("LOG_ANALYTICS_WORKSPACE_ID", "")
LOG_ANALYTICS_WORKSPACE_RESOURCE_ID = os.getenv("LOG_ANALYTICS_WORKSPACE_RESOURCE_ID", "")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
ERROR_ANALYSIS_USE_AZURE_OPENAI = os.getenv("ERROR_ANALYSIS_USE_AZURE_OPENAI", "true").lower() == "true"
ERROR_ANALYSIS_TIMEOUT_SECONDS = int(os.getenv("ERROR_ANALYSIS_TIMEOUT_SECONDS", "45"))
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@pranathi.local").lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "change-me-admin")
CONTOSO_DOMAIN = os.getenv("CONTOSO_DOMAIN", "contoso.com").lower()
LITWARE_DOMAIN = os.getenv("LITWARE_DOMAIN", "litware.com").lower()
CONTOSO_DB_NAME = os.getenv("CONTOSO_DB_NAME", "contoso_db")
LITWARE_DB_NAME = os.getenv("LITWARE_DB_NAME", "litware_db")
CONTOSO_DATABASE_URL = os.getenv("CONTOSO_DATABASE_URL", "")
LITWARE_DATABASE_URL = os.getenv("LITWARE_DATABASE_URL", "")
REDIS_URL = os.getenv("REDIS_URL", "")
TENANT_CONNECTION_CACHE_TTL_SECONDS = int(os.getenv("TENANT_CONNECTION_CACHE_TTL_SECONDS", "3600"))
CONTOSO_DEVICE_CONNECTION_STRING = os.getenv("CONTOSO_DEVICE_CONNECTION_STRING", "")
LITWARE_DEVICE_CONNECTION_STRING = os.getenv("LITWARE_DEVICE_CONNECTION_STRING", "")
SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "true").lower() == "true"
GITHUB_API_URL = os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "Pranathi0203/praanthiapp")
GITHUB_DASHBOARD_TOKEN = os.getenv("GITHUB_DASHBOARD_TOKEN", "")
GITHUB_DEPLOY_QA_DEFAULT = os.getenv("GITHUB_DEPLOY_QA_DEFAULT", "false").lower() == "true"
GITHUB_CI_WORKFLOW = os.getenv("GITHUB_CI_WORKFLOW", "ci-build.yml")
GITHUB_RELEASE_WORKFLOW = os.getenv("GITHUB_RELEASE_WORKFLOW", "release-pipeline.yml")
POSTGRES_ADMIN_URL = os.getenv("POSTGRES_ADMIN_URL", "")
POSTGRES_ADMIN_DB = os.getenv("POSTGRES_ADMIN_DB", "postgres")
AZURE_SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID", "")
AZURE_RESOURCE_GROUP = os.getenv("AZURE_RESOURCE_GROUP", "")
AZURE_POSTGRES_SERVER_NAME = os.getenv("AZURE_POSTGRES_SERVER_NAME", "")
WEBAPP_NAME = os.getenv("WEBAPP_NAME", "")
FUNCTION_APP_NAME = os.getenv("FUNCTION_APP_NAME", "")
IOTHUB_NAME = os.getenv("IOTHUB_NAME", "")
SERVICEBUS_NAMESPACE_NAME = os.getenv("SERVICEBUS_NAMESPACE_NAME", "")
AZURE_USE_MANAGED_IDENTITY = os.getenv("AZURE_USE_MANAGED_IDENTITY", "true").lower() == "true"
ALLOW_WEBAPP_RESTART = os.getenv("ALLOW_WEBAPP_RESTART", "true").lower() == "true"
ALLOW_FUNCTIONAPP_RESTART = os.getenv("ALLOW_FUNCTIONAPP_RESTART", "true").lower() == "true"
ALLOW_POSTGRES_RESTART = os.getenv("ALLOW_POSTGRES_RESTART", "true").lower() == "true"
POWERSHELL_EXECUTABLE = os.getenv("POWERSHELL_EXECUTABLE", "pwsh")
POSTGRES_PLATFORM_ACTION_TIMEOUT_SECONDS = int(
    os.getenv("POSTGRES_PLATFORM_ACTION_TIMEOUT_SECONDS", "1800")
)
DATADOG_SERVICE = os.getenv("DD_SERVICE", "pranathi-app")
DATADOG_ENV = os.getenv("DD_ENV", os.getenv("ENV", "local"))
DATADOG_VERSION = os.getenv("DD_VERSION", "")

app = FastAPI(title="Pranathi App")
app.add_middleware(
    SessionMiddleware,
    secret_key=APP_SECRET,
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY,
)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
logger = logging.getLogger(__name__)
POSTGRES_ADMIN_SCRIPT_CANDIDATES = [
    os.path.join(BASE_DIR, "scripts", "postgres_admin.ps1"),
    os.path.join(os.path.dirname(BASE_DIR), "scripts", "postgres_admin.ps1"),
]

_redis_client = None
_tenant_iot_clients = {}
_mobile_token_serializer = URLSafeTimedSerializer(APP_SECRET, salt="employee-mobile-auth")
_request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
_postgres_platform_cache: dict[str, tuple[float, dict | list]] = {}
POSTGRES_PLATFORM_CACHE_TTL_SECONDS = int(os.getenv("POSTGRES_PLATFORM_CACHE_TTL_SECONDS", "30"))


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


_ai_client: Any = None


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


def setup_telemetry():
    if DD_TRACE_ENABLED:
        if dd_tracer is None:
            logger.warning("DD_TRACE_ENABLED is true but ddtrace is not installed.")
            return
        logger.info(
            "Datadog tracing enabled for service=%s env=%s version=%s",
            DATADOG_SERVICE,
            DATADOG_ENV,
            DATADOG_VERSION or "unset",
        )
        return

    if not APPINSIGHTS_CONNECTION_STRING:
        logger.warning("Application Insights connection string not set.")
        return

    configure_azure_monitor(
        connection_string=APPINSIGHTS_CONNECTION_STRING,
        logging_level=logging.INFO,
    )
    FastAPIInstrumentor.instrument_app(app)


logging.basicConfig(level=logging.INFO)
setup_telemetry()


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
    request: Request,
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


def with_database_name(base_url: str, database_name: str) -> str:
    parsed = urlparse(base_url)
    return urlunparse(parsed._replace(path=f"/{database_name}"))


def get_org_for_email(email: str):
    normalized = email.lower().strip()
    if "@" not in normalized:
        return None
    domain = normalized.split("@", 1)[1]
    if domain == CONTOSO_DOMAIN:
        return "contoso"
    if domain == LITWARE_DOMAIN:
        return "litware"
    return None


def get_db_url_for_org(org: str) -> str:
    if org == "contoso":
        if CONTOSO_DATABASE_URL:
            return CONTOSO_DATABASE_URL
        if DATABASE_URL:
            return with_database_name(DATABASE_URL, CONTOSO_DB_NAME)
    if org == "litware":
        if LITWARE_DATABASE_URL:
            return LITWARE_DATABASE_URL
        if DATABASE_URL:
            return with_database_name(DATABASE_URL, LITWARE_DB_NAME)
    raise RuntimeError(f"Database URL is not configured for org: {org}")


def get_admin_db_url() -> str:
    if DATABASE_URL:
        return DATABASE_URL
    raise RuntimeError("Shared admin database URL is not configured")


def get_postgres_admin_url() -> str:
    if POSTGRES_ADMIN_URL:
        return POSTGRES_ADMIN_URL
    if DATABASE_URL:
        return with_database_name(DATABASE_URL, POSTGRES_ADMIN_DB)
    raise RuntimeError("PostgreSQL admin connection is not configured")


def get_conn(db_url: str, *, autocommit: bool = False):
    return psycopg.connect(db_url, autocommit=autocommit)


def is_postgres_platform_configured() -> bool:
    return bool(AZURE_SUBSCRIPTION_ID and AZURE_RESOURCE_GROUP and AZURE_POSTGRES_SERVER_NAME)


def get_postgres_admin_script_path() -> str:
    for candidate in POSTGRES_ADMIN_SCRIPT_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return POSTGRES_ADMIN_SCRIPT_CANDIDATES[0]


def ensure_azure_cli_login():
    show = subprocess.run(
        ["az", "account", "show", "-o", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if show.returncode != 0:
        if not AZURE_USE_MANAGED_IDENTITY:
            raise RuntimeError("Azure CLI is not logged in and managed identity is disabled.")
        subprocess.run(
            ["az", "login", "--identity", "--output", "none", "--only-show-errors"],
            capture_output=True,
            text=True,
            check=True,
        )

    if AZURE_SUBSCRIPTION_ID:
        subprocess.run(
            ["az", "account", "set", "--subscription", AZURE_SUBSCRIPTION_ID, "--only-show-errors"],
            capture_output=True,
            text=True,
            check=True,
        )


def run_azure_cli_json(args: list[str]) -> dict | list:
    try:
        ensure_azure_cli_login()
        completed = subprocess.run(
            ["az", *args, "-o", "json", "--only-show-errors"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Azure CLI is not installed in the web app container.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or "Unknown Azure CLI error."
        raise RuntimeError(detail) from exc

    raw = (completed.stdout or "").strip()
    if not raw:
        return {}

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Azure CLI returned non-JSON output: {raw}") from exc


def run_azure_cli_access_token(resource: str) -> str:
    payload = run_azure_cli_json(["account", "get-access-token", "--resource", resource])
    token = str((payload or {}).get("accessToken", "")).strip()
    if not token:
        raise RuntimeError(f"Azure CLI did not return an access token for resource {resource}.")
    return token


def arm_get(resource_id: str, api_version: str) -> dict[str, Any]:
    token = run_azure_cli_access_token("https://management.azure.com/")
    url = "https://management.azure.com{}?api-version={}".format(
        resource_id,
        urllib_parse.quote(api_version, safe=""),
    )
    req = urllib_request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib_request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"ARM request failed for {resource_id}: HTTP {exc.code} {body}") from exc


def post_json_with_token(url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    return post_json_with_headers(
        url,
        {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        payload,
    )


def post_json_with_headers(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int = 60
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Log query failed: HTTP {exc.code} {detail}") from exc


def is_error_operations_configured() -> bool:
    has_workspace = bool(LOG_ANALYTICS_WORKSPACE_ID)
    has_app_insights_lookup = bool(AZURE_SUBSCRIPTION_ID and AZURE_RESOURCE_GROUP and APPLICATION_INSIGHTS_NAME)
    return has_workspace or has_app_insights_lookup


def is_ai_error_analysis_configured() -> bool:
    return bool(
        ERROR_ANALYSIS_USE_AZURE_OPENAI
        and AZURE_OPENAI_ENDPOINT
        and AZURE_OPENAI_API_KEY
        and AZURE_OPENAI_DEPLOYMENT
    )


def get_workspace_context() -> dict[str, str]:
    if LOG_ANALYTICS_WORKSPACE_ID:
        return {
            "workspace_id": LOG_ANALYTICS_WORKSPACE_ID,
            "workspace_resource_id": LOG_ANALYTICS_WORKSPACE_RESOURCE_ID,
            "app_insights_name": APPLICATION_INSIGHTS_NAME,
            "resource_group": AZURE_RESOURCE_GROUP,
            "subscription_id": AZURE_SUBSCRIPTION_ID,
        }

    if not (AZURE_SUBSCRIPTION_ID and AZURE_RESOURCE_GROUP and APPLICATION_INSIGHTS_NAME):
        raise RuntimeError(
            "Set LOG_ANALYTICS_WORKSPACE_ID or configure AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, and APPLICATION_INSIGHTS_NAME."
        )

    component_resource_id = (
        f"/subscriptions/{AZURE_SUBSCRIPTION_ID}/resourceGroups/{AZURE_RESOURCE_GROUP}/"
        f"providers/Microsoft.Insights/components/{APPLICATION_INSIGHTS_NAME}"
    )
    component = arm_get(component_resource_id, "2020-02-02")
    properties = component.get("properties", {})
    workspace_resource_id = (
        properties.get("WorkspaceResourceId")
        or properties.get("workspaceResourceId")
        or LOG_ANALYTICS_WORKSPACE_RESOURCE_ID
    )
    if not workspace_resource_id:
        raise RuntimeError("Application Insights is not linked to a Log Analytics workspace.")

    workspace = arm_get(str(workspace_resource_id), "2022-10-01")
    customer_id = (
        workspace.get("properties", {}).get("customerId")
        or workspace.get("properties", {}).get("customerID")
        or LOG_ANALYTICS_WORKSPACE_ID
    )
    if not customer_id:
        raise RuntimeError("Could not resolve the Log Analytics workspace customer id.")

    return {
        "workspace_id": str(customer_id),
        "workspace_resource_id": str(workspace_resource_id),
        "app_insights_name": APPLICATION_INSIGHTS_NAME,
        "resource_group": AZURE_RESOURCE_GROUP,
        "subscription_id": AZURE_SUBSCRIPTION_ID,
    }


def build_error_union_query(minutes: int) -> str:
    window = max(1, minutes)
    return """
union isfuzzy=true
(
    AppExceptions
    | where TimeGenerated >= ago({window}m)
    | extend Details=tostring(column_ifexists("Details", "")), ParsedDetails=tostring(parse_json(column_ifexists("Details", "{{}}"))[0].rawStack)
    | project TimeGenerated, SourceTable="AppExceptions", Severity="Error", ErrorType=coalesce(Type, InnermostType, ProblemId), ErrorKey=coalesce(ProblemId, Type, InnermostType, OuterMessage), Message=coalesce(OuterMessage, InnermostMessage, Message), Details=coalesce(ParsedDetails, Details), OperationId=OperationId, ResourceId=_ResourceId
),
(
    AppTraces
    | where TimeGenerated >= ago({window}m)
    | where SeverityLevel >= 3 or Message has_any ("error", "exception", "failed", "timeout", "deadlock", "forbidden", "unauthorized")
    | extend Details=tostring(column_ifexists("customDimensions", dynamic({{}})).detail)
    | project TimeGenerated, SourceTable="AppTraces", Severity=tostring(SeverityLevel), ErrorType=tostring(column_ifexists("customDimensions", dynamic({{}})).event), ErrorKey=coalesce(tostring(column_ifexists("customDimensions", dynamic({{}})).event), Message), Message=Message, Details=Details, OperationId=OperationId, ResourceId=_ResourceId
),
(
    FunctionAppLogs
    | where TimeGenerated >= ago({window}m)
    | where tostring(column_ifexists("Level", "")) =~ "Error" or Message has_any ("error", "exception", "failed", "timeout", "deadlock")
    | extend Details=tostring(column_ifexists("ExceptionDetails", ""))
    | project TimeGenerated, SourceTable="FunctionAppLogs", Severity=tostring(column_ifexists("Level", "")), ErrorType=tostring(column_ifexists("Level", "")), ErrorKey=coalesce(Message, tostring(column_ifexists("Level", ""))), Message=Message, Details=Details, OperationId=tostring(column_ifexists("OperationId", "")), ResourceId=_ResourceId
),
(
    AzureDiagnostics
    | where TimeGenerated >= ago({window}m)
    | where tostring(column_ifexists("Message", "")) has_any ("error", "exception", "failed", "timeout", "deadlock", "unauthorized", "forbidden")
        or tostring(column_ifexists("Level", "")) =~ "Error"
        or tostring(column_ifexists("status_s", "")) =~ "Failed"
        or tostring(column_ifexists("ResultType", "")) =~ "Failed"
    | project TimeGenerated, SourceTable="AzureDiagnostics", Severity=tostring(column_ifexists("Level", "")), ErrorType=tostring(column_ifexists("Category", "")), ErrorKey=coalesce(tostring(column_ifexists("Category", "")), tostring(column_ifexists("Message", ""))), Message=tostring(column_ifexists("Message", "")), Details=tostring(column_ifexists("ResultDescription", "")), OperationId=tostring(column_ifexists("OperationId", "")), ResourceId=_ResourceId
),
(
    PGSQLServerLogs
    | where TimeGenerated >= ago({window}m)
    | where Message has_any ("error", "exception", "failed", "timeout", "deadlock", "too many connections", "lock wait", "could not connect", "connection refused")
    | project TimeGenerated, SourceTable="PGSQLServerLogs", Severity="Error", ErrorType="PGSQLServerLogs", ErrorKey=Message, Message=Message, Details="", OperationId="", ResourceId=_ResourceId
)
""".format(window=window)


def query_log_analytics(workspace_id: str, query: str) -> list[dict[str, Any]]:
    token = run_azure_cli_access_token("https://api.loganalytics.io")
    url = f"https://api.loganalytics.io/v1/workspaces/{workspace_id}/query"
    response = post_json_with_token(url, token, {"query": query})
    tables = response.get("tables", [])
    if not tables:
        return []

    columns = [col.get("name") for col in tables[0].get("columns", [])]
    rows = tables[0].get("rows", [])
    return [{columns[index]: row[index] for index in range(min(len(columns), len(row)))} for row in rows]


def resource_name_from_id(resource_id: str) -> str:
    cleaned = (resource_id or "").strip("/")
    if not cleaned:
        return "Unknown resource"
    return cleaned.split("/")[-1]


def severity_rank(label: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(label, 4)


def infer_error_severity(source_table: str, message: str, count: int) -> str:
    text = f"{source_table} {message}".lower()
    if "could not connect" in text or "connection refused" in text or "connection timeout expired" in text:
        return "critical"
    if "service bus" in text or "iothub" in text or "event hub" in text:
        return "high"
    if "unauthorized" in text or "forbidden" in text or "deadlock" in text:
        return "high"
    if count >= 10:
        return "high"
    if count >= 3:
        return "medium"
    return "low"


def sanitize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def dedupe_resources(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for resource in resources:
        normalized = {
            "name": sanitize_text(resource.get("name")),
            "kind": sanitize_text(resource.get("kind")),
            "id": sanitize_text(resource.get("id")),
        }
        key = (normalized["name"].lower(), normalized["kind"].lower(), normalized["id"].lower())
        if not normalized["name"] or key in seen:
            continue
        seen.add(key)
        unique.append(normalized)
    return unique


def normalize_repo_path(file_path: str) -> str:
    cleaned = sanitize_text(file_path)
    if cleaned.startswith("/app/"):
        return f"src/{cleaned.removeprefix('/app/')}"
    return cleaned


def extract_code_location(details: str) -> tuple[str, str]:
    detail_text = sanitize_text(details)
    if not detail_text:
        return "", ""

    file_match = re.search(r'File "([^"]+)", line (\d+)', detail_text)
    if file_match:
        return normalize_repo_path(file_match.group(1)), file_match.group(2)

    path_match = re.search(r'([A-Za-z0-9_./-]+\.py):(\d+)', detail_text)
    if path_match:
        return normalize_repo_path(path_match.group(1)), path_match.group(2)

    return "", ""


def json_dumps_compact(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def parse_json_response_text(content: Any) -> Any:
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict):
                text_parts.append(str(item.get("text", "")))
            else:
                text_parts.append(str(item))
        text = "".join(text_parts).strip()
    else:
        text = str(content or "").strip()

    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    return json.loads(text)


def build_fix_metadata(fix_type: str, *, reason: str = "", risk: str = "medium") -> dict[str, Any]:
    if fix_type == "start_postgres_server":
        return {
            "fix_type": fix_type,
            "action_label": "Start PostgreSQL server",
            "component": "Azure PostgreSQL Flexible Server",
            "component_group": "postgresql",
            "resource_name": AZURE_POSTGRES_SERVER_NAME or "PostgreSQL server",
            "resource_kind": "postgresql",
            "resource_id": AZURE_POSTGRES_SERVER_NAME,
            "is_available": bool(AZURE_POSTGRES_SERVER_NAME and ALLOW_POSTGRES_RESTART),
            "risk": risk,
            "reason": reason or "Start the PostgreSQL server to restore database availability.",
            "resources": dedupe_resources(
                [
                    {
                        "name": AZURE_POSTGRES_SERVER_NAME or "PostgreSQL server",
                        "kind": "postgresql",
                        "id": AZURE_POSTGRES_SERVER_NAME,
                    }
                ]
            ),
        }
    if fix_type == "restart_postgres_server":
        return {
            "fix_type": fix_type,
            "action_label": "Restart PostgreSQL server",
            "component": "Azure PostgreSQL Flexible Server",
            "component_group": "postgresql",
            "resource_name": AZURE_POSTGRES_SERVER_NAME or "PostgreSQL server",
            "resource_kind": "postgresql",
            "resource_id": AZURE_POSTGRES_SERVER_NAME,
            "is_available": bool(AZURE_POSTGRES_SERVER_NAME and ALLOW_POSTGRES_RESTART),
            "risk": risk,
            "reason": reason or "Restart the PostgreSQL server to recover database availability.",
            "resources": dedupe_resources(
                [
                    {
                        "name": AZURE_POSTGRES_SERVER_NAME or "PostgreSQL server",
                        "kind": "postgresql",
                        "id": AZURE_POSTGRES_SERVER_NAME,
                    }
                ]
            ),
        }
    if fix_type == "restart_function_app":
        return {
            "fix_type": fix_type,
            "action_label": "Restart Function App",
            "component": "Azure Function App",
            "component_group": "functionapp",
            "resource_name": FUNCTION_APP_NAME or "Function App",
            "resource_kind": "functionapp",
            "resource_id": FUNCTION_APP_NAME,
            "is_available": bool(FUNCTION_APP_NAME and ALLOW_FUNCTIONAPP_RESTART),
            "risk": risk,
            "reason": reason or "Restart the Function App to recover the event-driven pipeline.",
            "resources": dedupe_resources(
                [
                    {
                        "name": FUNCTION_APP_NAME or "Function App",
                        "kind": "functionapp",
                        "id": FUNCTION_APP_NAME,
                    }
                ]
            ),
        }
    if fix_type == "restart_webapp":
        return {
            "fix_type": fix_type,
            "action_label": "Restart Web App",
            "component": "Azure App Service Web App",
            "component_group": "webapp",
            "resource_name": WEBAPP_NAME or "Web App",
            "resource_kind": "webapp",
            "resource_id": WEBAPP_NAME,
            "is_available": bool(WEBAPP_NAME and ALLOW_WEBAPP_RESTART),
            "risk": risk,
            "reason": reason or "Restart the web app to recover the application runtime.",
            "resources": dedupe_resources(
                [
                    {
                        "name": WEBAPP_NAME or "Web App",
                        "kind": "webapp",
                        "id": WEBAPP_NAME,
                    }
                ]
            ),
        }
    return {
        "fix_type": "manual_investigation",
        "action_label": "Manual investigation",
        "component": "Unknown component",
        "component_group": "unknown",
        "resource_name": resource_name_from_id(""),
        "resource_kind": "unknown",
        "resource_id": "",
        "is_available": False,
        "risk": "unknown",
        "reason": reason or "No safe automated fix is available for this error signature yet.",
        "resources": [],
    }


def build_fix_catalog() -> list[dict[str, Any]]:
    return [
        {
            "fix_type": "start_postgres_server",
            "label": "Start PostgreSQL server",
            "available": bool(AZURE_POSTGRES_SERVER_NAME and ALLOW_POSTGRES_RESTART),
            "component": "Azure PostgreSQL Flexible Server",
            "resource_name": AZURE_POSTGRES_SERVER_NAME or "PostgreSQL server",
        },
        {
            "fix_type": "restart_postgres_server",
            "label": "Restart PostgreSQL server",
            "available": bool(AZURE_POSTGRES_SERVER_NAME and ALLOW_POSTGRES_RESTART),
            "component": "Azure PostgreSQL Flexible Server",
            "resource_name": AZURE_POSTGRES_SERVER_NAME or "PostgreSQL server",
        },
        {
            "fix_type": "restart_function_app",
            "label": "Restart Function App",
            "available": bool(FUNCTION_APP_NAME and ALLOW_FUNCTIONAPP_RESTART),
            "component": "Azure Function App",
            "resource_name": FUNCTION_APP_NAME or "Function App",
        },
        {
            "fix_type": "restart_webapp",
            "label": "Restart Web App",
            "available": bool(WEBAPP_NAME and ALLOW_WEBAPP_RESTART),
            "component": "Azure App Service Web App",
            "resource_name": WEBAPP_NAME or "Web App",
        },
        {
            "fix_type": "manual_investigation",
            "label": "Manual investigation",
            "available": True,
            "component": "Operator review",
            "resource_name": "n/a",
        },
    ]


def build_resource_catalog() -> list[dict[str, str]]:
    return dedupe_resources(
        [
            {"name": WEBAPP_NAME, "kind": "webapp", "id": WEBAPP_NAME},
            {"name": FUNCTION_APP_NAME, "kind": "functionapp", "id": FUNCTION_APP_NAME},
            {"name": AZURE_POSTGRES_SERVER_NAME, "kind": "postgresql", "id": AZURE_POSTGRES_SERVER_NAME},
            {"name": IOTHUB_NAME, "kind": "iothub", "id": IOTHUB_NAME},
            {"name": SERVICEBUS_NAMESPACE_NAME, "kind": "servicebus", "id": SERVICEBUS_NAMESPACE_NAME},
            {"name": APPLICATION_INSIGHTS_NAME, "kind": "telemetry", "id": APPLICATION_INSIGHTS_NAME},
        ]
    )


def call_azure_openai_json(system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
    if not is_ai_error_analysis_configured():
        raise RuntimeError(
            "Azure OpenAI error analysis is not configured. Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, and AZURE_OPENAI_DEPLOYMENT."
        )

    url = (
        f"{AZURE_OPENAI_ENDPOINT}/openai/deployments/{urllib_parse.quote(AZURE_OPENAI_DEPLOYMENT, safe='')}"
        f"/chat/completions?api-version={urllib_parse.quote(AZURE_OPENAI_API_VERSION, safe='')}"
    )
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json_dumps_compact(user_payload)},
        ],
        "temperature": 0.1,
        "max_completion_tokens": 3000,
    }
    response = post_json_with_headers(
        url,
        {"api-key": AZURE_OPENAI_API_KEY, "Content-Type": "application/json"},
        payload,
        timeout=ERROR_ANALYSIS_TIMEOUT_SECONDS,
    )
    choices = response.get("choices", [])
    if not choices:
        raise RuntimeError("Azure OpenAI returned no choices for admin error analysis.")
    message = (choices[0] or {}).get("message", {})
    return parse_json_response_text(message.get("content", ""))


def fallback_issue_analysis(row: dict[str, Any], fix: dict[str, Any]) -> dict[str, Any]:
    message = str(row.get("ExampleMessage", ""))
    details = str(row.get("ExampleDetails", ""))
    file_path, line_number = extract_code_location(details)
    count = int(row.get("Count", 0) or 0)
    severity = infer_error_severity(str(row.get("SourceTable", "")), message, count)
    affected_resources = list(fix.get("resources", []))
    example_resource_id = str(row.get("ExampleResourceId", "") or "")
    if example_resource_id:
        affected_resources.append(
            {
                "name": resource_name_from_id(example_resource_id),
                "kind": "telemetry",
                "id": example_resource_id,
            }
        )
    return {
        "title": describe_issue_title(str(row.get("SourceTable", "")), str(row.get("ErrorType", "")), message, fix),
        "summary": describe_issue_summary(str(row.get("SourceTable", "")), str(row.get("ErrorType", "")), message, fix),
        "severity_label": severity,
        "severity_score": {"critical": 9, "high": 8, "medium": 5, "low": 3}.get(severity, 5),
        "why_it_occurred": fix["reason"],
        "where_it_occurred": {
            "component": fix["component"],
            "file_path": file_path,
            "line_number": line_number,
            "resource": fix["resource_name"],
        },
        "recommended_fix_type": fix["fix_type"],
        "recommended_fix": fix["action_label"],
        "affected_resources": dedupe_resources(affected_resources),
        "source": "fallback_rules",
    }


def analyze_error_rows_with_ai(rows: list[dict[str, Any]], minutes: int) -> dict[str, dict[str, Any]]:
    issue_payload = []
    for index, row in enumerate(rows, start=1):
        issue_payload.append(
            {
                "issue_id": f"err-{index}",
                "source_table": sanitize_text(row.get("SourceTable")),
                "error_type": sanitize_text(row.get("ErrorType")),
                "error_key": sanitize_text(row.get("ErrorKey")),
                "occurrences": int(row.get("Count", 0) or 0),
                "first_seen": str(row.get("FirstSeen", "") or ""),
                "last_seen": str(row.get("LastSeen", "") or ""),
                "example_message": sanitize_text(row.get("ExampleMessage")),
                "example_details": sanitize_text(row.get("ExampleDetails")),
                "resource_id": sanitize_text(row.get("ExampleResourceId")),
                "operation_id": sanitize_text(row.get("ExampleOperationId")),
            }
        )

    system_prompt = (
        "You are diagnosing Azure application and platform failures for an admin operations dashboard. "
        "Analyze each grouped issue from Application Insights and Log Analytics. "
        "Return strict JSON with the shape {'issues': [...]} and no markdown. "
        "For each issue include: issue_id, title, summary, severity_label, severity_score, why_it_occurred, "
        "where_it_occurred, recommended_fix_type, recommended_fix, and affected_resources. "
        "Severity labels must be one of critical, high, medium, low. Severity score must be an integer from 1 to 10. "
        "where_it_occurred must be an object with component, file_path, line_number, and resource. "
        "Use file_path and line_number only when the telemetry clearly supports them; otherwise leave them empty. "
        "recommended_fix_type must be one of: start_postgres_server, restart_postgres_server, restart_function_app, restart_webapp, manual_investigation. "
        "Only recommend an automated fix when the evidence supports it. "
        "affected_resources must be a list of objects with name, kind, and id. "
        "Prefer the known Azure resources provided to you and mention telemetry resources only when they help explain impact."
    )
    response = call_azure_openai_json(
        system_prompt,
        {
            "window_minutes": minutes,
            "resources": build_resource_catalog(),
            "fix_catalog": build_fix_catalog(),
            "issues": issue_payload,
        },
    )
    issues = response.get("issues", [])
    return {
        sanitize_text(item.get("issue_id")): item
        for item in issues
        if isinstance(item, dict) and sanitize_text(item.get("issue_id"))
    }


def classify_error_fix(source_table: str, error_type: str, message: str) -> dict[str, Any]:
    haystack = " ".join([source_table, error_type, message]).lower()
    if any(
        signal in haystack
        for signal in [
            "pgsqlserverlogs",
            "postgres",
            "psycopg",
            "could not connect",
            "connection refused",
            "connection timeout expired",
            "database connection failed",
        ]
    ):
        postgres_status = ""
        try:
            server_info = get_cached_postgres_platform_action("show-server")
            postgres_status = str((server_info or {}).get("state") or (server_info or {}).get("status") or "").lower()
        except Exception:
            postgres_status = ""

        fix_type = "start_postgres_server" if postgres_status == "stopped" else "restart_postgres_server"
        return build_fix_metadata(
            fix_type,
            reason="The database appears unavailable, so a PostgreSQL server recovery action is the safest bounded fix.",
        )

    if any(signal in haystack for signal in ["service bus", "servicebus", "queue", "attendance-events"]):
        fix = build_fix_metadata(
            "restart_function_app",
            reason="The failure appears in the queue-processing pipeline, so restarting the Function App is the safest bounded recovery step.",
        )
        fix["component"] = "Service Bus attendance pipeline"
        fix["component_group"] = "servicebus"
        fix["resource_name"] = SERVICEBUS_NAMESPACE_NAME or "Service Bus namespace"
        fix["resource_kind"] = "servicebus"
        fix["resource_id"] = SERVICEBUS_NAMESPACE_NAME
        fix["resources"] = dedupe_resources(
            fix["resources"]
            + [
                {
                    "name": SERVICEBUS_NAMESPACE_NAME or "Service Bus namespace",
                    "kind": "servicebus",
                    "id": SERVICEBUS_NAMESPACE_NAME,
                }
            ]
        )
        return fix

    if any(signal in haystack for signal in ["iothub", "event hub", "eventhub", "iot ingress"]):
        fix = build_fix_metadata(
            "restart_function_app",
            reason="The issue points to IoT ingestion or Event Hub forwarding, so restarting the Function App is the safest bounded recovery step.",
        )
        fix["component"] = "IoT Hub ingestion pipeline"
        fix["component_group"] = "iothub"
        fix["resource_name"] = IOTHUB_NAME or "IoT Hub"
        fix["resource_kind"] = "iothub"
        fix["resource_id"] = IOTHUB_NAME
        fix["resources"] = dedupe_resources(
            fix["resources"]
            + [
                {
                    "name": IOTHUB_NAME or "IoT Hub",
                    "kind": "iothub",
                    "id": IOTHUB_NAME,
                }
            ]
        )
        return fix

    if any(signal in haystack for signal in ["functionapplogs", "function host", "azure functions"]):
        return build_fix_metadata(
            "restart_function_app",
            reason="The failure points to the event-driven pipeline, so restarting the Function App is the least disruptive recovery step.",
        )

    if any(signal in haystack for signal in ["appexceptions", "apptraces", "uvicorn", "fastapi", "internal server error"]):
        return build_fix_metadata(
            "restart_webapp",
            reason="The failures appear to come from the web runtime, so restarting the web app is the safest operational reset.",
        )

    return build_fix_metadata("manual_investigation")


def describe_issue_title(source_table: str, error_type: str, message: str, fix: dict[str, Any]) -> str:
    text = " ".join([source_table, error_type, message]).lower()
    if fix["component_group"] == "postgresql":
        return "Database server is unavailable"
    if fix["component_group"] == "servicebus":
        return "Service Bus pipeline is failing"
    if fix["component_group"] == "iothub":
        return "IoT Hub ingestion is failing"
    if fix["component_group"] == "functionapp":
        return "Background attendance processing is failing"
    if "health_check_completed" in text:
        return "Health checks are failing"
    if "http_request_completed" in text:
        return "Requests are completing with server errors"
    if "application_exception" in text:
        return "Application exception detected"
    if error_type and error_type.lower() != source_table.lower():
        return error_type.replace("_", " ").strip().title()
    if message:
        return message[:72].rstrip(".")
    return "Unknown issue"


def describe_issue_summary(source_table: str, error_type: str, message: str, fix: dict[str, Any]) -> str:
    text = " ".join([source_table, error_type, message]).lower()
    if fix["component_group"] == "postgresql":
        return "The web app cannot reliably reach PostgreSQL, so health checks and requests may fail."
    if fix["component_group"] == "servicebus":
        return "Attendance events are failing in the queue-based processing path."
    if fix["component_group"] == "iothub":
        return "IoT device events are not flowing cleanly through the ingestion path."
    if fix["component_group"] == "functionapp":
        return "The background processing app is reporting runtime failures."
    if "health_check_completed" in text:
        return "The application health endpoint is reporting unhealthy checks."
    if "http_request_completed" in text:
        return "Recent requests are ending in error states and need review."
    if "application_exception" in text:
        return "An exception reached telemetry and should be reviewed with the related failure."
    return message or "No summary available."


def classify_issue_role(message: str) -> str:
    lowered = (message or "").lower().strip()
    if lowered in {"health_check_completed", "http_request_completed", "application_exception"}:
        return "symptom"
    return "root"


def build_issue_overview(issues: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for item in issues:
        counts[item["severity"]] = counts.get(item["severity"], 0) + 1

    primary_issue = issues[0] if issues else None
    if primary_issue:
        headline = primary_issue["title"]
        guidance = primary_issue["summary"]
    else:
        headline = "No major recurring issues found"
        guidance = "The recent scan did not return recurring telemetry failures."

    return {
        "counts": counts,
        "headline": headline,
        "guidance": guidance,
    }


def load_recent_error_operations(minutes: int = 60, limit: int = 12) -> list[dict[str, Any]]:
    workspace = get_workspace_context()
    query = """
{union_query}
| summarize Count=count(), FirstSeen=min(TimeGenerated), LastSeen=max(TimeGenerated), ExampleMessage=any(Message), ExampleResourceId=any(ResourceId), ExampleOperationId=any(OperationId) by SourceTable, ErrorType, ErrorKey
| join kind=leftouter (
    {union_query}
    | summarize ExampleDetails=any(Details) by SourceTable, ErrorType, ErrorKey
) on SourceTable, ErrorType, ErrorKey
| sort by Count desc, LastSeen desc
| take {limit}
""".format(union_query=build_error_union_query(minutes), limit=max(1, min(limit, 50)))

    rows = query_log_analytics(workspace["workspace_id"], query)
    ai_analysis_by_id: dict[str, dict[str, Any]] = {}
    if rows and is_ai_error_analysis_configured():
        try:
            ai_analysis_by_id = analyze_error_rows_with_ai(rows, minutes)
        except Exception as exc:
            logger.warning("Azure OpenAI error analysis failed, falling back to rules: %s", exc)
            record_exception_to_telemetry(exc, action="ai_error_analysis")

    items = []
    for index, row in enumerate(rows, start=1):
        issue_id = f"err-{index}"
        source_table = str(row.get("SourceTable", ""))
        error_type = str(row.get("ErrorType", ""))
        message = str(row.get("ExampleMessage", ""))
        count = int(row.get("Count", 0) or 0)
        fallback_fix = classify_error_fix(source_table, error_type, message)
        analysis = ai_analysis_by_id.get(issue_id, fallback_issue_analysis(row, fallback_fix))
        recommended_fix_type = sanitize_text(analysis.get("recommended_fix_type")) or fallback_fix["fix_type"]
        fix = build_fix_metadata(
            recommended_fix_type,
            reason=sanitize_text(analysis.get("why_it_occurred")) or fallback_fix["reason"],
            risk="medium",
        )

        title = sanitize_text(analysis.get("title")) or describe_issue_title(source_table, error_type, message, fix)
        summary = sanitize_text(analysis.get("summary")) or describe_issue_summary(source_table, error_type, message, fix)
        example_resource_id = str(row.get("ExampleResourceId", "") or "")
        ai_resources = analysis.get("affected_resources", [])
        affected_resources = [resource for resource in fix.get("resources", []) if resource.get("name")] + (
            ai_resources if isinstance(ai_resources, list) else []
        )
        if example_resource_id and all((resource.get("id") or "") != example_resource_id for resource in affected_resources):
            affected_resources.append(
                {
                    "name": resource_name_from_id(example_resource_id),
                    "kind": "telemetry",
                    "id": example_resource_id,
                }
            )
        affected_resources = dedupe_resources(affected_resources)

        severity = sanitize_text(analysis.get("severity_label")).lower() or infer_error_severity(source_table, message, count)
        if severity not in {"critical", "high", "medium", "low"}:
            severity = infer_error_severity(source_table, message, count)
        location = analysis.get("where_it_occurred", {}) if isinstance(analysis.get("where_it_occurred"), dict) else {}
        file_path = normalize_repo_path(str(location.get("file_path", "") or ""))
        line_number = sanitize_text(location.get("line_number", ""))
        if not file_path or not line_number:
            fallback_file, fallback_line = extract_code_location(str(row.get("ExampleDetails", "")))
            file_path = file_path or fallback_file
            line_number = line_number or fallback_line

        items.append(
            {
                "id": issue_id,
                "source_table": source_table,
                "error_type": error_type or "Unknown",
                "error_key": str(row.get("ErrorKey", "")),
                "count": count,
                "first_seen": row.get("FirstSeen", ""),
                "last_seen": row.get("LastSeen", ""),
                "example_message": message,
                "example_details": str(row.get("ExampleDetails", "") or ""),
                "title": title,
                "summary": summary,
                "role": classify_issue_role(message),
                "operation_id": row.get("ExampleOperationId", ""),
                "severity": severity,
                "severity_score": parse_int(
                    analysis.get("severity_score", {"critical": 9, "high": 8, "medium": 5, "low": 3}.get(severity, 5)),
                    {"critical": 9, "high": 8, "medium": 5, "low": 3}.get(severity, 5),
                ),
                "severity_rank": severity_rank(severity),
                "why_it_occurred": sanitize_text(analysis.get("why_it_occurred")) or fix["reason"],
                "where_it_occurred": {
                    "component": sanitize_text(location.get("component")) or fix["component"],
                    "file_path": file_path,
                    "line_number": line_number,
                    "resource": sanitize_text(location.get("resource")) or fix["resource_name"],
                },
                "fix": fix,
                "affected_resources": affected_resources,
                "analysis_source": sanitize_text(analysis.get("source")) or ("azure_openai" if issue_id in ai_analysis_by_id else "fallback_rules"),
            }
        )

    items.sort(
        key=lambda item: (
            item["severity_rank"],
            -int(item.get("severity_score", 0)),
            0 if item["role"] == "root" else 1,
            -item["count"],
        )
    )
    return items


def apply_error_fix(fix_type: str):
    if fix_type == "start_postgres_server":
        run_postgres_platform_action("start-server")
        return f"Start requested for {AZURE_POSTGRES_SERVER_NAME}."
    if fix_type == "restart_postgres_server":
        run_postgres_platform_action("restart-server")
        return f"Restart requested for {AZURE_POSTGRES_SERVER_NAME}."
    if fix_type == "restart_function_app":
        run_azure_cli_json(
            ["functionapp", "restart", "--resource-group", AZURE_RESOURCE_GROUP, "--name", FUNCTION_APP_NAME]
        )
        return f"Restart requested for {FUNCTION_APP_NAME}."
    if fix_type == "restart_webapp":
        run_azure_cli_json(["webapp", "restart", "--resource-group", AZURE_RESOURCE_GROUP, "--name", WEBAPP_NAME])
        return f"Restart requested for {WEBAPP_NAME}."
    raise RuntimeError(f"Unsupported fix type: {fix_type}")


def run_postgres_platform_action(action: str, **kwargs: str) -> dict | list:
    if not is_postgres_platform_configured():
        raise RuntimeError("Azure PostgreSQL platform actions are not configured.")
    script_path = get_postgres_admin_script_path()
    if not os.path.exists(script_path):
        raise RuntimeError(f"PostgreSQL admin script not found at {script_path}.")

    command = [
        POWERSHELL_EXECUTABLE,
        "-NoLogo",
        "-NoProfile",
        "-File",
        script_path,
        "-Action",
        action,
        "-SubscriptionId",
        AZURE_SUBSCRIPTION_ID,
        "-ResourceGroup",
        AZURE_RESOURCE_GROUP,
        "-ServerName",
        AZURE_POSTGRES_SERVER_NAME,
        "-UseManagedIdentity",
        "$true" if AZURE_USE_MANAGED_IDENTITY else "$false",
    ]

    for key, value in kwargs.items():
        if value is None or value == "":
            continue
        command.extend([f"-{key}", str(value)])

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=POSTGRES_PLATFORM_ACTION_TIMEOUT_SECONDS,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"{POWERSHELL_EXECUTABLE} is not installed in the web app container."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Azure PostgreSQL action timed out after {exc.timeout} seconds.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or "Unknown PowerShell execution failure."
        raise RuntimeError(detail) from exc

    raw = (completed.stdout or "").strip()
    if not raw:
        return {}

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"PowerShell script returned non-JSON output: {raw}") from exc


def get_cached_postgres_platform_action(action: str, **kwargs: str) -> dict | list:
    cache_key = json.dumps({"action": action, "kwargs": kwargs}, sort_keys=True)
    cached_item = _postgres_platform_cache.get(cache_key)
    now = time.time()
    if cached_item and now - cached_item[0] < POSTGRES_PLATFORM_CACHE_TTL_SECONDS:
        return cached_item[1]

    result = run_postgres_platform_action(action, **kwargs)
    _postgres_platform_cache[cache_key] = (now, result)
    return result


def hash_password(password: str) -> str:
    payload = f"{password}:{APP_SECRET}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def get_redis_client():
    global _redis_client

    if _redis_client is not None:
        return _redis_client

    if not REDIS_URL or redis is None:
        return None

    _redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


def get_request_telemetry() -> dict[str, str]:
    telemetry = _compact_fields(
        {
            "request_id": get_request_id(),
            "trace_id": get_trace_id(),
        }
    )
    return telemetry


def create_mobile_token(user_email: str, organization: str) -> str:
    return _mobile_token_serializer.dumps({"email": user_email, "organization": organization})


def verify_mobile_token(token: str) -> dict:
    try:
        payload = _mobile_token_serializer.loads(token, max_age=60 * 60 * 24 * 7)
    except SignatureExpired as exc:
        log_event(logging.WARNING, "mobile_token_expired")
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.") from exc
    except BadSignature as exc:
        log_event(logging.WARNING, "mobile_token_invalid")
        raise HTTPException(status_code=401, detail="Invalid mobile session token.") from exc

    email = payload.get("email")
    organization = payload.get("organization")
    if not email or not organization:
        log_event(logging.WARNING, "mobile_token_incomplete")
        raise HTTPException(status_code=401, detail="Incomplete mobile session token.")

    return {"email": email, "organization": organization}


def get_device_connection_string_for_org(org: str) -> str:
    cache_key = f"tenant:{org}:iot:device-connection-string"
    cached_value = None
    redis_client = get_redis_client()

    if redis_client is not None:
        try:
            cached_value = redis_client.get(cache_key)
        except Exception as exc:
            log_event(logging.WARNING, "redis_lookup_failed", organization=org, error=str(exc))

    if cached_value:
        return cached_value

    connection_string = ""
    if org == "contoso":
        connection_string = CONTOSO_DEVICE_CONNECTION_STRING
    elif org == "litware":
        connection_string = LITWARE_DEVICE_CONNECTION_STRING

    if not connection_string:
        raise RuntimeError(f"IoT device connection string is not configured for org: {org}")

    if redis_client is not None:
        try:
            redis_client.setex(cache_key, TENANT_CONNECTION_CACHE_TTL_SECONDS, connection_string)
        except Exception as exc:
            log_event(logging.WARNING, "redis_cache_write_failed", organization=org, error=str(exc))

    return connection_string


def get_iot_client_for_org(org: str):
    if org in _tenant_iot_clients:
        return _tenant_iot_clients[org]

    if IoTHubDeviceClient is None:
        raise RuntimeError("azure-iot-device dependency is not installed")

    connection_string = get_device_connection_string_for_org(org)
    client = IoTHubDeviceClient.create_from_connection_string(connection_string)
    client.connect()
    _tenant_iot_clients[org] = client
    return client


def publish_attendance_event(org: str, payload: dict):
    client = get_iot_client_for_org(org)
    log_event(
        logging.INFO,
        "attendance_event_publish_started",
        organization=org,
        event_id=payload.get("event_id"),
        source=payload.get("source"),
    )
    client.send_message(json.dumps(payload))
    log_event(
        logging.INFO,
        "attendance_event_publish_completed",
        organization=org,
        event_id=payload.get("event_id"),
        source=payload.get("source"),
    )


def ensure_schema(db_url: str):
    with get_conn(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS app_users (
                    id BIGSERIAL PRIMARY KEY,
                    email VARCHAR(320) UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS attendance_events (
                    event_id UUID PRIMARY KEY,
                    user_email VARCHAR(320) NOT NULL,
                    organization VARCHAR(50) NOT NULL,
                    event_type VARCHAR(20) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'processed',
                    source VARCHAR(50) NOT NULL DEFAULT 'iot-pipeline',
                    requested_at TIMESTAMPTZ NOT NULL,
                    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
        conn.commit()


def ensure_admin_schema():
    with get_conn(get_admin_db_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_events (
                    id BIGSERIAL PRIMARY KEY,
                    user_email VARCHAR(320) NOT NULL,
                    organization VARCHAR(50) NOT NULL,
                    actor_type VARCHAR(20) NOT NULL DEFAULT 'employee',
                    event_type VARCHAR(20) NOT NULL,
                    event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
        conn.commit()


def record_auth_event(
    user_email: str,
    organization: str,
    event_type: str,
    actor_type: str = "employee",
    source: str = "webapp",
):
    try:
        ensure_admin_schema()
        with get_conn(get_admin_db_url()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO auth_events (user_email, organization, actor_type, event_type, metadata)
                    VALUES (%s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        user_email,
                        organization,
                        actor_type,
                        event_type,
                        json.dumps({"source": source}),
                    ),
                )
            conn.commit()
    except Exception as exc:
        log_event(
            logging.WARNING,
            "auth_event_record_failed",
            user_email=user_email,
            organization=organization,
            actor_type=actor_type,
            auth_event_type=event_type,
            source=source,
            error=str(exc),
        )


def require_user_session(request: Request):
    session_user = request.session.get("user")
    if not session_user:
        return None
    if not session_user.get("email") or not session_user.get("organization"):
        return None
    return session_user


def require_admin_session(request: Request):
    session_admin = request.session.get("admin")
    if not session_admin:
        return None
    if not session_admin.get("email"):
        return None
    return session_admin


def extract_database_name(db_url: str) -> str:
    parsed = urlparse(db_url)
    return parsed.path.lstrip("/")


def get_managed_database_entries() -> list[dict[str, str | bool]]:
    entries: list[dict[str, str | bool]] = []

    try:
        admin_db_url = get_admin_db_url()
        admin_database_name = extract_database_name(admin_db_url)
        if admin_database_name:
            entries.append(
                {
                    "key": "admin",
                    "label": "Shared admin database",
                    "database_name": admin_database_name,
                    "db_url": admin_db_url,
                    "organization": "admin",
                    "can_delete": False,
                    "can_restore": False,
                }
            )
    except Exception:
        pass

    for org in ("contoso", "litware"):
        try:
            db_url = get_db_url_for_org(org)
        except Exception:
            continue

        database_name = extract_database_name(db_url)
        if not database_name:
            continue

        entries.append(
            {
                "key": org,
                "label": f"{org.title()} tenant database",
                "database_name": database_name,
                "db_url": db_url,
                "organization": org,
                "can_delete": True,
                "can_restore": True,
            }
        )

    return entries


def get_managed_database_entry(database_key: str) -> dict[str, str | bool]:
    for entry in get_managed_database_entries():
        if entry["key"] == database_key:
            return entry
    raise RuntimeError(f"Unknown managed database: {database_key}")


def load_database_dashboard_context(request: Request, msg: str = "", error: str = ""):
    session_admin = require_admin_session(request)
    if not session_admin:
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    try:
        managed_entries = get_managed_database_entries()
    except Exception as exc:
        managed_entries = []
        error = error or str(exc)

    server = {
        "configured": is_postgres_platform_configured(),
        "resource_group": AZURE_RESOURCE_GROUP,
        "server_name": AZURE_POSTGRES_SERVER_NAME,
        "subscription_id": AZURE_SUBSCRIPTION_ID,
        "status": "Unavailable",
        "sku": "",
        "version": "",
        "storage_mb": "",
        "backup_retention_days": "",
        "ha_state": "",
        "fqdn": "",
        "error": "",
    }
    backups = []

    if server["configured"]:
        try:
            overview_payload = get_cached_postgres_platform_action("show-overview")
            if isinstance(overview_payload, dict):
                server_payload = overview_payload.get("server") or {}
                if isinstance(server_payload, dict):
                    server["status"] = server_payload.get("state", "Unknown")
                    server["sku"] = ((server_payload.get("sku") or {}).get("name")) or ""
                    server["version"] = server_payload.get("version", "")
                    server["storage_mb"] = server_payload.get("storage", {}).get("storageSizeGb", "")
                    server["backup_retention_days"] = server_payload.get("backup", {}).get(
                        "backupRetentionDays", ""
                    )
                    server["ha_state"] = (server_payload.get("highAvailability") or {}).get("state", "")
                    server["fqdn"] = server_payload.get("fullyQualifiedDomainName", "")

                backup_payload = overview_payload.get("backups") or []
                if isinstance(backup_payload, list):
                    backups = backup_payload[:10]
        except Exception as exc:
            server["error"] = str(exc)
            record_exception_to_telemetry(exc, action="show_postgres_overview")

    databases = []
    try:
        with get_conn(get_postgres_admin_url()) as admin_conn:
            with admin_conn.cursor() as admin_cur:
                for entry in managed_entries:
                    database_name = str(entry["database_name"])
                    database_info = {
                        "key": entry["key"],
                        "label": entry["label"],
                        "database_name": database_name,
                        "organization": entry["organization"],
                        "exists": False,
                        "size": "Unavailable",
                        "active_connections": 0,
                        "app_user_count": None,
                        "attendance_event_count": None,
                        "schema_ready": False,
                        "can_delete": entry["can_delete"],
                        "error": "",
                    }

                    try:
                        admin_cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database_name,))
                        database_info["exists"] = admin_cur.fetchone() is not None

                        if database_info["exists"]:
                            admin_cur.execute("SELECT pg_size_pretty(pg_database_size(%s))", (database_name,))
                            size_row = admin_cur.fetchone()
                            database_info["size"] = size_row[0] if size_row and size_row[0] else "0 bytes"

                            admin_cur.execute(
                                "SELECT COUNT(*) FROM pg_stat_activity WHERE datname = %s",
                                (database_name,),
                            )
                            connection_row = admin_cur.fetchone()
                            database_info["active_connections"] = (
                                connection_row[0] if connection_row and connection_row[0] else 0
                            )

                        if database_info["exists"]:
                            with get_conn(str(entry["db_url"])) as tenant_conn:
                                with tenant_conn.cursor() as tenant_cur:
                                    tenant_cur.execute(
                                        """
                                        SELECT
                                            to_regclass('public.app_users') IS NOT NULL,
                                            to_regclass('public.attendance_events') IS NOT NULL
                                        """
                                    )
                                    schema_row = tenant_cur.fetchone() or (False, False)
                                    app_users_exists, attendance_exists = schema_row
                                    database_info["schema_ready"] = bool(app_users_exists and attendance_exists)

                                    if app_users_exists:
                                        tenant_cur.execute("SELECT COUNT(*) FROM app_users")
                                        database_info["app_user_count"] = tenant_cur.fetchone()[0] or 0

                                    if attendance_exists:
                                        tenant_cur.execute("SELECT COUNT(*) FROM attendance_events")
                                        database_info["attendance_event_count"] = tenant_cur.fetchone()[0] or 0
                    except Exception as exc:
                        database_info["error"] = str(exc)
                        record_exception_to_telemetry(exc, database_name=database_name)

                    databases.append(database_info)
    except Exception as exc:
        error = error or str(exc)
        record_exception_to_telemetry(exc, action="load_database_inventory")

    return templates.TemplateResponse(
        request,
        "admin_database.html",
        {
            "request": request,
            "admin_email": session_admin["email"],
            "msg": msg,
            "error": error,
            "server": server,
            "backups": backups,
            "databases": databases,
            "postgres_admin_configured": bool(POSTGRES_ADMIN_URL or DATABASE_URL),
            "postgres_platform_configured": is_postgres_platform_configured(),
        },
    )


class MobileCredentials(BaseModel):
    email: str
    password: str = Field(min_length=8)


class MobilePunchRequest(BaseModel):
    action: str


def is_github_dashboard_configured() -> bool:
    return bool(GITHUB_DASHBOARD_TOKEN and "/" in GITHUB_REPOSITORY)


def parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_github_repo_parts() -> tuple[str, str]:
    if "/" not in GITHUB_REPOSITORY:
        raise RuntimeError("GITHUB_REPOSITORY must use owner/repo format.")
    owner, repo = GITHUB_REPOSITORY.split("/", 1)
    return owner, repo


def github_api_request(
    method: str,
    path: str,
    *,
    query: dict | None = None,
    payload: dict | None = None,
    expected_statuses: tuple[int, ...] = (200,),
) -> dict | list | None:
    if not is_github_dashboard_configured():
        raise RuntimeError("GitHub dashboard integration is not configured.")

    url = f"{GITHUB_API_URL}{path}"
    if query:
        url = f"{url}?{urllib_parse.urlencode(query)}"

    body = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_DASHBOARD_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pranathi-admin-dashboard",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib_request.Request(url, data=body, method=method, headers=headers)

    try:
        with urllib_request.urlopen(request, timeout=30) as response:
            status = response.getcode()
            raw = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        detail = raw
        try:
            detail = json.loads(raw).get("message", raw)
        except json.JSONDecodeError:
            pass
        raise RuntimeError(f"GitHub API request failed ({exc.code}): {detail}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"GitHub API request failed: {exc.reason}") from exc

    if status not in expected_statuses:
        raise RuntimeError(f"GitHub API request returned unexpected status {status}.")

    if not raw:
        return None

    return json.loads(raw)


def load_open_pull_requests() -> list[dict]:
    owner, repo = get_github_repo_parts()
    data = github_api_request(
        "GET",
        f"/repos/{owner}/{repo}/pulls",
        query={"state": "open", "sort": "updated", "direction": "desc", "per_page": 25},
    )
    pulls = []
    for item in data or []:
        pulls.append(
            {
                "number": item["number"],
                "title": item["title"],
                "author": (item.get("user") or {}).get("login", "unknown"),
                "branch": (item.get("head") or {}).get("ref", ""),
                "head_sha": (item.get("head") or {}).get("sha", ""),
                "base_branch": (item.get("base") or {}).get("ref", ""),
                "updated_at": item.get("updated_at"),
                "html_url": item.get("html_url"),
                "draft": item.get("draft", False),
            }
        )
    return pulls


def merge_pull_request(pr_number: int, head_sha: str) -> str:
    owner, repo = get_github_repo_parts()
    response = github_api_request(
        "PUT",
        f"/repos/{owner}/{repo}/pulls/{pr_number}/merge",
        payload={
            "sha": head_sha,
            "merge_method": "merge",
            "commit_title": f"Merge pull request #{pr_number} via admin dashboard",
        },
        expected_statuses=(200, 201),
    )
    merge_sha = (response or {}).get("sha")
    if not merge_sha:
        raise RuntimeError("GitHub merged the pull request but did not return a merge commit SHA.")
    return merge_sha


def close_pull_request(pr_number: int):
    owner, repo = get_github_repo_parts()
    github_api_request(
        "PATCH",
        f"/repos/{owner}/{repo}/pulls/{pr_number}",
        payload={"state": "closed"},
        expected_statuses=(200,),
    )


def trigger_github_workflow(workflow_name: str, ref: str, inputs: dict | None = None):
    owner, repo = get_github_repo_parts()
    github_api_request(
        "POST",
        f"/repos/{owner}/{repo}/actions/workflows/{workflow_name}/dispatches",
        payload={"ref": ref, "inputs": inputs or {}},
        expected_statuses=(204,),
    )


def wait_for_workflow_run(
    workflow_name: str,
    *,
    head_sha: str,
    event: str,
    created_after: str,
    timeout_seconds: int = 420,
    poll_interval_seconds: int = 5,
) -> dict:
    owner, repo = get_github_repo_parts()
    created_after_dt = parse_iso_datetime(created_after)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        data = github_api_request(
            "GET",
            f"/repos/{owner}/{repo}/actions/workflows/{workflow_name}/runs",
            query={"event": event, "per_page": 20},
        )
        for run in (data or {}).get("workflow_runs", []):
            if run.get("head_sha") != head_sha:
                continue
            created_at = run.get("created_at")
            if not created_at or parse_iso_datetime(created_at) < created_after_dt:
                continue
            return {
                "id": run.get("id"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "html_url": run.get("html_url"),
            }
        time.sleep(poll_interval_seconds)
    raise RuntimeError(f"Timed out waiting for workflow {workflow_name} to start.")


def wait_for_workflow_completion(
    workflow_name: str,
    *,
    run_id: int,
    timeout_seconds: int = 900,
    poll_interval_seconds: int = 10,
) -> dict:
    owner, repo = get_github_repo_parts()
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        run = github_api_request(
            "GET",
            f"/repos/{owner}/{repo}/actions/runs/{run_id}",
            expected_statuses=(200,),
        )
        if (run or {}).get("status") == "completed":
            return {
                "id": run.get("id"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "html_url": run.get("html_url"),
            }
        time.sleep(poll_interval_seconds)
    raise RuntimeError(f"Timed out waiting for workflow {workflow_name} to finish.")


def load_github_dashboard_context(request: Request, msg: str = "", error: str = ""):
    session_admin = require_admin_session(request)
    if not session_admin:
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    pulls = []
    github_error = error
    if is_github_dashboard_configured():
        try:
            pulls = load_open_pull_requests()
        except Exception as exc:
            record_exception_to_telemetry(exc)
            github_error = str(exc)

    return templates.TemplateResponse(
        request,
        "admin_github.html",
        {
            "request": request,
            "admin_email": session_admin["email"],
            "msg": msg,
            "error": github_error,
            "pull_requests": pulls,
            "github_repository": GITHUB_REPOSITORY,
            "github_configured": is_github_dashboard_configured(),
            "deploy_qa_default": GITHUB_DEPLOY_QA_DEFAULT,
        },
    )


def load_error_operations_context(request: Request, msg: str = "", error: str = "", minutes: int = 60):
    session_admin = require_admin_session(request)
    if not session_admin:
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    issues: list[dict[str, Any]] = []
    issue_overview = build_issue_overview([])
    scan_error = error
    if is_error_operations_configured():
        try:
            issues = load_recent_error_operations(minutes=minutes)
            issue_overview = build_issue_overview(issues)
        except Exception as exc:
            record_exception_to_telemetry(exc, action="scan_recent_errors")
            scan_error = str(exc)

    return templates.TemplateResponse(
        request,
        "admin_errors.html",
        {
            "request": request,
            "admin_email": session_admin["email"],
            "msg": msg,
            "error": scan_error,
            "issues": issues,
            "issue_overview": issue_overview,
            "minutes": minutes,
            "error_ops_configured": is_error_operations_configured(),
            "ai_error_analysis_configured": is_ai_error_analysis_configured(),
            "allow_webapp_restart": ALLOW_WEBAPP_RESTART,
            "allow_functionapp_restart": ALLOW_FUNCTIONAPP_RESTART,
            "allow_postgres_restart": ALLOW_POSTGRES_RESTART,
        },
    )


def authenticate_employee(email: str, password: str) -> dict:
    normalized = email.lower().strip()
    org = get_org_for_email(normalized)
    if not org:
        log_event(logging.WARNING, "employee_org_validation_failed", user_email=normalized)
        raise HTTPException(
            status_code=400,
            detail=f"Only {CONTOSO_DOMAIN} and {LITWARE_DOMAIN} emails are allowed.",
        )

    try:
        db_url = get_db_url_for_org(org)
        ensure_schema(db_url)
        with get_conn(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT email FROM app_users WHERE email = %s AND password_hash = %s",
                    (normalized, hash_password(password)),
                )
                row = cur.fetchone()
                if not row:
                    log_event(logging.WARNING, "employee_login_failed", user_email=normalized, organization=org)
                    raise HTTPException(status_code=401, detail="Invalid email or password.")
    except HTTPException:
        raise
    except Exception as exc:
        record_exception_to_telemetry(exc)
        raise HTTPException(status_code=500, detail="Database connection failed.") from exc

    return {"email": normalized, "organization": org}


def create_employee_account(email: str, password: str) -> dict:
    normalized = email.lower().strip()
    org = get_org_for_email(normalized)
    if not org:
        log_event(logging.WARNING, "employee_org_validation_failed", user_email=normalized)
        raise HTTPException(
            status_code=400,
            detail=f"Only {CONTOSO_DOMAIN} and {LITWARE_DOMAIN} emails are allowed.",
        )

    if len(password) < 8:
        log_event(logging.WARNING, "employee_password_validation_failed", user_email=normalized)
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    try:
        db_url = get_db_url_for_org(org)
        ensure_schema(db_url)
        with get_conn(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM app_users WHERE email = %s", (normalized,))
                if cur.fetchone():
                    log_event(logging.WARNING, "employee_signup_conflict", user_email=normalized, organization=org)
                    raise HTTPException(status_code=409, detail="Email already exists.")

                cur.execute(
                    "INSERT INTO app_users(email, password_hash) VALUES(%s, %s)",
                    (normalized, hash_password(password)),
                )
            conn.commit()
    except HTTPException:
        raise
    except Exception as exc:
        record_exception_to_telemetry(exc)
        raise HTTPException(status_code=500, detail="Database connection failed.") from exc

    return {"email": normalized, "organization": org}


def load_attendance_history(user_email: str, organization: str, limit: int = 20):
    try:
        db_url = get_db_url_for_org(organization)
        ensure_schema(db_url)
        with get_conn(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT event_type, requested_at, processed_at, status, source
                    FROM attendance_events
                    WHERE user_email = %s
                    ORDER BY requested_at DESC
                    LIMIT %s
                    """,
                    (user_email, limit),
                )
                return [
                    {
                        "event_type": row[0],
                        "requested_at": row[1].isoformat() if row[1] else None,
                        "processed_at": row[2].isoformat() if row[2] else None,
                        "status": row[3],
                        "source": row[4],
                    }
                    for row in cur.fetchall()
                ]
    except Exception as exc:
        record_exception_to_telemetry(exc)
        raise HTTPException(status_code=500, detail="Attendance history could not be loaded.") from exc


def queue_attendance_event(user_email: str, organization: str, action: str, source: str):
    normalized_action = action.lower().strip()
    if normalized_action not in {"punch_in", "punch_out"}:
        log_event(
            logging.WARNING,
            "attendance_action_validation_failed",
            user_email=user_email,
            organization=organization,
            attendance_action=normalized_action,
            source=source,
        )
        raise HTTPException(status_code=400, detail="Invalid attendance action.")

    payload = {
        "event_id": str(uuid.uuid4()),
        "user_email": user_email,
        "organization": organization,
        "event_type": normalized_action,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "telemetry": get_request_telemetry(),
    }

    try:
        publish_attendance_event(organization, payload)
    except Exception as exc:
        record_exception_to_telemetry(
            exc,
            organization=organization,
            user_email=user_email,
            event_id=payload["event_id"],
            attendance_action=normalized_action,
            source=source,
        )
        raise HTTPException(status_code=502, detail="Attendance event could not be queued to IoT Hub.") from exc

    log_event(
        logging.INFO,
        "attendance_event_queued",
        organization=organization,
        user_email=user_email,
        event_id=payload["event_id"],
        attendance_action=normalized_action,
        source=source,
    )
    return payload


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
    if redis is None:
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


def get_mobile_user_from_header(authorization: str | None) -> dict:
    if not authorization:
        log_event(logging.WARNING, "mobile_auth_header_missing")
        raise HTTPException(status_code=401, detail="Authorization header is required.")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        log_event(logging.WARNING, "mobile_auth_header_invalid", authorization_scheme=scheme or "missing")
        raise HTTPException(status_code=401, detail="Authorization header must use Bearer token.")

    return verify_mobile_token(token)


def load_dashboard_context(request: Request, msg: str = "", error: str = ""):
    session_user = require_user_session(request)
    if not session_user:
        return RedirectResponse("/login?msg=Please+log+in+to+continue", status_code=303)

    try:
        db_url = get_db_url_for_org(session_user["organization"])
        ensure_schema(db_url)
        with get_conn(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT email FROM app_users ORDER BY created_at DESC LIMIT 200")
                employees = [r[0] for r in cur.fetchall()]
                cur.execute(
                    """
                    SELECT event_type, requested_at, processed_at, status, source
                    FROM attendance_events
                    WHERE user_email = %s
                    ORDER BY requested_at DESC
                    LIMIT 20
                    """,
                    (session_user["email"],),
                )
                attendance_history = [
                    {
                        "event_type": row[0],
                        "requested_at": row[1],
                        "processed_at": row[2],
                        "status": row[3],
                        "source": row[4],
                    }
                    for row in cur.fetchall()
                ]
    except Exception as exc:
        record_exception_to_telemetry(exc)
        return RedirectResponse("/login?msg=Database+connection+failed", status_code=303)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "user_email": session_user["email"],
            "organization": session_user["organization"],
            "employees": employees,
            "attendance_history": attendance_history,
            "msg": msg,
            "error": error,
        },
    )


def load_admin_dashboard_context(request: Request, msg: str = "", error: str = ""):
    session_admin = require_admin_session(request)
    if not session_admin:
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    recent_auth_events = []
    recent_attendance_events = []
    stats = {
        "login_count": 0,
        "logout_count": 0,
        "attendance_count": 0,
        "tenant_count": 0,
        "database_count": len(get_managed_database_entries()),
    }

    try:
        ensure_admin_schema()
        with get_conn(get_admin_db_url()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE event_type = 'login' AND event_time >= NOW() - INTERVAL '24 hours'),
                        COUNT(*) FILTER (WHERE event_type = 'logout' AND event_time >= NOW() - INTERVAL '24 hours'),
                        COUNT(DISTINCT organization) FILTER (WHERE actor_type = 'employee')
                    FROM auth_events
                    """
                )
                row = cur.fetchone()
                stats["login_count"] = row[0] or 0
                stats["logout_count"] = row[1] or 0
                stats["tenant_count"] = row[2] or 0

                cur.execute(
                    """
                    SELECT user_email, organization, actor_type, event_type, event_time
                    FROM auth_events
                    ORDER BY event_time DESC
                    LIMIT 50
                    """
                )
                recent_auth_events = [
                    {
                        "user_email": item[0],
                        "organization": item[1],
                        "actor_type": item[2],
                        "event_type": item[3],
                        "event_time": item[4],
                    }
                    for item in cur.fetchall()
                ]
    except Exception as exc:
        record_exception_to_telemetry(exc)
        return RedirectResponse("/admin/login?msg=Admin+database+connection+failed", status_code=303)

    for org in ("contoso", "litware"):
        try:
            db_url = get_db_url_for_org(org)
            ensure_schema(db_url)
            with get_conn(db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT user_email, organization, event_type, requested_at, processed_at, status, source
                        FROM attendance_events
                        ORDER BY requested_at DESC
                        LIMIT 25
                        """
                    )
                    recent_attendance_events.extend(
                        {
                            "user_email": row[0],
                            "organization": row[1],
                            "event_type": row[2],
                            "requested_at": row[3],
                            "processed_at": row[4],
                            "status": row[5],
                            "source": row[6],
                        }
                        for row in cur.fetchall()
                    )

                    cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM attendance_events
                        WHERE requested_at >= NOW() - INTERVAL '24 hours'
                        """
                    )
                    stats["attendance_count"] += cur.fetchone()[0] or 0
        except Exception as exc:
            logger.warning("Admin dashboard could not load attendance for %s: %s", org, exc)

    recent_attendance_events.sort(key=lambda item: item["requested_at"], reverse=True)
    recent_attendance_events = recent_attendance_events[:50]

    return templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {
            "request": request,
            "admin_email": session_admin["email"],
            "recent_auth_events": recent_auth_events,
            "recent_attendance_events": recent_attendance_events,
            "stats": stats,
            "msg": msg,
            "error": error,
            "github_configured": is_github_dashboard_configured(),
        },
    )


@app.get("/", response_class=HTMLResponse)
def home(request: Request, msg: str = ""):
    session_user = require_user_session(request)
    session_admin = require_admin_session(request)
    return templates.TemplateResponse(
        request,
        "home.html",
        {"request": request, "session_user": session_user, "session_admin": session_admin, "msg": msg},
    )


@app.middleware("http")
async def add_request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    token = _request_id_context.set(request_id)
    request.state.request_id = request_id
    started = time.perf_counter()
    path = request.url.path
    method = request.method

    log_event(logging.INFO, "http_request_started", method=method, path=path)

    try:
        response = await call_next(request)
    except Exception as exc:
        record_exception_to_telemetry(exc, method=method, path=path)
        _request_id_context.reset(token)
        raise

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    level = get_http_log_level(response.status_code)
    log_event(
        level,
        "http_request_completed",
        method=method,
        path=path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    _request_id_context.reset(token)
    return response


@app.exception_handler(HTTPException)
async def app_http_exception_handler(request: Request, exc: HTTPException):
    record_http_failure_to_telemetry(
        request,
        status_code=exc.status_code,
        detail=exc.detail,
        event_name="http_exception_raised",
    )
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def app_validation_exception_handler(request: Request, exc: RequestValidationError):
    record_http_failure_to_telemetry(
        request,
        status_code=422,
        detail=exc.errors(),
        event_name="request_validation_failed",
        error_count=len(exc.errors()),
    )
    return await request_validation_exception_handler(request, exc)


@app.get("/health/live")
def liveness():
    return {"status": "healthy", "environment": os.getenv("ENV", "local")}


@app.get("/health")
def health():
    payload, status_code = build_health_payload()
    log_level = logging.INFO if status_code == 200 else logging.ERROR
    log_event(log_level, "health_check_completed", overall_status=payload["status"], checks=payload["checks"])
    return JSONResponse(payload, status_code=status_code)


@app.get("/hello/{name}")
def hello(name: str):
    return {"message": f"Hello, {name}!"}


@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, msg: str = ""):
    return RedirectResponse("/?msg=Employee+signup+has+moved+to+the+macOS+app", status_code=303)


@app.post("/signup")
def signup(email: str = Form(...), password: str = Form(...), confirm_password: str = Form(...)):
    if password != confirm_password:
        return RedirectResponse("/?msg=Passwords+do+not+match", status_code=303)

    try:
        create_employee_account(email, password)
    except HTTPException as exc:
        return RedirectResponse(f"/?msg={exc.detail.replace(' ', '+')}", status_code=303)

    return RedirectResponse("/?msg=Account+created+in+mobile+employee+app", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, msg: str = ""):
    return RedirectResponse("/?msg=Employee+login+has+moved+to+the+macOS+app", status_code=303)


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        authenticate_employee(email, password)
    except HTTPException as exc:
        return RedirectResponse(f"/?msg={exc.detail.replace(' ', '+')}", status_code=303)

    return RedirectResponse("/?msg=Employee+login+has+moved+to+the+macOS+app", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, msg: str = "", error: str = ""):
    return RedirectResponse("/?msg=Employee+dashboard+is+available+in+the+macOS+app", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
def admin_root(request: Request):
    if require_admin_session(request):
        return RedirectResponse("/admin/dashboard", status_code=303)
    return RedirectResponse("/admin/login", status_code=303)


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request, msg: str = ""):
    return templates.TemplateResponse(request, "admin_login.html", {"request": request, "msg": msg})


@app.post("/admin/login")
def admin_login(request: Request, email: str = Form(...), password: str = Form(...)):
    normalized = email.lower().strip()
    if normalized != ADMIN_EMAIL or hash_password(password) != hash_password(ADMIN_PASSWORD):
        log_event(logging.WARNING, "admin_login_failed", user_email=normalized)
        return RedirectResponse("/admin/login?msg=Invalid+admin+credentials", status_code=303)

    request.session["admin"] = {"email": normalized}
    log_event(logging.INFO, "admin_login_succeeded", user_email=normalized)
    record_auth_event(normalized, "admin", "login", actor_type="admin")
    return RedirectResponse("/admin/dashboard?msg=Admin+session+started", status_code=303)


@app.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request, msg: str = "", error: str = ""):
    return load_admin_dashboard_context(request, msg=msg, error=error)


@app.get("/admin/github", response_class=HTMLResponse)
def admin_github_dashboard(request: Request, msg: str = "", error: str = ""):
    return load_github_dashboard_context(request, msg=msg, error=error)


@app.get("/admin/errors", response_class=HTMLResponse)
def admin_error_operations(request: Request, msg: str = "", error: str = "", minutes: int = 60):
    return load_error_operations_context(request, msg=msg, error=error, minutes=minutes)


@app.get("/admin/databases", response_class=HTMLResponse)
def admin_database_dashboard(request: Request, msg: str = "", error: str = ""):
    return load_database_dashboard_context(request, msg=msg, error=error)


@app.post("/admin/errors/apply")
def admin_error_apply_fix(
    request: Request,
    fix_type: str = Form(...),
    source_table: str = Form(""),
    error_type: str = Form(""),
    example_message: str = Form(""),
    minutes: int = Form(60),
):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    del source_table, error_type, example_message
    fix = build_fix_metadata(fix_type)

    if not fix["is_available"]:
        message = f"{fix['action_label']} is not enabled for this environment."
        return RedirectResponse(f"/admin/errors?error={urllib_parse.quote_plus(message)}", status_code=303)

    try:
        result_message = apply_error_fix(fix_type)
        return RedirectResponse(
            f"/admin/errors?minutes={minutes}&msg={urllib_parse.quote_plus(result_message)}",
            status_code=303,
        )
    except Exception as exc:
        record_exception_to_telemetry(exc, action="apply_error_fix", fix_type=fix_type)
        return RedirectResponse(
            f"/admin/errors?minutes={minutes}&error={urllib_parse.quote_plus(str(exc))}",
            status_code=303,
        )


@app.post("/admin/errors/deny")
def admin_error_deny_fix(request: Request, minutes: int = Form(60), fix_label: str = Form("fix")):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    message = f"{fix_label} was denied. No automated action was taken."
    return RedirectResponse(
        f"/admin/errors?minutes={minutes}&msg={urllib_parse.quote_plus(message)}",
        status_code=303,
    )


@app.post("/admin/github/approve")
def admin_github_approve(
    request: Request,
    pr_number: int = Form(...),
    head_sha: str = Form(...),
    deploy_qa: str = Form("false"),
):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    if not is_github_dashboard_configured():
        return RedirectResponse(
            "/admin/github?error=GitHub+dashboard+integration+is+not+configured",
            status_code=303,
        )

    deploy_qa_enabled = deploy_qa.lower() == "true"
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        merge_sha = merge_pull_request(pr_number, head_sha)
        trigger_github_workflow(GITHUB_CI_WORKFLOW, "main")
        ci_run = wait_for_workflow_run(
            GITHUB_CI_WORKFLOW,
            head_sha=merge_sha,
            event="workflow_dispatch",
            created_after=now_iso,
        )
        ci_run = wait_for_workflow_completion(GITHUB_CI_WORKFLOW, run_id=ci_run["id"])
        if ci_run.get("conclusion") != "success":
            raise RuntimeError(f"CI build failed. Review run: {ci_run.get('html_url')}")

        release_created_after = datetime.now(timezone.utc).isoformat()
        trigger_github_workflow(
            GITHUB_RELEASE_WORKFLOW,
            "main",
            inputs={
                "image_tag": merge_sha,
                "deploy_qa": "true" if deploy_qa_enabled else "false",
            },
        )
        release_run = wait_for_workflow_run(
            GITHUB_RELEASE_WORKFLOW,
            head_sha=merge_sha,
            event="workflow_dispatch",
            created_after=release_created_after,
        )
        message = (
            f"PR #{pr_number} merged. CI passed and release run #{release_run['id']} started."
        )
        return RedirectResponse(
            f"/admin/github?msg={urllib_parse.quote_plus(message)}",
            status_code=303,
        )
    except Exception as exc:
        record_exception_to_telemetry(exc)
        return RedirectResponse(
            f"/admin/github?error={urllib_parse.quote_plus(str(exc))}",
            status_code=303,
        )


@app.post("/admin/github/reject")
def admin_github_reject(request: Request, pr_number: int = Form(...)):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    if not is_github_dashboard_configured():
        return RedirectResponse(
            "/admin/github?error=GitHub+dashboard+integration+is+not+configured",
            status_code=303,
        )

    try:
        close_pull_request(pr_number)
        return RedirectResponse(
            f"/admin/github?msg={urllib_parse.quote_plus(f'PR #{pr_number} was closed.')}",
            status_code=303,
        )
    except Exception as exc:
        record_exception_to_telemetry(exc)
        return RedirectResponse(
            f"/admin/github?error={urllib_parse.quote_plus(str(exc))}",
            status_code=303,
        )


@app.post("/admin/databases/ensure-schema")
def admin_database_ensure_schema(request: Request, database_key: str = Form(...)):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    try:
        entry = get_managed_database_entry(database_key)
        ensure_schema(str(entry["db_url"]))
        message = f"{entry['label']} schema verified."
        return RedirectResponse(f"/admin/databases?msg={urllib_parse.quote_plus(message)}", status_code=303)
    except Exception as exc:
        record_exception_to_telemetry(exc, database_key=database_key, action="ensure_schema")
        return RedirectResponse(
            f"/admin/databases?error={urllib_parse.quote_plus(str(exc))}",
            status_code=303,
        )


@app.post("/admin/databases/server/backup")
def admin_database_server_backup(request: Request, backup_name: str = Form(...)):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    try:
        run_postgres_platform_action("create-backup", BackupName=backup_name)
        message = f"Backup {backup_name} started for {AZURE_POSTGRES_SERVER_NAME}."
        return RedirectResponse(f"/admin/databases?msg={urllib_parse.quote_plus(message)}", status_code=303)
    except Exception as exc:
        record_exception_to_telemetry(exc, action="create_backup")
        return RedirectResponse(
            f"/admin/databases?error={urllib_parse.quote_plus(str(exc))}",
            status_code=303,
        )


@app.post("/admin/databases/server/restore")
def admin_database_server_restore(
    request: Request,
    target_server_name: str = Form(...),
    restore_time_utc: str = Form(...),
):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    try:
        run_postgres_platform_action(
            "restore-server",
            TargetServerName=target_server_name,
            RestoreTimeUtc=restore_time_utc,
        )
        message = (
            f"Point-in-time restore started for {AZURE_POSTGRES_SERVER_NAME} into {target_server_name}."
        )
        return RedirectResponse(f"/admin/databases?msg={urllib_parse.quote_plus(message)}", status_code=303)
    except Exception as exc:
        record_exception_to_telemetry(exc, action="restore_server")
        return RedirectResponse(
            f"/admin/databases?error={urllib_parse.quote_plus(str(exc))}",
            status_code=303,
        )


@app.post("/admin/databases/server/start")
def admin_database_server_start(request: Request):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    try:
        run_postgres_platform_action("start-server")
        message = f"Start requested for {AZURE_POSTGRES_SERVER_NAME}."
        return RedirectResponse(f"/admin/databases?msg={urllib_parse.quote_plus(message)}", status_code=303)
    except Exception as exc:
        record_exception_to_telemetry(exc, action="start_server")
        return RedirectResponse(
            f"/admin/databases?error={urllib_parse.quote_plus(str(exc))}",
            status_code=303,
        )


@app.post("/admin/databases/server/stop")
def admin_database_server_stop(request: Request):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    try:
        run_postgres_platform_action("stop-server")
        message = f"Stop requested for {AZURE_POSTGRES_SERVER_NAME}."
        return RedirectResponse(f"/admin/databases?msg={urllib_parse.quote_plus(message)}", status_code=303)
    except Exception as exc:
        record_exception_to_telemetry(exc, action="stop_server")
        return RedirectResponse(
            f"/admin/databases?error={urllib_parse.quote_plus(str(exc))}",
            status_code=303,
        )


@app.post("/admin/databases/server/restart")
def admin_database_server_restart(request: Request):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    try:
        run_postgres_platform_action("restart-server")
        message = f"Restart requested for {AZURE_POSTGRES_SERVER_NAME}."
        return RedirectResponse(f"/admin/databases?msg={urllib_parse.quote_plus(message)}", status_code=303)
    except Exception as exc:
        record_exception_to_telemetry(exc, action="restart_server")
        return RedirectResponse(
            f"/admin/databases?error={urllib_parse.quote_plus(str(exc))}",
            status_code=303,
        )


@app.post("/admin/databases/server/delete")
def admin_database_server_delete(request: Request):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    try:
        run_postgres_platform_action("delete-server")
        message = f"Delete requested for server {AZURE_POSTGRES_SERVER_NAME}."
        return RedirectResponse(f"/admin/databases?msg={urllib_parse.quote_plus(message)}", status_code=303)
    except Exception as exc:
        record_exception_to_telemetry(exc, action="delete_server")
        return RedirectResponse(
            f"/admin/databases?error={urllib_parse.quote_plus(str(exc))}",
            status_code=303,
        )


@app.post("/admin/databases/server/create")
def admin_database_server_create(
    request: Request,
    new_server_name: str = Form(...),
    admin_user: str = Form(...),
    admin_password: str = Form(...),
    sku_name: str = Form("B_Standard_B1ms"),
    storage_size_gb: str = Form("32"),
    postgres_version: str = Form("14"),
):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    try:
        run_postgres_platform_action(
            "create-server",
            NewServerName=new_server_name,
            AdminUser=admin_user,
            AdminPassword=admin_password,
            SkuName=sku_name,
            StorageSizeGb=storage_size_gb,
            PostgresVersion=postgres_version,
        )
        log_event(logging.INFO, "create_server_requested", new_server_name=new_server_name)
        message = f"Create server requested: {new_server_name} in {AZURE_RESOURCE_GROUP}."
        return RedirectResponse(f"/admin/databases?msg={urllib_parse.quote_plus(message)}", status_code=303)
    except Exception as exc:
        record_exception_to_telemetry(exc, action="create_server", new_server_name=new_server_name)
        return RedirectResponse(
            f"/admin/databases?error={urllib_parse.quote_plus(str(exc))}",
            status_code=303,
        )


@app.post("/admin/databases/database/delete")
def admin_database_delete(request: Request, database_key: str = Form(...)):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    try:
        entry = get_managed_database_entry(database_key)
        if entry["organization"] == "admin":
            raise RuntimeError("Delete is disabled for the shared admin database.")
        run_postgres_platform_action("delete-database", DatabaseName=str(entry["database_name"]))
        message = f"{entry['label']} delete requested on {AZURE_POSTGRES_SERVER_NAME}."
        return RedirectResponse(f"/admin/databases?msg={urllib_parse.quote_plus(message)}", status_code=303)
    except Exception as exc:
        record_exception_to_telemetry(exc, database_key=database_key, action="delete_database")
        return RedirectResponse(
            f"/admin/databases?error={urllib_parse.quote_plus(str(exc))}",
            status_code=303,
        )


@app.post("/attendance/punch")
def attendance_punch(request: Request, action: str = Form(...)):
    return RedirectResponse("/?msg=Attendance+punching+has+moved+to+the+macOS+app", status_code=303)


@app.get("/logout")
def logout(request: Request):
    session_user = require_user_session(request)
    session_admin = require_admin_session(request)
    if session_user:
        record_auth_event(session_user["email"], session_user["organization"], "logout")
    if session_admin:
        record_auth_event(session_admin["email"], "admin", "logout", actor_type="admin")
    request.session.clear()
    log_event(logging.INFO, "session_logout_completed")
    return RedirectResponse("/", status_code=303)


@app.get("/employee/logout")
def employee_logout(request: Request):
    session_user = require_user_session(request)
    if session_user:
        record_auth_event(session_user["email"], session_user["organization"], "logout")
    request.session.pop("user", None)
    return RedirectResponse("/", status_code=303)


@app.get("/admin/logout")
def admin_logout(request: Request):
    session_admin = require_admin_session(request)
    if session_admin:
        record_auth_event(session_admin["email"], "admin", "logout", actor_type="admin")
    request.session.pop("admin", None)
    return RedirectResponse("/", status_code=303)


@app.post("/api/mobile/signup")
def mobile_signup(payload: MobileCredentials):
    employee = create_employee_account(payload.email, payload.password)
    token = create_mobile_token(employee["email"], employee["organization"])
    log_event(
        logging.INFO,
        "mobile_signup_succeeded",
        user_email=employee["email"],
        organization=employee["organization"],
    )
    record_auth_event(employee["email"], employee["organization"], "signup", source="mobile-app")
    record_auth_event(employee["email"], employee["organization"], "login", source="mobile-app")
    return JSONResponse(
        {
            "token": token,
            "user": employee,
            "message": "Account created successfully.",
        }
    )


@app.post("/api/mobile/login")
def mobile_login(payload: MobileCredentials):
    employee = authenticate_employee(payload.email, payload.password)
    token = create_mobile_token(employee["email"], employee["organization"])
    log_event(
        logging.INFO,
        "mobile_login_succeeded",
        user_email=employee["email"],
        organization=employee["organization"],
    )
    record_auth_event(employee["email"], employee["organization"], "login", source="mobile-app")
    return JSONResponse(
        {
            "token": token,
            "user": employee,
            "message": "Login successful.",
        }
    )


@app.get("/api/mobile/me")
def mobile_me(authorization: str | None = Header(default=None)):
    employee = get_mobile_user_from_header(authorization)
    return JSONResponse({"user": employee})


@app.get("/api/mobile/attendance/history")
def mobile_attendance_history(authorization: str | None = Header(default=None)):
    employee = get_mobile_user_from_header(authorization)
    history = load_attendance_history(employee["email"], employee["organization"])
    return JSONResponse({"history": history})


@app.post("/api/mobile/attendance/punch")
def mobile_attendance_punch(payload: MobilePunchRequest, authorization: str | None = Header(default=None)):
    employee = get_mobile_user_from_header(authorization)
    event = queue_attendance_event(
        employee["email"],
        employee["organization"],
        payload.action,
        source="mobile-app",
    )
    log_event(
        logging.INFO,
        "mobile_attendance_punch_accepted",
        user_email=employee["email"],
        organization=employee["organization"],
        event_id=event["event_id"],
        attendance_action=event["event_type"],
    )
    return JSONResponse(
        {
            "message": f"{event['event_type'].replace('_', ' ').title()} queued through IoT Hub.",
            "event": event,
        }
    )


@app.post("/api/mobile/logout")
def mobile_logout(authorization: str | None = Header(default=None)):
    employee = get_mobile_user_from_header(authorization)
    record_auth_event(employee["email"], employee["organization"], "logout", source="mobile-app")
    return JSONResponse({"message": "Logout recorded."})
