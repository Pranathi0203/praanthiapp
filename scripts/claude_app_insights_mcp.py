#!/usr/bin/env python3
import json
import os
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from mcp.server.fastmcp import FastMCP


SERVER_NAME = "pranathi-app-insights"
SERVER_INSTRUCTIONS = (
    "Read-only App Insights diagnostics server for recent error analysis. "
    "Use your knowledge of Azure, Python, PostgreSQL, Service Bus, IoT Hub, "
    "and Event Hub to reason about root causes and suggest fixes. "
    "When remediation tools are available, always ask the user for approval "
    "before applying a fix."
)

mcp = FastMCP(name=SERVER_NAME, instructions=SERVER_INSTRUCTIONS)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def require_env(names: List[str]) -> None:
    missing = [name for name in names if not get_env(name)]
    if missing:
        raise RuntimeError("Missing required environment variables: {}".format(", ".join(missing)))


def bool_env(name: str, default: bool = False) -> bool:
    raw = get_env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def run_az_json(args: List[str]) -> Any:
    command = ["az"] + args + ["-o", "json"]
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("Azure CLI (`az`) is not installed or not on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or "Unknown Azure CLI error."
        raise RuntimeError("Azure CLI command failed: {}\n{}".format(" ".join(command), detail)) from exc

    try:
        return json.loads(completed.stdout or "null")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Azure CLI returned non-JSON output for: {}".format(" ".join(command))) from exc


def run_az_command(args: List[str]) -> Dict[str, Any]:
    command = ["az"] + args + ["-o", "json"]
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("Azure CLI (`az`) is not installed or not on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or "Unknown Azure CLI error."
        raise RuntimeError("Azure CLI command failed: {}\n{}".format(" ".join(command), detail)) from exc

    stdout = (completed.stdout or "").strip()
    if not stdout:
        return {"status": "ok"}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"status": "ok", "raw_output": stdout}


def get_access_token(resource: str) -> str:
    token_payload = run_az_json(["account", "get-access-token", "--resource", resource])
    token = token_payload.get("accessToken", "")
    if not token:
        raise RuntimeError("Azure CLI did not return an access token for resource {}.".format(resource))
    return token


def arm_get(resource_id: str, api_version: str) -> Dict[str, Any]:
    token = get_access_token("https://management.azure.com/")
    url = "https://management.azure.com{}?api-version={}".format(
        resource_id, urllib_parse.quote(api_version, safe="")
    )
    req = urllib_request.Request(
        url,
        headers={
            "Authorization": "Bearer {}".format(token),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib_request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError("ARM request failed for {}: HTTP {} {}".format(resource_id, exc.code, body)) from exc


def post_json(url: str, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=body,
        headers={
            "Authorization": "Bearer {}".format(token),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError("Log query failed: HTTP {} {}".format(exc.code, detail)) from exc


def get_workspace_context() -> Dict[str, str]:
    workspace_id = get_env("LOG_ANALYTICS_WORKSPACE_ID")
    if workspace_id:
        return {
            "workspace_id": workspace_id,
            "workspace_resource_id": get_env("LOG_ANALYTICS_WORKSPACE_RESOURCE_ID"),
            "app_insights_name": get_env("APPLICATION_INSIGHTS_NAME") or get_env("APP_INSIGHTS_NAME"),
            "resource_group": get_env("AZURE_RESOURCE_GROUP"),
            "subscription_id": get_env("AZURE_SUBSCRIPTION_ID"),
        }

    app_insights_name = get_env("APPLICATION_INSIGHTS_NAME") or get_env("APP_INSIGHTS_NAME")
    resource_group = get_env("AZURE_RESOURCE_GROUP")
    subscription_id = get_env("AZURE_SUBSCRIPTION_ID")
    require_env(["AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP"])
    if not app_insights_name:
        raise RuntimeError(
            "Set either LOG_ANALYTICS_WORKSPACE_ID or APPLICATION_INSIGHTS_NAME/APP_INSIGHTS_NAME."
        )

    component_resource_id = (
        "/subscriptions/{}/resourceGroups/{}/providers/Microsoft.Insights/components/{}".format(
            subscription_id, resource_group, app_insights_name
        )
    )
    component = arm_get(component_resource_id, "2020-02-02")
    properties = component.get("properties", {})
    workspace_resource_id = (
        properties.get("WorkspaceResourceId")
        or properties.get("workspaceResourceId")
        or get_env("LOG_ANALYTICS_WORKSPACE_RESOURCE_ID")
    )
    if not workspace_resource_id:
        raise RuntimeError(
            "The Application Insights resource is not linked to a Log Analytics workspace, or the workspace id could not be resolved."
        )

    workspace = arm_get(workspace_resource_id, "2022-10-01")
    customer_id = (
        workspace.get("properties", {}).get("customerId")
        or workspace.get("properties", {}).get("customerID")
        or get_env("LOG_ANALYTICS_WORKSPACE_ID")
    )
    if not customer_id:
        raise RuntimeError("Could not resolve the Log Analytics workspace customer id.")

    return {
        "workspace_id": customer_id,
        "workspace_resource_id": workspace_resource_id,
        "app_insights_name": app_insights_name,
        "resource_group": resource_group,
        "subscription_id": subscription_id,
    }


def get_fix_context() -> Dict[str, Any]:
    return {
        "subscription_id": get_env("AZURE_SUBSCRIPTION_ID"),
        "resource_group": get_env("AZURE_RESOURCE_GROUP"),
        "webapp_name": get_env("WEBAPP_NAME"),
        "function_app_name": get_env("FUNCTION_APP_NAME"),
        "postgres_server_name": get_env("AZURE_POSTGRES_SERVER_NAME"),
        "allow_webapp_restart": bool_env("ALLOW_WEBAPP_RESTART", True),
        "allow_functionapp_restart": bool_env("ALLOW_FUNCTIONAPP_RESTART", True),
        "allow_postgres_restart": bool_env("ALLOW_POSTGRES_RESTART", True),
    }


def sanitize_text(value: str) -> str:
    return " ".join((value or "").strip().split())


def classify_fix_candidate(source_table: str, error_type: str, message: str) -> Dict[str, Any]:
    haystack = " ".join(
        [
            sanitize_text(source_table).lower(),
            sanitize_text(error_type).lower(),
            sanitize_text(message).lower(),
        ]
    )

    postgres_signals = [
        "psycopg",
        "postgres",
        "pgsqlserverlogs",
        "too many connections",
        "connection refused",
        "could not connect",
        "connection timeout",
        "terminating connection",
        "the server is not in active state",
        "server is stopped",
        "stopped state",
    ]
    function_signals = [
        "functionapplogs",
        "azure functions",
        "function host",
        "listener was unable",
        "service bus",
        "event hub",
        "iothub",
        "worker process started and initialized",
    ]
    webapp_signals = [
        "appexceptions",
        "apptraces",
        "fastapi",
        "uvicorn",
        "gunicorn",
        "500",
        "internal server error",
    ]

    if any(signal in haystack for signal in postgres_signals):
        return {
            "fix_type": "start_postgres_server",
            "component": "Azure PostgreSQL Flexible Server",
            "reason": "The error looks database-availability related, so starting the PostgreSQL server is the safest first response when the server may be stopped or unavailable.",
            "automation_safe": True,
            "risk": "medium",
        }

    if any(signal in haystack for signal in function_signals):
        return {
            "fix_type": "restart_function_app",
            "component": "Azure Function App",
            "reason": "The error appears to come from the event-driven pipeline or Functions runtime, so restarting the Function App is the safest operational reset.",
            "automation_safe": True,
            "risk": "medium",
        }

    if any(signal in haystack for signal in webapp_signals):
        return {
            "fix_type": "restart_webapp",
            "component": "Azure App Service Web App",
            "reason": "The error appears to come from the web application runtime, so restarting the web app is a bounded remediation step.",
            "automation_safe": True,
            "risk": "medium",
        }

    return {
        "fix_type": "manual_investigation",
        "component": "unknown",
        "reason": "The current heuristics could not map this error to a safe automated remediation.",
        "automation_safe": False,
        "risk": "unknown",
    }


def ensure_fix_allowed(fix_type: str, context: Dict[str, Any]) -> None:
    require_env(["AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP"])

    if fix_type == "restart_webapp":
        if not context["webapp_name"]:
            raise RuntimeError("WEBAPP_NAME is required for restart_webapp.")
        if not context["allow_webapp_restart"]:
            raise RuntimeError("restart_webapp is disabled by ALLOW_WEBAPP_RESTART.")
        return

    if fix_type == "restart_function_app":
        if not context["function_app_name"]:
            raise RuntimeError("FUNCTION_APP_NAME is required for restart_function_app.")
        if not context["allow_functionapp_restart"]:
            raise RuntimeError("restart_function_app is disabled by ALLOW_FUNCTIONAPP_RESTART.")
        return

    if fix_type == "restart_postgres_server":
        if not context["postgres_server_name"]:
            raise RuntimeError("AZURE_POSTGRES_SERVER_NAME is required for restart_postgres_server.")
        if not context["allow_postgres_restart"]:
            raise RuntimeError("restart_postgres_server is disabled by ALLOW_POSTGRES_RESTART.")
        return

    if fix_type == "start_postgres_server":
        if not context["postgres_server_name"]:
            raise RuntimeError("AZURE_POSTGRES_SERVER_NAME is required for start_postgres_server.")
        if not context["allow_postgres_restart"]:
            raise RuntimeError("start_postgres_server is disabled by ALLOW_POSTGRES_RESTART.")
        return

    raise RuntimeError("Unsupported or unsafe fix type: {}".format(fix_type))


def restart_webapp(context: Dict[str, Any]) -> Dict[str, Any]:
    return run_az_command(
        [
            "webapp",
            "restart",
            "--subscription",
            context["subscription_id"],
            "--resource-group",
            context["resource_group"],
            "--name",
            context["webapp_name"],
        ]
    )


def restart_function_app(context: Dict[str, Any]) -> Dict[str, Any]:
    return run_az_command(
        [
            "functionapp",
            "restart",
            "--subscription",
            context["subscription_id"],
            "--resource-group",
            context["resource_group"],
            "--name",
            context["function_app_name"],
        ]
    )


def restart_postgres_server(context: Dict[str, Any]) -> Dict[str, Any]:
    return run_az_command(
        [
            "postgres",
            "flexible-server",
            "restart",
            "--subscription",
            context["subscription_id"],
            "--resource-group",
            context["resource_group"],
            "--name",
            context["postgres_server_name"],
        ]
    )


def start_postgres_server(context: Dict[str, Any]) -> Dict[str, Any]:
    return run_az_command(
        [
            "postgres",
            "flexible-server",
            "start",
            "--subscription",
            context["subscription_id"],
            "--resource-group",
            context["resource_group"],
            "--name",
            context["postgres_server_name"],
        ]
    )


def apply_fix_operation(fix_type: str, context: Dict[str, Any]) -> Dict[str, Any]:
    if fix_type == "restart_webapp":
        return restart_webapp(context)
    if fix_type == "restart_function_app":
        return restart_function_app(context)
    if fix_type == "restart_postgres_server":
        return restart_postgres_server(context)
    if fix_type == "start_postgres_server":
        return start_postgres_server(context)
    raise RuntimeError("Unsupported fix type: {}".format(fix_type))


def build_union_query(minutes: int) -> str:
    window = max(1, minutes)
    return """
let windowStart = ago({window}m);
union isfuzzy=true
(
    AppExceptions
    | where TimeGenerated >= windowStart
    | project TimeGenerated, SourceTable="AppExceptions", Severity="Error", ErrorType=coalesce(Type, InnermostType, ProblemId), ErrorKey=coalesce(ProblemId, Type, InnermostType, OuterMessage), Message=coalesce(OuterMessage, InnermostMessage, Message), OperationId=OperationId, ResourceId=_ResourceId
),
(
    AppTraces
    | where TimeGenerated >= windowStart
    | where SeverityLevel >= 3 or Message has_any ("error", "exception", "failed", "timeout", "deadlock", "forbidden", "unauthorized")
    | project TimeGenerated, SourceTable="AppTraces", Severity=tostring(SeverityLevel), ErrorType=tostring(column_ifexists("customDimensions", dynamic({{}})).event), ErrorKey=coalesce(tostring(column_ifexists("customDimensions", dynamic({{}})).event), Message), Message=Message, OperationId=OperationId, ResourceId=_ResourceId
),
(
    FunctionAppLogs
    | where TimeGenerated >= windowStart
    | where tostring(column_ifexists("Level", "")) =~ "Error" or Message has_any ("error", "exception", "failed", "timeout", "deadlock")
    | project TimeGenerated, SourceTable="FunctionAppLogs", Severity=tostring(column_ifexists("Level", "")), ErrorType=tostring(column_ifexists("Level", "")), ErrorKey=coalesce(Message, tostring(column_ifexists("Level", ""))), Message=Message, OperationId=tostring(column_ifexists("OperationId", "")), ResourceId=_ResourceId
),
(
    AzureDiagnostics
    | where TimeGenerated >= windowStart
    | where tostring(column_ifexists("Message", "")) has_any ("error", "exception", "failed", "timeout", "deadlock", "unauthorized", "forbidden")
        or tostring(column_ifexists("Level", "")) =~ "Error"
        or tostring(column_ifexists("status_s", "")) =~ "Failed"
        or tostring(column_ifexists("ResultType", "")) =~ "Failed"
    | project TimeGenerated, SourceTable="AzureDiagnostics", Severity=tostring(column_ifexists("Level", "")), ErrorType=tostring(column_ifexists("Category", "")), ErrorKey=coalesce(tostring(column_ifexists("Category", "")), tostring(column_ifexists("Message", ""))), Message=tostring(column_ifexists("Message", "")), OperationId=tostring(column_ifexists("OperationId", "")), ResourceId=_ResourceId
),
(
    PGSQLServerLogs
    | where TimeGenerated >= windowStart
    | where Message has_any ("error", "exception", "failed", "timeout", "deadlock", "too many connections", "lock wait")
    | project TimeGenerated, SourceTable="PGSQLServerLogs", Severity="Error", ErrorType="PGSQLServerLogs", ErrorKey=Message, Message=Message, OperationId="", ResourceId=_ResourceId
)
""".format(window=window)


def query_workspace(workspace_id: str, query: str) -> List[Dict[str, Any]]:
    token = get_access_token("https://api.loganalytics.io")
    url = "https://api.loganalytics.io/v1/workspaces/{}/query".format(workspace_id)
    response = post_json(url, token, {"query": query})
    tables = response.get("tables", [])
    if not tables:
        return []

    columns = [col.get("name") for col in tables[0].get("columns", [])]
    rows = tables[0].get("rows", [])
    results: List[Dict[str, Any]] = []
    for row in rows:
        item = {columns[index]: row[index] for index in range(min(len(columns), len(row)))}
        results.append(item)
    return results


def find_recent_errors_impl(minutes: int = 60, limit: int = 20) -> Dict[str, Any]:
    workspace = get_workspace_context()
    safe_limit = max(1, min(limit, 100))
    query = """
{union_query}
| summarize Count=count(), FirstSeen=min(TimeGenerated), LastSeen=max(TimeGenerated), ExampleMessage=any(Message), ExampleResourceId=any(ResourceId), ExampleOperationId=any(OperationId) by SourceTable, ErrorType, ErrorKey
| sort by Count desc, LastSeen desc
| take {limit}
""".format(union_query=build_union_query(minutes), limit=safe_limit)
    rows = query_workspace(workspace["workspace_id"], query)
    return {
        "generated_at": utc_now_iso(),
        "window_minutes": minutes,
        "workspace": workspace,
        "errors": rows,
    }


def analyze_recent_errors_impl(minutes: int = 60, limit: int = 20) -> Dict[str, Any]:
    recent = find_recent_errors_impl(minutes=minutes, limit=limit)
    return {
        "generated_at": utc_now_iso(),
        "window_minutes": minutes,
        "workspace": recent["workspace"],
        "error_count": len(recent["errors"]),
        "errors": recent["errors"],
        "analysis_guidance": (
            "Analyze the errors above using your knowledge of Azure and Python. "
            "For each error: identify the root cause, explain why it is happening, "
            "how errors relate to each other (e.g. one failure causing a cascade), "
            "and suggest specific fixes. "
            "Use SourceTable to understand origin (AppExceptions=unhandled exceptions, "
            "AppTraces=logger output, FunctionAppLogs=Azure Functions runtime, "
            "AzureDiagnostics=platform-level, PGSQLServerLogs=PostgreSQL). "
            "Use ErrorType and ExampleMessage as the primary signal for root cause. "
            "Use ExampleResourceId to identify which Azure resource failed. "
            "Correlate errors by timestamp (FirstSeen/LastSeen) to find cascades."
        ),
    }


def propose_fix_for_error_impl(
    source_table: str,
    error_type: str = "",
    example_message: str = "",
) -> Dict[str, Any]:
    source_table = sanitize_text(source_table)
    error_type = sanitize_text(error_type)
    example_message = sanitize_text(example_message)
    context = get_fix_context()
    proposal = classify_fix_candidate(source_table=source_table, error_type=error_type, message=example_message)
    fix_type = proposal["fix_type"]

    allowed = False
    blocked_reason = ""
    if proposal["automation_safe"]:
        try:
            ensure_fix_allowed(fix_type, context)
            allowed = True
        except RuntimeError as exc:
            blocked_reason = str(exc)

    return {
        "generated_at": utc_now_iso(),
        "input": {
            "source_table": source_table,
            "error_type": error_type,
            "example_message": example_message,
        },
        "proposal": proposal,
        "fix_available": allowed,
        "blocked_reason": blocked_reason,
        "approval_required": True,
        "operator_guidance": (
            "Ask the user whether they want to apply this fix before calling apply_fix_for_error."
        ),
    }


def apply_fix_for_error_impl(
    fix_type: str,
    confirmed: bool,
    source_table: str = "",
    error_type: str = "",
    example_message: str = "",
) -> Dict[str, Any]:
    normalized_fix_type = sanitize_text(fix_type)
    if not confirmed:
        raise RuntimeError(
            "Refusing to apply fix because confirmed=false. Ask the user for approval first."
        )

    context = get_fix_context()
    proposal = classify_fix_candidate(
        source_table=sanitize_text(source_table),
        error_type=sanitize_text(error_type),
        message=sanitize_text(example_message),
    )

    if proposal["fix_type"] != normalized_fix_type:
        raise RuntimeError(
            "Requested fix_type '{}' does not match the safest inferred fix '{}' for this error.".format(
                normalized_fix_type, proposal["fix_type"]
            )
        )

    ensure_fix_allowed(normalized_fix_type, context)
    action_result = apply_fix_operation(normalized_fix_type, context)

    return {
        "generated_at": utc_now_iso(),
        "status": "applied",
        "fix_type": normalized_fix_type,
        "component": proposal["component"],
        "reason": proposal["reason"],
        "resource_group": context["resource_group"],
        "subscription_id": context["subscription_id"],
        "action_result": action_result,
        "next_step_guidance": "Re-run error analysis after a few minutes to confirm the failure stopped recurring.",
    }


@mcp.tool(
    name="find_recent_errors",
    description="Read recent Application Insights / Log Analytics error signals and return grouped raw findings.",
)
def find_recent_errors(minutes: int = 60, limit: int = 20) -> str:
    return json.dumps(find_recent_errors_impl(minutes=minutes, limit=limit), indent=2)


@mcp.tool(
    name="analyze_recent_errors",
    description=(
        "Read recent Application Insights / Log Analytics error signals and return raw error data "
        "for Claude to analyze, identify root causes, correlate cascades, and suggest fixes."
    ),
)
def analyze_recent_errors(minutes: int = 60, limit: int = 20) -> str:
    return json.dumps(analyze_recent_errors_impl(minutes=minutes, limit=limit), indent=2)


@mcp.tool(
    name="propose_fix_for_error",
    description=(
        "Inspect an error signature and suggest the safest supported remediation. "
        "Use this before any fix. After reading the result, ask the user for approval before applying a fix."
    ),
)
def propose_fix_for_error(
    source_table: str,
    error_type: str = "",
    example_message: str = "",
) -> str:
    return json.dumps(
        propose_fix_for_error_impl(
            source_table=source_table,
            error_type=error_type,
            example_message=example_message,
        ),
        indent=2,
    )


@mcp.tool(
    name="apply_fix_for_error",
    description=(
        "Apply a supported remediation for an error after the user has explicitly approved it. "
        "Never call this tool until the user confirms they want the fix applied."
    ),
)
def apply_fix_for_error(
    fix_type: str,
    confirmed: bool,
    source_table: str = "",
    error_type: str = "",
    example_message: str = "",
) -> str:
    return json.dumps(
        apply_fix_for_error_impl(
            fix_type=fix_type,
            confirmed=confirmed,
            source_table=source_table,
            error_type=error_type,
            example_message=example_message,
        ),
        indent=2,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
