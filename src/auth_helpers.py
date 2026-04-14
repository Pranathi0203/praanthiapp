import hashlib
import logging

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from config import APP_SECRET, REDIS_URL
from telemetry import log_event, _compact_fields, get_request_id, get_trace_id

try:
    import redis
except ImportError:  # pragma: no cover - optional until infrastructure is configured
    redis = None

_redis_client = None
_mobile_token_serializer = URLSafeTimedSerializer(APP_SECRET, salt="employee-mobile-auth")


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
