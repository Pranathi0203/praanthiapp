import hashlib
import logging
import os
from urllib.parse import urlparse, urlunparse

import psycopg
from azure.monitor.opentelemetry import configure_azure_monitor
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from opentelemetry import trace
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.trace.status import Status, StatusCode

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.getenv("DATABASE_URL", "")
APP_SECRET = os.getenv("APP_SECRET", "change-me")
APPINSIGHTS_CONNECTION_STRING = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
CONTOSO_DOMAIN = os.getenv("CONTOSO_DOMAIN", "contoso.com").lower()
LITWARE_DOMAIN = os.getenv("LITWARE_DOMAIN", "litware.com").lower()
CONTOSO_DB_NAME = os.getenv("CONTOSO_DB_NAME", "contoso_db")
LITWARE_DB_NAME = os.getenv("LITWARE_DB_NAME", "litware_db")
CONTOSO_DATABASE_URL = os.getenv("CONTOSO_DATABASE_URL", "")
LITWARE_DATABASE_URL = os.getenv("LITWARE_DATABASE_URL", "")

app = FastAPI(title="Pranathi App")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
logger = logging.getLogger(__name__)


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


def get_conn(db_url: str):
    return psycopg.connect(db_url)


def hash_password(password: str) -> str:
    payload = f"{password}:{APP_SECRET}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def ensure_users_table(db_url: str):
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
        conn.commit()


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/hello/{name}")
def hello(name: str):
    return {"message": f"Hello, {name}!"}


@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, msg: str = ""):
    return templates.TemplateResponse("signup.html", {"request": request, "msg": msg})


@app.post("/signup")
def signup(email: str = Form(...), password: str = Form(...), confirm_password: str = Form(...)):
    normalized = email.lower().strip()
    org = get_org_for_email(normalized)
    if not org:
        return RedirectResponse(
            f"/signup?msg=Only+{CONTOSO_DOMAIN}+and+{LITWARE_DOMAIN}+emails+are+allowed",
            status_code=303,
        )

    if password != confirm_password:
        return RedirectResponse("/signup?msg=Passwords+do+not+match", status_code=303)

    if len(password) < 8:
        return RedirectResponse("/signup?msg=Password+must+be+at+least+8+characters", status_code=303)

    try:
        db_url = get_db_url_for_org(org)
        ensure_users_table(db_url)
        with get_conn(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM app_users WHERE email = %s", (normalized,))
                if cur.fetchone():
                    return RedirectResponse("/signup?msg=Email+already+exists", status_code=303)

                cur.execute(
                    "INSERT INTO app_users(email, password_hash) VALUES(%s, %s)",
                    (normalized, hash_password(password)),
                )
            conn.commit()
    except Exception as exc:
        record_exception_to_telemetry(exc)
        return RedirectResponse("/signup?msg=Database+connection+failed", status_code=303)

    return RedirectResponse("/login?msg=Account+created+successfully", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, msg: str = ""):
    return templates.TemplateResponse("login.html", {"request": request, "msg": msg})


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    normalized = email.lower().strip()
    org = get_org_for_email(normalized)
    if not org:
        return RedirectResponse(
            f"/login?msg=Only+{CONTOSO_DOMAIN}+and+{LITWARE_DOMAIN}+emails+are+allowed",
            status_code=303,
        )

    try:
        db_url = get_db_url_for_org(org)
        ensure_users_table(db_url)
        with get_conn(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT email FROM app_users WHERE email = %s AND password_hash = %s",
                    (normalized, hash_password(password)),
                )
                row = cur.fetchone()
                if not row:
                    return RedirectResponse("/login?msg=Invalid+email+or+password", status_code=303)

                cur.execute(
                    "SELECT email FROM app_users ORDER BY created_at DESC LIMIT 200"
                )
                employees = [r[0] for r in cur.fetchall()]
    except Exception as exc:
        record_exception_to_telemetry(exc)
        return RedirectResponse("/login?msg=Database+connection+failed", status_code=303)

    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "user_email": normalized, "organization": org, "employees": employees},
    )


@app.get("/logout")
def logout():
    return RedirectResponse("/", status_code=303)
