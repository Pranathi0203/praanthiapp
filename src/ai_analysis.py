import json
import logging
import os
import re
import subprocess
import time
from typing import Any
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from urllib import error as urllib_error

from config import (
    APPLICATION_INSIGHTS_NAME,
    ALLOW_FUNCTIONAPP_RESTART,
    ALLOW_POSTGRES_RESTART,
    ALLOW_WEBAPP_RESTART,
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_DEPLOYMENT,
    AZURE_OPENAI_ENDPOINT,
    AZURE_POSTGRES_SERVER_NAME,
    AZURE_RESOURCE_GROUP,
    AZURE_SUBSCRIPTION_ID,
    FUNCTION_APP_NAME,
    IOTHUB_NAME,
    LOG_ANALYTICS_WORKSPACE_ID,
    LOG_ANALYTICS_WORKSPACE_RESOURCE_ID,
    POSTGRES_PLATFORM_ACTION_TIMEOUT_SECONDS,
    POWERSHELL_EXECUTABLE,
    SERVICEBUS_NAMESPACE_NAME,
    WEBAPP_NAME,
    AZURE_USE_MANAGED_IDENTITY,
    GITHUB_REPOSITORY,
)
from telemetry import log_event, record_exception_to_telemetry
from azure_utils import (
    arm_get,
    post_json_with_headers,
    post_json_with_token,
    run_azure_cli_access_token,
    run_azure_cli_json,
)
from db import (
    get_postgres_admin_script_path,
    is_postgres_platform_configured,
)

logger = logging.getLogger(__name__)

_postgres_platform_cache: dict[str, tuple[float, dict | list]] = {}
POSTGRES_PLATFORM_CACHE_TTL_SECONDS = int(os.getenv("POSTGRES_PLATFORM_CACHE_TTL_SECONDS", "30"))


# ─── Error ops helpers ────────────────────────────────────────────────────────

def is_error_operations_configured() -> bool:
    has_workspace = bool(LOG_ANALYTICS_WORKSPACE_ID)
    has_app_insights_lookup = bool(AZURE_SUBSCRIPTION_ID and AZURE_RESOURCE_GROUP and APPLICATION_INSIGHTS_NAME)
    return has_workspace or has_app_insights_lookup


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


# ─── Azure OpenAI ─────────────────────────────────────────────────────────────

def is_ai_configured() -> bool:
    return bool(AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY and AZURE_OPENAI_DEPLOYMENT)


def _openai_url() -> str:
    return (
        f"{AZURE_OPENAI_ENDPOINT}/openai/deployments/"
        f"{urllib_parse.quote(AZURE_OPENAI_DEPLOYMENT, safe='')}"
        f"/chat/completions?api-version={urllib_parse.quote(AZURE_OPENAI_API_VERSION, safe='')}"
    )


def call_azure_openai(system_prompt: str, user_message: str) -> dict[str, Any]:
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.1,
        "max_completion_tokens": 4000,
        "response_format": {"type": "json_object"},
    }
    response = post_json_with_headers(
        _openai_url(),
        {"api-key": AZURE_OPENAI_API_KEY, "Content-Type": "application/json"},
        payload,
        timeout=60,
    )
    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError("Azure OpenAI returned no choices.")
    return parse_json_response_text((choices[0].get("message") or {}).get("content", ""))


def call_azure_openai_with_tools(system_prompt: str, user_message: str, tools: list[dict]) -> dict[str, Any]:
    """Call Azure OpenAI with tool definitions. Returns {tool_calls: [...], content: str}."""
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.1,
        "max_completion_tokens": 4000,
        "tools": tools,
        "tool_choice": "auto",
    }
    response = post_json_with_headers(
        _openai_url(),
        {"api-key": AZURE_OPENAI_API_KEY, "Content-Type": "application/json"},
        payload,
        timeout=60,
    )
    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError("Azure OpenAI returned no choices.")
    message = choices[0].get("message") or {}
    return {
        "tool_calls": message.get("tool_calls") or [],
        "content": message.get("content") or "",
    }


def _build_ai_analysis_prompt() -> str:
    return f"""\
You are an expert software engineer and Azure operator analyzing Azure App Insights error logs.

Explain each error in plain English. For infrastructure failures decide the exact az CLI \
command to fix it. For code bugs set is_code_change = true and leave fix_command empty.

Environment:
  resource_group  : {AZURE_RESOURCE_GROUP or "unknown"}
  webapp_name     : {WEBAPP_NAME or "unknown"}
  function_app    : {FUNCTION_APP_NAME or "unknown"}
  postgres_server : {AZURE_POSTGRES_SERVER_NAME or "unknown"}

Return JSON exactly matching this schema:
{{
  "issues": [
    {{
      "issue_id": "err-N",
      "title": "Short plain-English title (max 10 words)",
      "what_happened": "One sentence: what broke, in simple terms",
      "why_it_occurred": "One sentence: the root cause",
      "affected_resource": "The specific service, resource, or component that failed",
      "severity": "critical|high|medium|low",
      "code_location": {{
        "file_path": "",
        "line_number": 0,
        "function_name": ""
      }},
      "suggested_fix": {{
        "description": "Plain English: what to change to fix this",
        "is_code_change": false,
        "fix_command": ""
      }}
    }}
  ]
}}

Rules:
- Simple words. "The database ran out of connections" not "Connection pool exhaustion occurred".
- code_location.file_path must come from the error stack trace only — never guess.
- If file_path is from a third-party library (site-packages, /usr/local/lib) it is NOT application code — is_code_change must be false.
- Some issues include a "code_snippet" field with actual source code. Use it to write a precise fix description referencing real variable names.
- is_code_change = true for application code bugs (KeyError, AttributeError, TypeError, ValueError, IndexError, missing field, null check). Leave fix_command empty.
- is_code_change = false for infrastructure failures. Set fix_command to a complete az CLI command.
- fix_command examples: "az webapp restart --resource-group <rg> --name <app>" or "az postgres flexible-server restart --resource-group <rg> --name <server>"
- If transient, self-healing, or third-party library error with no actionable fix, leave fix_command empty.
- severity: critical = users cannot use the app, high = intermittent failures, medium = degraded, low = no user impact."""


# ─── Defender Recommendations Agent ──────────────────────────────────────────

_AI_DEFENDER_PROMPT = f"""\
You are an Azure security engineer analyzing Microsoft Defender for Cloud recommendations.

For each unhealthy recommendation decide the exact az CLI command or ARM REST call \
to remediate it. Be specific — use the resource names and IDs provided.

Environment:
  subscription_id : {AZURE_SUBSCRIPTION_ID or "unknown"}
  resource_group  : {AZURE_RESOURCE_GROUP or "unknown"}

Return JSON exactly matching this schema:
{{
  "recommendations": [
    {{
      "rec_id": "rec-N",
      "title": "Short plain-English title (max 10 words)",
      "what_happened": "One sentence: what the security gap is",
      "why_it_matters": "One sentence: the risk if not fixed",
      "affected_resource": "resource name",
      "severity": "critical|high|medium|low",
      "suggested_fix": {{
        "description": "Plain English: what to do to fix this",
        "fix_command": "complete az CLI command to remediate, or empty if no az command applies"
      }}
    }}
  ]
}}

Rules:
- fix_command must be a complete, runnable az CLI command using the exact resource names from the input.
- If the fix requires portal or manual steps only, leave fix_command empty and describe it in description.
- severity mapping: High -> high, Medium -> medium, Low -> low."""


def load_defender_recommendations() -> list[dict[str, Any]]:
    """Fetch unhealthy Defender for Cloud assessments via REST API."""
    if not AZURE_SUBSCRIPTION_ID:
        return []
    token = run_azure_cli_access_token("https://management.azure.com/")
    url = (
        f"https://management.azure.com/subscriptions/{AZURE_SUBSCRIPTION_ID}"
        f"/providers/Microsoft.Security/assessments?api-version=2021-06-01"
    )
    response = post_json_with_headers.__wrapped__(url, {"Authorization": f"Bearer {token}"}) \
        if hasattr(post_json_with_headers, "__wrapped__") else None
    # Use urllib directly
    req = urllib_request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib_request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Defender API failed: {exc}") from exc

    items = data.get("value") or []
    unhealthy = []
    for item in items:
        props = item.get("properties") or {}
        status = (props.get("status") or {}).get("code", "")
        if status != "Unhealthy":
            continue
        resource_details = props.get("resourceDetails") or {}
        unhealthy.append({
            "rec_id": item.get("name", ""),
            "display_name": props.get("displayName", ""),
            "status": status,
            "resource_id": resource_details.get("Id", ""),
            "resource_name": resource_details.get("ResourceName", ""),
            "resource_type": resource_details.get("ResourceType", ""),
            "status_change_date": (props.get("status") or {}).get("statusChangeDate", ""),
        })
    return unhealthy


def analyze_defender_with_ai(recommendations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not recommendations or not is_ai_configured():
        return {}
    result = call_azure_openai(
        _AI_DEFENDER_PROMPT,
        json_dumps_compact({"recommendations": recommendations}),
    )
    return {
        sanitize_text(item.get("rec_id")): item
        for item in (result.get("recommendations") or [])
        if isinstance(item, dict) and item.get("rec_id")
    }


# ─── Policy Compliance Agent ──────────────────────────────────────────────────

_AI_POLICY_PROMPT = f"""\
You are an Azure compliance engineer analyzing Azure Policy non-compliant resources.

For each policy violation decide the exact az CLI command to bring the resource \
into compliance. Be specific — use the exact resource names and IDs provided.

Environment:
  subscription_id : {AZURE_SUBSCRIPTION_ID or "unknown"}
  resource_group  : {AZURE_RESOURCE_GROUP or "unknown"}

Return JSON exactly matching this schema:
{{
  "violations": [
    {{
      "viol_id": "pol-N",
      "title": "Short plain-English title (max 10 words)",
      "what_failed": "One sentence: what policy rule the resource is breaking",
      "why_it_matters": "One sentence: the compliance or security risk",
      "affected_resource": "resource name",
      "severity": "critical|high|medium|low",
      "suggested_fix": {{
        "description": "Plain English: what needs to change to comply",
        "fix_command": "complete az CLI command to remediate, or empty if not applicable"
      }}
    }}
  ]
}}

Rules:
- fix_command must be a complete, runnable az CLI command using the exact resource IDs from the input.
- Use az resource update, az webapp update, az storage account update, etc. as appropriate.
- If the fix cannot be done via az CLI, leave fix_command empty and explain in description.
- severity: map based on security impact — auth/encryption policies = high, diagnostic logs = low."""


def load_policy_violations() -> list[dict[str, Any]]:
    """Fetch non-compliant policy states via az CLI."""
    if not AZURE_RESOURCE_GROUP:
        return []
    raw = run_azure_cli_json([
        "policy", "state", "list",
        "--resource-group", AZURE_RESOURCE_GROUP,
        "--filter", "complianceState eq 'NonCompliant'",
    ])
    if not isinstance(raw, list):
        return []
    seen: set[tuple[str, str]] = set()
    violations = []
    for item in raw:
        policy_name = str(item.get("policyDefinitionName", "") or "")
        resource_id = str(item.get("resourceId", "") or "")
        key = (policy_name, resource_id)
        if key in seen or not policy_name or not resource_id:
            continue
        seen.add(key)
        violations.append({
            "policy_definition_name": policy_name,
            "policy_definition_reference_id": str(item.get("policyDefinitionReferenceId", "") or ""),
            "policy_action": str(item.get("policyDefinitionAction", "") or ""),
            "resource_id": resource_id,
            "resource_type": str(item.get("resourceType", "") or ""),
            "resource_group": str(item.get("resourceGroup", "") or ""),
            "timestamp": str(item.get("timestamp", "") or ""),
        })
    return violations


def analyze_policy_with_ai(violations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not violations or not is_ai_configured():
        return {}
    # Batch: AI analyses up to 20 at a time
    batched = violations[:20]
    indexed = [{"index": i + 1, **v} for i, v in enumerate(batched)]
    result = call_azure_openai(
        _AI_POLICY_PROMPT,
        json_dumps_compact({"violations": indexed}),
    )
    return {
        sanitize_text(item.get("viol_id")): item
        for item in (result.get("violations") or [])
        if isinstance(item, dict) and item.get("viol_id")
    }


def normalize_repo_file_path(file_path: str) -> str:
    """Convert an absolute local path to a repo-relative path.

    Strips any leading absolute path prefix up to and including known repo root
    markers (src/, tests/, functions/, infra/, scripts/, etc.).  Falls back to
    stripping the longest common prefix with known top-level directories so the
    GitHub API receives a relative path like 'tests/simulate_error.py'.
    """
    if not file_path:
        return file_path
    # Already relative
    if not file_path.startswith("/"):
        return file_path.lstrip("./")
    import re as _re
    # Find the first known top-level directory in the path and return from there
    match = _re.search(r"(?:^|/)((src|tests|functions|infra|scripts|liquibase|desktop)/.*)", file_path)
    if match:
        return match.group(1)
    # Fallback: strip everything before the last occurrence of a .py/.js/.ts etc.
    # by finding the shortest suffix that looks like a relative path
    parts = file_path.lstrip("/").split("/")
    # Drop parts that look like absolute system path components (Users, home, etc.)
    for i, part in enumerate(parts):
        if part in ("src", "tests", "functions", "infra", "scripts", "app", "lib"):
            return "/".join(parts[i:])
    # Last resort: just strip the leading slash
    return file_path.lstrip("/")


def _extract_file_line_from_row(row: dict[str, Any]) -> tuple[str, int]:
    """Best-effort extract file path and line number from error details/message."""
    details = str(row.get("ExampleDetails", "") or "")
    message = str(row.get("ExampleMessage", "") or "")
    text = details or message
    import re
    # Match "File: path/to/file.py:34" or 'file "path.py", line 34' patterns
    for pattern in [
        r'[Ff]ile[:\s]+["\']?([^\s"\']+\.py)["\']?,?\s*[Ll]ine[:\s]+(\d+)',
        r'([^\s"\']+\.py)[:\s]+(\d+)',
    ]:
        m = re.search(pattern, text)
        if m:
            return m.group(1), int(m.group(2))
    return "", 0


def github_get_snippet(file_path: str, line_number: int, context: int = 20) -> str:
    """Fetch +/-context lines around line_number from a file in the GitHub repo.
    Returns an empty string if the file cannot be fetched or GitHub is not configured."""
    from github_utils import is_github_dashboard_configured, github_get_file
    if not is_github_dashboard_configured() or not file_path:
        return ""
    rel_path = normalize_repo_file_path(file_path)
    try:
        content, _ = github_get_file(rel_path)
        lines = content.splitlines()
        start = max(0, line_number - context - 1)
        end = min(len(lines), line_number + context)
        snippet_lines = [f"{start + 1 + j}: {l}" for j, l in enumerate(lines[start:end])]
        return "\n".join(snippet_lines)
    except Exception:
        return ""


def analyze_errors_with_ai(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not rows or not is_ai_configured():
        return {}

    issues_payload = []
    for i, row in enumerate(rows, start=1):
        issue: dict[str, Any] = {
            "issue_id": f"err-{i}",
            "source": row.get("SourceTable", ""),
            "error_type": row.get("ErrorType", ""),
            "message": sanitize_text(row.get("ExampleMessage", "")),
            "details": sanitize_text(row.get("ExampleDetails", "")),
            "occurrences": int(row.get("Count", 0) or 0),
            "first_seen": str(row.get("FirstSeen", "") or ""),
            "last_seen": str(row.get("LastSeen", "") or ""),
        }
        # Attach targeted code snippet if we can find a file/line in the stack trace
        file_path, line_number = _extract_file_line_from_row(row)
        if file_path and line_number:
            snippet = github_get_snippet(file_path, line_number)
            if snippet:
                issue["code_snippet"] = f"# {file_path} (around line {line_number})\n{snippet}"
        issues_payload.append(issue)

    result = call_azure_openai(_build_ai_analysis_prompt(), json_dumps_compact({"issues": issues_payload}))
    return {
        sanitize_text(item.get("issue_id")): item
        for item in (result.get("issues") or [])
        if isinstance(item, dict) and item.get("issue_id")
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

    # AI diagnosis (best-effort — falls back to rules if AI not configured or fails)
    ai_by_id: dict[str, dict[str, Any]] = {}
    if rows:
        try:
            ai_by_id = analyze_errors_with_ai(rows)
        except Exception as exc:
            logger.warning("AI error analysis failed, using rule-based fallback: %s", exc)

    items = []
    for index, row in enumerate(rows, start=1):
        issue_id = f"err-{index}"
        source_table = str(row.get("SourceTable", ""))
        error_type = str(row.get("ErrorType", ""))
        message = str(row.get("ExampleMessage", ""))
        count = int(row.get("Count", 0) or 0)
        ai = ai_by_id.get(issue_id, {})

        # Code location: prefer AI-extracted (from stack trace analysis), fall back to regex
        ai_loc = ai.get("code_location") or {}
        file_path = sanitize_text(ai_loc.get("file_path", "")) or ""
        line_number = str(ai_loc.get("line_number") or "")
        function_name = sanitize_text(ai_loc.get("function_name", ""))
        if not file_path or not line_number or line_number == "0":
            fallback_file, fallback_line = extract_code_location(str(row.get("ExampleDetails", "")))
            file_path = file_path or fallback_file
            line_number = line_number if (line_number and line_number != "0") else fallback_line

        severity = sanitize_text(ai.get("severity", "")).lower()
        if severity not in {"critical", "high", "medium", "low"}:
            severity = infer_error_severity(source_table, message, count)

        ai_fix = ai.get("suggested_fix") or {}
        fix_command = sanitize_text(ai_fix.get("fix_command") or "")
        example_resource_id = str(row.get("ExampleResourceId", "") or "")
        affected_resources = dedupe_resources(
            [{"name": resource_name_from_id(example_resource_id), "kind": "telemetry", "id": example_resource_id}]
            if example_resource_id else []
        )

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
                "title": sanitize_text(ai.get("title")) or message[:80],
                "what_happened": sanitize_text(ai.get("what_happened")) or message,
                "why_it_occurred": sanitize_text(ai.get("why_it_occurred")) or "",
                "affected_resource": sanitize_text(ai.get("affected_resource")) or "",
                "role": classify_issue_role(message),
                "operation_id": row.get("ExampleOperationId", ""),
                "severity": severity,
                "severity_score": {"critical": 9, "high": 8, "medium": 5, "low": 3}.get(severity, 5),
                "severity_rank": severity_rank(severity),
                "where_it_occurred": {
                    "file_path": file_path,
                    "line_number": line_number,
                    "function_name": function_name,
                },
                "ai_fix": {
                    "description": sanitize_text(ai_fix.get("description")) or "",
                    "is_code_change": bool(ai_fix.get("is_code_change") and file_path and line_number and line_number != "0"),
                    "fix_command": fix_command,
                },
                "affected_resources": affected_resources,
                "ai_available": bool(ai),
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
        try:
            server_info = get_cached_postgres_platform_action("show-server")
            state = str((server_info or {}).get("state") or "").lower()
        except Exception:
            state = ""
        if state == "stopped":
            run_postgres_platform_action("start-server")
            return f"Start requested for {AZURE_POSTGRES_SERVER_NAME} (server was stopped, start used instead of restart)."
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


def load_error_operations_context(
    request,
    msg: str = "",
    error: str = "",
    minutes: int = 60,
    tab: str = "errors",
):
    from auth_helpers import require_admin_session
    from github_utils import is_github_dashboard_configured
    from fastapi.responses import RedirectResponse

    session_admin = require_admin_session(request)
    if not session_admin:
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    issues: list[dict[str, Any]] = []
    issue_overview = build_issue_overview([])
    defender_items: list[dict[str, Any]] = []
    policy_items: list[dict[str, Any]] = []
    scan_error = error

    if tab == "errors" and is_error_operations_configured():
        try:
            issues = load_recent_error_operations(minutes=minutes)
            issue_overview = build_issue_overview(issues)
        except Exception as exc:
            record_exception_to_telemetry(exc, action="scan_recent_errors")
            scan_error = str(exc)

    elif tab == "defender":
        try:
            raw_recs = load_defender_recommendations()
            ai_recs = analyze_defender_with_ai(raw_recs)
            for i, rec in enumerate(raw_recs, start=1):
                rec_id = f"rec-{i}"
                ai = ai_recs.get(rec_id, {})
                ai_fix = (ai.get("suggested_fix") or {})
                defender_items.append({
                    "id": rec_id,
                    "raw_id": rec.get("rec_id", ""),
                    "title": sanitize_text(ai.get("title")) or rec.get("display_name", ""),
                    "display_name": rec.get("display_name", ""),
                    "what_happened": sanitize_text(ai.get("what_happened")) or rec.get("display_name", ""),
                    "why_it_matters": sanitize_text(ai.get("why_it_matters")) or "",
                    "affected_resource": sanitize_text(ai.get("affected_resource")) or rec.get("resource_name", ""),
                    "resource_id": rec.get("resource_id", ""),
                    "resource_type": rec.get("resource_type", ""),
                    "severity": sanitize_text(ai.get("severity") or "medium").lower(),
                    "status_change_date": rec.get("status_change_date", ""),
                    "ai_fix": {
                        "description": sanitize_text(ai_fix.get("description")) or "",
                        "fix_command": sanitize_text(ai_fix.get("fix_command") or ""),
                    },
                    "ai_available": bool(ai),
                })
        except Exception as exc:
            record_exception_to_telemetry(exc, action="scan_defender")
            scan_error = str(exc)

    elif tab == "policy":
        try:
            raw_viols = load_policy_violations()
            ai_viols = analyze_policy_with_ai(raw_viols)
            for i, viol in enumerate(raw_viols, start=1):
                viol_id = f"pol-{i}"
                ai = ai_viols.get(viol_id, {})
                ai_fix = (ai.get("suggested_fix") or {})
                policy_items.append({
                    "id": viol_id,
                    "title": sanitize_text(ai.get("title")) or viol.get("policy_definition_reference_id", ""),
                    "what_failed": sanitize_text(ai.get("what_failed")) or "",
                    "why_it_matters": sanitize_text(ai.get("why_it_matters")) or "",
                    "affected_resource": sanitize_text(ai.get("affected_resource")) or resource_name_from_id(viol.get("resource_id", "")),
                    "resource_id": viol.get("resource_id", ""),
                    "resource_type": viol.get("resource_type", ""),
                    "policy_name": viol.get("policy_definition_name", ""),
                    "severity": sanitize_text(ai.get("severity") or "medium").lower(),
                    "timestamp": viol.get("timestamp", ""),
                    "ai_fix": {
                        "description": sanitize_text(ai_fix.get("description")) or "",
                        "fix_command": sanitize_text(ai_fix.get("fix_command") or ""),
                    },
                    "ai_available": bool(ai),
                })
        except Exception as exc:
            record_exception_to_telemetry(exc, action="scan_policy")
            scan_error = str(exc)

    from main import templates
    return templates.TemplateResponse(
        request,
        "admin_errors.html",
        {
            "request": request,
            "admin_email": session_admin["email"],
            "msg": msg,
            "error": scan_error,
            "tab": tab,
            "issues": issues,
            "issue_overview": issue_overview,
            "defender_items": defender_items,
            "policy_items": policy_items,
            "minutes": minutes,
            "error_ops_configured": is_error_operations_configured(),
            "ai_configured": is_ai_configured(),
            "github_configured": is_github_dashboard_configured(),
            "github_repository": GITHUB_REPOSITORY,
        },
    )
