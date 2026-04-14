import json
import logging

from config import (
    CONTOSO_DEVICE_CONNECTION_STRING,
    LITWARE_DEVICE_CONNECTION_STRING,
    TENANT_CONNECTION_CACHE_TTL_SECONDS,
)
from telemetry import log_event

try:
    from azure.iot.device import IoTHubDeviceClient
except ImportError:  # pragma: no cover - optional until infrastructure is configured
    IoTHubDeviceClient = None

_tenant_iot_clients = {}


def get_device_connection_string_for_org(org: str) -> str:
    from auth_helpers import get_redis_client
    cache_key = f"tenant:{org}:iot:device-connection-string"
    cached_value = None
    redis_client = get_redis_client()

    if redis_client is not None:
        try:
            cached_value = redis_client.get(cache_key)
        except Exception as exc:
            log_event(logging.WARNING, "redis_lookup_failed", organization=org, error=str(exc))

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
            log_event(logging.WARNING, "redis_cache_write_failed", organization=org, error=str(exc))

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
    log_event(
        logging.INFO,
        "attendance_event_publish_started",
        organization=org,
        event_id=payload.get("event_id"),
        source=payload.get("source"),
    )
    client.send_message(json.dumps(payload))
    log_event(
        logging.INFO,
        "attendance_event_publish_completed",
        organization=org,
        event_id=payload.get("event_id"),
        source=payload.get("source"),
    )
