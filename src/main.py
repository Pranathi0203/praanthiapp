import hashlib
import os

import psycopg
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.getenv("DATABASE_URL", "")
APP_SECRET = os.getenv("APP_SECRET", "change-me")

app = FastAPI(title="Pranathi App")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg.connect(DATABASE_URL)


def hash_password(password: str) -> str:
    payload = f"{password}:{APP_SECRET}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def ensure_users_table():
    with get_conn() as conn:
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
    if password != confirm_password:
        return RedirectResponse("/signup?msg=Passwords+do+not+match", status_code=303)

    if len(password) < 8:
        return RedirectResponse("/signup?msg=Password+must+be+at+least+8+characters", status_code=303)

    try:
        ensure_users_table()
        with get_conn() as conn:
            with conn.cursor() as cur:
                normalized = email.lower().strip()
                cur.execute("SELECT id FROM app_users WHERE email = %s", (normalized,))
                if cur.fetchone():
                    return RedirectResponse("/signup?msg=Email+already+exists", status_code=303)

                cur.execute(
                    "INSERT INTO app_users(email, password_hash) VALUES(%s, %s)",
                    (normalized, hash_password(password)),
                )
            conn.commit()
    except Exception:
        return RedirectResponse("/signup?msg=Database+connection+failed", status_code=303)

    return RedirectResponse("/login?msg=Account+created+successfully", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, msg: str = ""):
    return templates.TemplateResponse("login.html", {"request": request, "msg": msg})


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        ensure_users_table()
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT email FROM app_users WHERE email = %s AND password_hash = %s",
                    (email.lower().strip(), hash_password(password)),
                )
                row = cur.fetchone()
                if not row:
                    return RedirectResponse("/login?msg=Invalid+email+or+password", status_code=303)
    except Exception:
        return RedirectResponse("/login?msg=Database+connection+failed", status_code=303)

    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "user_email": email.lower().strip()},
    )


@app.get("/logout")
def logout():
    return RedirectResponse("/", status_code=303)
