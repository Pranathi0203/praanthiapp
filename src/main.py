import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from urllib.parse import urlparse, urlunparse

import psycopg
from azure.monitor.opentelemetry import configure_azure_monitor
from fastapi import FastAPI, Form, Header, HTTPException, Request
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
    from azure.iot.device import IoTHubDeviceClient
except ImportError:  # pragma: no cover - optional until infrastructure is configured
    IoTHubDeviceClient = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.getenv("DATABASE_URL", "")
APP_SECRET = os.getenv("APP_SECRET", "change-me")
APPINSIGHTS_CONNECTION_STRING = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
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

_redis_client = None
_tenant_iot_clients = {}
_mobile_token_serializer = URLSafeTimedSerializer(APP_SECRET, salt="employee-mobile-auth")


def setup_telemetry():
    if not APPINSIGHTS_CONNECTION_STRING:
        logger.warning("Application Insights connection string not set.")
        return

    configure_azure_monitor(connection_string=APPINSIGHTS_CONNECTION_STRING)
    FastAPIInstrumentor.instrument_app(app)


setup_telemetry()


def record_exception_to_telemetry(exc: Exception):
    logger.exception("Application exception")
    span = trace.get_current_span()
    if span and span.is_recording():
        span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR))


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


def get_conn(db_url: str):
    return psycopg.connect(db_url)


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


def create_mobile_token(user_email: str, organization: str) -> str:
    return _mobile_token_serializer.dumps({"email": user_email, "organization": organization})


def verify_mobile_token(token: str) -> dict:
    try:
        payload = _mobile_token_serializer.loads(token, max_age=60 * 60 * 24 * 7)
    except SignatureExpired as exc:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.") from exc
    except BadSignature as exc:
        raise HTTPException(status_code=401, detail="Invalid mobile session token.") from exc

    email = payload.get("email")
    organization = payload.get("organization")
    if not email or not organization:
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
            logger.warning("Redis lookup failed for %s: %s", org, exc)

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
            logger.warning("Redis cache write failed for %s: %s", org, exc)

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
    client.send_message(json.dumps(payload))


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
        logger.warning("Auth event could not be recorded for %s: %s", user_email, exc)


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


def authenticate_employee(email: str, password: str) -> dict:
    normalized = email.lower().strip()
    org = get_org_for_email(normalized)
    if not org:
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
        raise HTTPException(
            status_code=400,
            detail=f"Only {CONTOSO_DOMAIN} and {LITWARE_DOMAIN} emails are allowed.",
        )

    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    try:
        db_url = get_db_url_for_org(org)
        ensure_schema(db_url)
        with get_conn(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM app_users WHERE email = %s", (normalized,))
                if cur.fetchone():
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
        raise HTTPException(status_code=400, detail="Invalid attendance action.")

    payload = {
        "event_id": str(uuid.uuid4()),
        "user_email": user_email,
        "organization": organization,
        "event_type": normalized_action,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
    }

    try:
        publish_attendance_event(organization, payload)
    except Exception as exc:
        record_exception_to_telemetry(exc)
        raise HTTPException(status_code=502, detail="Attendance event could not be queued to IoT Hub.") from exc

    return payload


def get_mobile_user_from_header(authorization: str | None) -> dict:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required.")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
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
    stats = {"login_count": 0, "logout_count": 0, "attendance_count": 0, "tenant_count": 0}

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
        "home.html",
        {"request": request, "session_user": session_user, "session_admin": session_admin, "msg": msg},
    )


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/hello/{name}")
def hello(name: str):
    return {"message": f"Hello, {name}!"}


@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, msg: str = ""):
    return RedirectResponse("/?msg=Employee+signup+has+moved+to+the+Android+app", status_code=303)


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
    return RedirectResponse("/?msg=Employee+login+has+moved+to+the+Android+app", status_code=303)


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        authenticate_employee(email, password)
    except HTTPException as exc:
        return RedirectResponse(f"/?msg={exc.detail.replace(' ', '+')}", status_code=303)

    return RedirectResponse("/?msg=Employee+login+has+moved+to+the+Android+app", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, msg: str = "", error: str = ""):
    return RedirectResponse("/?msg=Employee+dashboard+is+available+in+the+Android+app", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
def admin_root(request: Request):
    if require_admin_session(request):
        return RedirectResponse("/admin/dashboard", status_code=303)
    return RedirectResponse("/admin/login", status_code=303)


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request, msg: str = ""):
    return templates.TemplateResponse("admin_login.html", {"request": request, "msg": msg})


@app.post("/admin/login")
def admin_login(request: Request, email: str = Form(...), password: str = Form(...)):
    normalized = email.lower().strip()
    if normalized != ADMIN_EMAIL or hash_password(password) != hash_password(ADMIN_PASSWORD):
        return RedirectResponse("/admin/login?msg=Invalid+admin+credentials", status_code=303)

    request.session["admin"] = {"email": normalized}
    record_auth_event(normalized, "admin", "login", actor_type="admin")
    return RedirectResponse("/admin/dashboard?msg=Admin+session+started", status_code=303)


@app.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request, msg: str = "", error: str = ""):
    return load_admin_dashboard_context(request, msg=msg, error=error)


@app.get("/admin/github", response_class=HTMLResponse)
def admin_github_dashboard(request: Request, msg: str = "", error: str = ""):
    return load_github_dashboard_context(request, msg=msg, error=error)


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


@app.post("/attendance/punch")
def attendance_punch(request: Request, action: str = Form(...)):
    return RedirectResponse("/?msg=Attendance+punching+has+moved+to+the+Android+app", status_code=303)


@app.get("/logout")
def logout(request: Request):
    session_user = require_user_session(request)
    session_admin = require_admin_session(request)
    if session_user:
        record_auth_event(session_user["email"], session_user["organization"], "logout")
    if session_admin:
        record_auth_event(session_admin["email"], "admin", "logout", actor_type="admin")
    request.session.clear()
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
