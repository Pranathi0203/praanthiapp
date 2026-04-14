import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib import parse as urllib_parse

from config import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    APP_SECRET,
    AZURE_POSTGRES_SERVER_NAME,
    AZURE_RESOURCE_GROUP,
    AZURE_SUBSCRIPTION_ID,
    BASE_DIR,
    FUNCTION_APP_NAME,
    GITHUB_CI_WORKFLOW,
    GITHUB_RELEASE_WORKFLOW,
    GITHUB_REPOSITORY,
    SESSION_HTTPS_ONLY,
    WEBAPP_NAME,
)

from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

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

# ─── Import helpers from modules ─────────────────────────────────────────────

from telemetry import (
    _request_id_context,
    _compact_fields,
    get_request_id,
    get_trace_id,
    get_datadog_log_context,
    get_datadog_trace_id,
    _parse_ikey,
    _get_ai_client,
    log_event,
    record_exception_to_telemetry,
    get_http_log_level,
    record_http_failure_to_telemetry,
    setup_telemetry,
)

from db import (
    with_database_name,
    get_org_for_email,
    get_db_url_for_org,
    get_admin_db_url,
    get_postgres_admin_url,
    get_conn,
    is_postgres_platform_configured,
    get_postgres_admin_script_path,
    ensure_schema,
    ensure_admin_schema,
    record_auth_event,
    extract_database_name,
    get_managed_database_entries,
    get_managed_database_entry,
    load_database_dashboard_context,
    authenticate_employee,
    create_employee_account,
    load_attendance_history,
    POSTGRES_ADMIN_SCRIPT_CANDIDATES,
)

from azure_utils import (
    ensure_azure_cli_login,
    run_azure_cli_json,
    run_azure_cli_access_token,
    arm_get,
    post_json_with_token,
    post_json_with_headers,
    _BLOCKED_AZ_SUBCOMMANDS,
    validate_and_run_az_command,
)

from ai_analysis import (
    is_error_operations_configured,
    get_workspace_context,
    build_error_union_query,
    query_log_analytics,
    resource_name_from_id,
    severity_rank,
    infer_error_severity,
    sanitize_text,
    parse_int,
    dedupe_resources,
    normalize_repo_path,
    extract_code_location,
    json_dumps_compact,
    parse_json_response_text,
    build_fix_metadata,
    build_fix_catalog,
    build_resource_catalog,
    fallback_issue_analysis,
    classify_error_fix,
    describe_issue_title,
    describe_issue_summary,
    classify_issue_role,
    build_issue_overview,
    is_ai_configured,
    call_azure_openai,
    call_azure_openai_with_tools,
    _build_ai_analysis_prompt,
    _AI_DEFENDER_PROMPT,
    load_defender_recommendations,
    analyze_defender_with_ai,
    _AI_POLICY_PROMPT,
    load_policy_violations,
    analyze_policy_with_ai,
    normalize_repo_file_path,
    _extract_file_line_from_row,
    github_get_snippet,
    analyze_errors_with_ai,
    load_recent_error_operations,
    apply_error_fix,
    run_postgres_platform_action,
    get_cached_postgres_platform_action,
    load_error_operations_context,
    _postgres_platform_cache,
    POSTGRES_PLATFORM_CACHE_TTL_SECONDS,
)

from github_utils import (
    is_github_dashboard_configured,
    parse_iso_datetime,
    get_github_repo_parts,
    github_api_request,
    github_get_file_on_branch,
    github_get_file,
    github_create_branch,
    github_update_file,
    github_create_pr,
    load_open_pull_requests,
    merge_pull_request,
    close_pull_request,
    trigger_github_workflow,
    wait_for_workflow_run,
    wait_for_workflow_completion,
    _CODE_FIX_SYSTEM_PROMPT,
    generate_code_fix,
    create_code_fix_pr,
    load_github_dashboard_context,
)

from auth_helpers import (
    hash_password,
    get_redis_client,
    get_request_telemetry,
    create_mobile_token,
    verify_mobile_token,
    require_user_session,
    require_admin_session,
    _mobile_token_serializer,
    _redis_client,
)

from iot_helpers import (
    get_device_connection_string_for_org,
    get_iot_client_for_org,
    publish_attendance_event,
    _tenant_iot_clients,
)

from health import (
    check_database_health,
    check_redis_health,
    check_iot_health,
    build_health_payload,
)

from pipeline import (
    _PIPELINE_STAGE_META,
    _stage_message,
    _build_pipeline_stage,
    _run_az_probe,
    _probe_webapp_raw,
    _probe_iothub_raw,
    _probe_servicebus_raw,
    _probe_functionapp_raw,
    _probe_postgresql_raw,
    _probe_redis_raw,
    load_pipeline_stages,
)

from admin_context import (
    load_dashboard_context,
    load_admin_dashboard_context,
    POLICY_REMEDIATION_MAP,
    POLICY_SEVERITY_MAP,
    load_policy_compliance,
    apply_policy_fix,
)

logging.basicConfig(level=logging.INFO)
setup_telemetry(app)


class MobileCredentials(BaseModel):
    email: str
    password: str = Field(min_length=8)


class MobilePunchRequest(BaseModel):
    action: str


def get_mobile_user_from_header(authorization: str | None) -> dict:
    if not authorization:
        log_event(logging.WARNING, "mobile_auth_header_missing")
        raise HTTPException(status_code=401, detail="Authorization header is required.")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        log_event(logging.WARNING, "mobile_auth_header_invalid", authorization_scheme=scheme or "missing")
        raise HTTPException(status_code=401, detail="Authorization header must use Bearer token.")

    return verify_mobile_token(token)


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
def admin_error_operations(request: Request, msg: str = "", error: str = "", minutes: int = 60, tab: str = "errors"):
    return load_error_operations_context(request, msg=msg, error=error, minutes=minutes, tab=tab)


@app.get("/admin/databases", response_class=HTMLResponse)
def admin_database_dashboard(request: Request, msg: str = "", error: str = ""):
    return load_database_dashboard_context(request, msg=msg, error=error)


# ─── Policy Compliance ──────────────────────────────────────────────────────

@app.get("/admin/policies", response_class=HTMLResponse)
def admin_policy_compliance(request: Request, msg: str = "", error: str = ""):
    session_admin = require_admin_session(request)
    if not session_admin:
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    findings: list[dict[str, Any]] = []
    scan_error = error
    if AZURE_RESOURCE_GROUP and AZURE_SUBSCRIPTION_ID:
        try:
            findings = load_policy_compliance()
        except Exception as exc:
            record_exception_to_telemetry(exc, action="load_policy_compliance")
            scan_error = str(exc)

    severity_counts = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        severity_counts[f["severity"]] = severity_counts.get(f["severity"], 0) + 1

    return templates.TemplateResponse(
        request,
        "admin_policies.html",
        {
            "request": request,
            "admin_email": session_admin["email"],
            "msg": msg,
            "error": scan_error,
            "findings": findings,
            "severity_counts": severity_counts,
            "configured": bool(AZURE_RESOURCE_GROUP and AZURE_SUBSCRIPTION_ID),
        },
    )


@app.post("/admin/policies/apply")
def admin_policy_apply_fix(
    request: Request,
    policy_name: str = Form(...),
    resource_id: str = Form(...),
):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)
    try:
        result = apply_policy_fix(policy_name, resource_id)
        log_event(logging.INFO, "policy_fix_applied", policy_name=policy_name, resource_id=resource_id)
        return RedirectResponse(f"/admin/policies?msg={urllib_parse.quote_plus(result)}", status_code=303)
    except Exception as exc:
        record_exception_to_telemetry(exc, action="apply_policy_fix", policy_name=policy_name)
        return RedirectResponse(f"/admin/policies?error={urllib_parse.quote_plus(str(exc))}", status_code=303)


@app.post("/admin/policies/deny")
def admin_policy_deny_fix(request: Request, policy_name: str = Form(...), resource_name: str = Form("")):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)
    msg = f"Fix for '{policy_name}' on {resource_name} was denied. No action taken."
    return RedirectResponse(f"/admin/policies?msg={urllib_parse.quote_plus(msg)}", status_code=303)


# ─── Pipeline Guardian Routes ─────────────────────────────────────────────────

@app.get("/admin/pipeline", response_class=HTMLResponse)
def admin_pipeline_guardian(request: Request, msg: str = "", error: str = ""):
    session_admin = require_admin_session(request)
    if not session_admin:
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    stages: list[dict[str, Any]] = []
    scan_error = error

    try:
        stages = load_pipeline_stages()
    except Exception as exc:
        record_exception_to_telemetry(exc, action="load_pipeline_stages")
        scan_error = str(exc)

    issues = [s for s in stages if s["status"] in ("unhealthy", "degraded", "unknown") and s["status"] != "not_configured"]
    stage_counts = {
        "healthy": sum(1 for s in stages if s["status"] == "healthy"),
        "unhealthy": sum(1 for s in stages if s["status"] == "unhealthy"),
        "degraded": sum(1 for s in stages if s["status"] == "degraded"),
        "unknown": sum(1 for s in stages if s["status"] in ("unknown", "not_configured")),
    }

    return templates.TemplateResponse(
        request,
        "admin_pipeline.html",
        {
            "request": request,
            "admin_email": session_admin["email"],
            "msg": msg,
            "error": scan_error,
            "stages": stages,
            "issues": issues,
            "stage_counts": stage_counts,
        },
    )


@app.post("/admin/pipeline/apply")
def admin_pipeline_apply_fix(
    request: Request,
    fix_type: str = Form(...),
    stage_id: str = Form(""),
):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    fix_meta = build_fix_metadata(fix_type)
    if not fix_meta.get("is_available"):
        msg = f"{fix_meta.get('action_label', fix_type)} is not enabled for this environment."
        return RedirectResponse(f"/admin/pipeline?error={urllib_parse.quote_plus(msg)}", status_code=303)

    try:
        result = apply_error_fix(fix_type)
        log_event(logging.INFO, "pipeline_fix_applied", fix_type=fix_type, stage_id=stage_id)
        return RedirectResponse(f"/admin/pipeline?msg={urllib_parse.quote_plus(result)}", status_code=303)
    except Exception as exc:
        record_exception_to_telemetry(exc, action="apply_pipeline_fix", fix_type=fix_type, stage_id=stage_id)
        return RedirectResponse(f"/admin/pipeline?error={urllib_parse.quote_plus(str(exc))}", status_code=303)


@app.post("/admin/pipeline/dismiss")
def admin_pipeline_dismiss(request: Request, stage_id: str = Form(""), stage_name: str = Form("")):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)
    msg = f"Finding for '{stage_name or stage_id}' dismissed. No action taken."
    return RedirectResponse(f"/admin/pipeline?msg={urllib_parse.quote_plus(msg)}", status_code=303)


@app.post("/admin/errors/apply")
def admin_error_apply_fix(
    request: Request,
    fix_type: str = Form(""),
    minutes: int = Form(60),
):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    if not fix_type:
        return RedirectResponse(f"/admin/errors?minutes={minutes}&error=No+fix+provided", status_code=303)
    fix = build_fix_metadata(fix_type)
    if not fix["is_available"]:
        msg = f"{fix['action_label']} is not enabled for this environment."
        return RedirectResponse(f"/admin/errors?error={urllib_parse.quote_plus(msg)}", status_code=303)
    try:
        result_message = apply_error_fix(fix_type)
        return RedirectResponse(
            f"/admin/errors?minutes={minutes}&msg={urllib_parse.quote_plus(result_message)}", status_code=303
        )
    except Exception as exc:
        record_exception_to_telemetry(exc, action="apply_error_fix", fix_type=fix_type)
        return RedirectResponse(
            f"/admin/errors?minutes={minutes}&error={urllib_parse.quote_plus(str(exc))}", status_code=303
        )


@app.post("/admin/errors/create-fix-pr")
def admin_error_create_fix_pr(
    request: Request,
    issue_id: str = Form(""),
    file_path: str = Form(""),
    line_number: int = Form(0),
    what_happened: str = Form(""),
    why_it_occurred: str = Form(""),
    fix_description: str = Form(""),
    error_message: str = Form(""),
    minutes: int = Form(60),
    tab: str = Form("errors"),
):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)
    if not file_path:
        return RedirectResponse(
            f"/admin/errors?tab={tab}&minutes={minutes}&error={urllib_parse.quote_plus('No file path — cannot create fix PR.')}",
            status_code=303,
        )
    if not is_github_dashboard_configured():
        return RedirectResponse(
            f"/admin/errors?tab={tab}&minutes={minutes}&error={urllib_parse.quote_plus('GitHub is not configured. Set GITHUB_REPOSITORY and GITHUB_DASHBOARD_TOKEN.')}",
            status_code=303,
        )
    if not is_ai_configured():
        return RedirectResponse(
            f"/admin/errors?tab={tab}&minutes={minutes}&error={urllib_parse.quote_plus('Azure OpenAI is not configured. Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT.')}",
            status_code=303,
        )
    try:
        pr_url = create_code_fix_pr(
            issue_id=issue_id,
            file_path=file_path,
            line_number=line_number,
            what_happened=what_happened,
            why_it_occurred=why_it_occurred,
            fix_description=fix_description,
            error_message=error_message,
        )
        log_event(logging.INFO, "ai_code_fix_pr_created", issue_id=issue_id, file_path=file_path, pr_url=pr_url)
        msg = f"Fix PR created: {pr_url}"
        return RedirectResponse(f"/admin/errors?tab={tab}&minutes={minutes}&msg={urllib_parse.quote_plus(msg)}", status_code=303)
    except Exception as exc:
        record_exception_to_telemetry(exc, action="create_code_fix_pr", issue_id=issue_id)
        return RedirectResponse(
            f"/admin/errors?tab={tab}&minutes={minutes}&error={urllib_parse.quote_plus(str(exc))}", status_code=303
        )


@app.post("/admin/errors/deny")
def admin_error_deny_fix(request: Request, minutes: int = Form(60), fix_label: str = Form("fix"), tab: str = Form("errors")):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    message = f"{fix_label} was denied. No automated action was taken."
    return RedirectResponse(
        f"/admin/errors?tab={tab}&minutes={minutes}&msg={urllib_parse.quote_plus(message)}",
        status_code=303,
    )


@app.post("/admin/errors/run-command")
def admin_run_az_command(
    request: Request,
    fix_command: str = Form(""),
    fix_label: str = Form("fix"),
    minutes: int = Form(60),
    tab: str = Form("errors"),
):
    if not require_admin_session(request):
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)
    if not fix_command:
        return RedirectResponse(
            f"/admin/errors?tab={tab}&minutes={minutes}&error=No+command+provided",
            status_code=303,
        )
    try:
        output = validate_and_run_az_command(fix_command)
        log_event(logging.INFO, "ai_az_command_executed", command=fix_command, output=output[:200])
        msg = f"Command executed successfully: {fix_label}"
        return RedirectResponse(
            f"/admin/errors?tab={tab}&minutes={minutes}&msg={urllib_parse.quote_plus(msg)}",
            status_code=303,
        )
    except Exception as exc:
        record_exception_to_telemetry(exc, action="run_az_command", command=fix_command)
        return RedirectResponse(
            f"/admin/errors?tab={tab}&minutes={minutes}&error={urllib_parse.quote_plus(str(exc))}",
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
