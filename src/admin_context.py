import logging
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
import json

from fastapi.responses import RedirectResponse

from config import (
    AZURE_RESOURCE_GROUP,
    AZURE_SUBSCRIPTION_ID,
)
from telemetry import log_event, record_exception_to_telemetry
from db import (
    ensure_admin_schema,
    ensure_schema,
    get_admin_db_url,
    get_conn,
    get_db_url_for_org,
    get_managed_database_entries,
)
from azure_utils import run_azure_cli_access_token, run_azure_cli_json
from auth_helpers import require_admin_session, require_user_session
from github_utils import is_github_dashboard_configured
from ai_analysis import (
    resource_name_from_id,
    sanitize_text,
)

logger = logging.getLogger(__name__)

POLICY_REMEDIATION_MAP: dict[str, dict[str, Any]] = {
    "pranathi-appservice-https-only": {
        "label": "Enable HTTPS-only on App Service",
        "risk": "low",
        "arm_patch": {
            "resource_type": "Microsoft.Web/sites",
            "api_version": "2022-03-01",
            "body": {"properties": {"httpsOnly": True}},
        },
    },
    "pranathi-appservice-min-tls": {
        "label": "Set minimum TLS 1.2 on App Service",
        "risk": "low",
        "arm_patch": {
            "resource_type": "Microsoft.Web/sites/config",
            "api_version": "2022-03-01",
            "body": {"properties": {"minTlsVersion": "1.2"}},
        },
    },
    "pranathi-keyvault-purge-protection": {
        "label": "Enable purge protection on Key Vault",
        "risk": "low",
        "arm_patch": {
            "resource_type": "Microsoft.KeyVault/vaults",
            "api_version": "2022-07-01",
            "body": {"properties": {"enablePurgeProtection": True}},
        },
    },
    "pranathi-keyvault-soft-delete": {
        "label": "Enable soft delete on Key Vault",
        "risk": "low",
        "arm_patch": {
            "resource_type": "Microsoft.KeyVault/vaults",
            "api_version": "2022-07-01",
            "body": {"properties": {"enableSoftDelete": True}},
        },
    },
    "pranathi-storage-https-only": {
        "label": "Enforce HTTPS-only on Storage Account",
        "risk": "low",
        "arm_patch": {
            "resource_type": "Microsoft.Storage/storageAccounts",
            "api_version": "2022-09-01",
            "body": {"properties": {"supportsHttpsTrafficOnly": True}},
        },
    },
    "pranathi-storage-no-public-access": {
        "label": "Disable public blob access on Storage Account",
        "risk": "low",
        "arm_patch": {
            "resource_type": "Microsoft.Storage/storageAccounts",
            "api_version": "2022-09-01",
            "body": {"properties": {"allowBlobPublicAccess": False}},
        },
    },
    "pranathi-acr-admin-disabled": {
        "label": "Disable admin user on Container Registry",
        "risk": "low",
        "arm_patch": {
            "resource_type": "Microsoft.ContainerRegistry/registries",
            "api_version": "2023-01-01-preview",
            "body": {"properties": {"adminUserEnabled": False}},
        },
    },
}

POLICY_SEVERITY_MAP = {
    "pranathi-appservice-https-only": "high",
    "pranathi-appservice-min-tls": "medium",
    "pranathi-keyvault-purge-protection": "high",
    "pranathi-keyvault-soft-delete": "high",
    "pranathi-storage-https-only": "high",
    "pranathi-storage-no-public-access": "high",
    "pranathi-acr-admin-disabled": "medium",
}


def load_policy_compliance() -> list[dict[str, Any]]:
    from azure_utils import ensure_azure_cli_login
    ensure_azure_cli_login()
    raw = run_azure_cli_json([
        "policy", "state", "list",
        "--resource-group", AZURE_RESOURCE_GROUP,
        "--filter", "complianceState eq 'NonCompliant'",
        "--select", "resourceId,policyDefinitionName,policyDefinitionDisplayName,complianceState,timestamp,resourceType",
    ])
    if not isinstance(raw, list):
        return []

    findings = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        policy_name = sanitize_text(item.get("policyDefinitionName", ""))
        resource_id = sanitize_text(item.get("resourceId", ""))
        key = (policy_name, resource_id)
        if key in seen or not policy_name or not resource_id:
            continue
        seen.add(key)

        remediation = POLICY_REMEDIATION_MAP.get(policy_name)
        if not remediation:
            continue

        findings.append({
            "policy_name": policy_name,
            "display_name": sanitize_text(item.get("policyDefinitionDisplayName", policy_name)),
            "resource_id": resource_id,
            "resource_name": resource_name_from_id(resource_id),
            "resource_type": sanitize_text(item.get("resourceType", "")),
            "severity": POLICY_SEVERITY_MAP.get(policy_name, "medium"),
            "timestamp": sanitize_text(item.get("timestamp", "")),
            "fix_label": remediation["label"],
            "fix_risk": remediation["risk"],
            "arm_patch": remediation["arm_patch"],
        })

    findings.sort(key=lambda f: {"high": 0, "medium": 1, "low": 2}.get(f["severity"], 3))
    return findings


def apply_policy_fix(policy_name: str, resource_id: str) -> str:
    remediation = POLICY_REMEDIATION_MAP.get(policy_name)
    if not remediation:
        raise RuntimeError(f"No remediation defined for policy: {policy_name}")

    token = run_azure_cli_access_token("https://management.azure.com/")
    url = f"https://management.azure.com{resource_id}?api-version={remediation['arm_patch']['api_version']}"
    req = urllib_request.Request(
        url,
        data=json.dumps(remediation["arm_patch"]["body"]).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="PATCH",
    )
    try:
        with urllib_request.urlopen(req, timeout=30) as response:
            response.read()
    except urllib_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"ARM remediation failed: HTTP {exc.code} {body}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"Network error during ARM remediation: {exc.reason}") from exc

    return f"{remediation['label']} applied to {resource_name_from_id(resource_id)}."


def load_dashboard_context(request, msg: str = "", error: str = ""):
    session_user = require_user_session(request)
    if not session_user:
        return RedirectResponse("/login?msg=Please+log+in+to+continue", status_code=303)

    from main import templates
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


def load_admin_dashboard_context(request, msg: str = "", error: str = ""):
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

    from main import templates
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
