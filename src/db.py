import json
import logging
import os
from urllib.parse import urlparse, urlunparse

import psycopg
from fastapi import HTTPException

from config import (
    BASE_DIR,
    CONTOSO_DATABASE_URL,
    CONTOSO_DB_NAME,
    CONTOSO_DOMAIN,
    DATABASE_URL,
    LITWARE_DATABASE_URL,
    LITWARE_DB_NAME,
    LITWARE_DOMAIN,
    POSTGRES_ADMIN_DB,
    POSTGRES_ADMIN_URL,
    AZURE_SUBSCRIPTION_ID,
    AZURE_RESOURCE_GROUP,
    AZURE_POSTGRES_SERVER_NAME,
)
from telemetry import log_event, record_exception_to_telemetry

POSTGRES_ADMIN_SCRIPT_CANDIDATES = [
    os.path.join(BASE_DIR, "scripts", "postgres_admin.ps1"),
    os.path.join(os.path.dirname(BASE_DIR), "scripts", "postgres_admin.ps1"),
]


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


def load_database_dashboard_context(request, msg: str = "", error: str = ""):
    from auth_helpers import require_admin_session
    from ai_analysis import get_cached_postgres_platform_action
    from fastapi.responses import RedirectResponse

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

    from main import templates
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


def authenticate_employee(email: str, password: str) -> dict:
    from auth_helpers import hash_password
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
    from auth_helpers import hash_password
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
