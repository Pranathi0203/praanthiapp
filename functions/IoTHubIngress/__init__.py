import json
import logging
import os
import time

from azure.servicebus import ServiceBusClient, ServiceBusMessage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def log_event(level, event_name, **fields):
    payload = {key: value for key, value in fields.items() if value is not None and value != ""}
    logger.log(level, event_name, extra={"custom_dimensions": {"event": event_name, **payload}})


def _iter_events(event):
    if isinstance(event, list):
        return event
    return [event]


def _normalize_properties(properties):
    normalized = {}
    for key, value in (properties or {}).items():
        decoded_key = key.decode("utf-8") if isinstance(key, bytes) else key
        decoded_value = value.decode("utf-8") if isinstance(value, bytes) else value
        normalized[decoded_key] = decoded_value
    return normalized


def main(event):
    queue_name = os.environ["ATTENDANCE_QUEUE_NAME"]
    service_bus_connection = os.environ["SERVICEBUS_CONNECTION"]

    with ServiceBusClient.from_connection_string(service_bus_connection) as client:
        with client.get_queue_sender(queue_name=queue_name) as sender:
            for current_event in _iter_events(event):
                started = time.perf_counter()
                raw_payload = current_event.get_body().decode("utf-8")
                payload = json.loads(raw_payload)
                telemetry = payload.get("telemetry") or {}
                application_properties = _normalize_properties(telemetry)

                log_event(
                    logging.INFO,
                    "iot_ingress_event_received",
                    event_id=payload.get("event_id"),
                    organization=payload.get("organization"),
                    source=payload.get("source"),
                    request_id=application_properties.get("request_id"),
                    trace_id=application_properties.get("trace_id"),
                )

                sender.send_messages(
                    ServiceBusMessage(
                        json.dumps(payload),
                        application_properties=application_properties,
                    )
                )

                log_event(
                    logging.INFO,
                    "iot_ingress_event_forwarded",
                    event_id=payload.get("event_id"),
                    organization=payload.get("organization"),
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                    queue_name=queue_name,
                    request_id=application_properties.get("request_id"),
                    trace_id=application_properties.get("trace_id"),
                )
