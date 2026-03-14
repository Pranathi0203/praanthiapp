import json
import logging
import os
from contextlib import closing

import psycopg

logger = logging.getLogger(__name__)


def get_database_url(organization: str) -> str:
    if organization == "contoso":
        return os.environ["CONTOSO_DATABASE_URL"]
    if organization == "litware":
        return os.environ["LITWARE_DATABASE_URL"]
    raise ValueError(f"Unsupported organization: {organization}")


def main(msg):
    payload = json.loads(msg.get_body().decode("utf-8"))
    organization = payload["organization"]
    database_url = get_database_url(organization)

    with closing(psycopg.connect(database_url)) as conn:
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

    logger.info("Attendance event %s persisted for %s", payload["event_id"], payload["user_email"])
