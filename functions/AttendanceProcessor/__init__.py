import json
import logging
import os
import time
from contextlib import closing

import psycopg

logger = logging.getLogger(__name__)


def log_event(level, event_name, **fields):
    logger.log(level, json.dumps({"event": event_name, **fields}, default=str, sort_keys=True))


def _normalize_properties(properties):
    normalized = {}
    for key, value in (properties or {}).items():
        decoded_key = key.decode("utf-8") if isinstance(key, bytes) else key
        decoded_value = value.decode("utf-8") if isinstance(value, bytes) else value
        normalized[decoded_key] = decoded_value
    return normalized


def get_database_url(organization: str) -> str:
    if organization == "contoso":
        return os.environ["CONTOSO_DATABASE_URL"]
    if organization == "litware":
        return os.environ["LITWARE_DATABASE_URL"]
    raise ValueError(f"Unsupported organization: {organization}")


def ensure_schema(conn):
    with conn.cursor() as cur:
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


def main(msg):
    started = time.perf_counter()
    payload = json.loads(msg.get_body().decode("utf-8"))
    application_properties = _normalize_properties(getattr(msg, "application_properties", {}))
    telemetry = payload.get("telemetry") or {}
    organization = payload["organization"]

    log_event(
        logging.INFO,
        "attendance_processor_started",
        event_id=payload.get("event_id"),
        organization=organization,
        user_email=payload.get("user_email"),
        source=payload.get("source"),
        request_id=application_properties.get("request_id") or telemetry.get("request_id"),
        trace_id=application_properties.get("trace_id") or telemetry.get("trace_id"),
    )

    try:
        database_url = get_database_url(organization)
        with closing(psycopg.connect(database_url)) as conn:
            ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO attendance_events (
                        event_id,
                        user_email,
                        organization,
                        event_type,
                        status,
                        source,
                        requested_at,
                        processed_at,
                        metadata
                    )
                    VALUES (
                        %(event_id)s,
                        %(user_email)s,
                        %(organization)s,
                        %(event_type)s,
                        'processed',
                        %(source)s,
                        %(requested_at)s,
                        NOW(),
                        %(metadata)s::jsonb
                    )
                    ON CONFLICT (event_id) DO UPDATE
                    SET
                        event_type = EXCLUDED.event_type,
                        status = EXCLUDED.status,
                        source = EXCLUDED.source,
                        requested_at = EXCLUDED.requested_at,
                        processed_at = NOW(),
                        metadata = EXCLUDED.metadata
                    """,
                    {
                        "event_id": payload["event_id"],
                        "user_email": payload["user_email"],
                        "organization": organization,
                        "event_type": payload["event_type"],
                        "source": payload.get("source", "iot-pipeline"),
                        "requested_at": payload["requested_at"],
                        "metadata": json.dumps(payload),
                    },
                )
            conn.commit()
    except Exception as exc:
        logger.error(
            json.dumps(
                {
                    "event": "attendance_processor_failed",
                    "event_id": payload.get("event_id"),
                    "organization": organization,
                    "user_email": payload.get("user_email"),
                    "request_id": application_properties.get("request_id") or telemetry.get("request_id"),
                    "trace_id": application_properties.get("trace_id") or telemetry.get("trace_id"),
                    "error": str(exc),
                },
                default=str,
                sort_keys=True,
            )
        )
        raise

    log_event(
        logging.INFO,
        "attendance_processor_completed",
        event_id=payload.get("event_id"),
        organization=organization,
        user_email=payload.get("user_email"),
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
        request_id=application_properties.get("request_id") or telemetry.get("request_id"),
        trace_id=application_properties.get("trace_id") or telemetry.get("trace_id"),
    )
