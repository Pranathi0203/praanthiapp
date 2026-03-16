import json
import logging
import os

from azure.servicebus import ServiceBusClient, ServiceBusMessage

logger = logging.getLogger(__name__)


def _iter_events(event):
    if isinstance(event, list):
        return event
    return [event]


def main(event):
    queue_name = os.environ["ATTENDANCE_QUEUE_NAME"]
    service_bus_connection = os.environ["SERVICEBUS_CONNECTION"]

    with ServiceBusClient.from_connection_string(service_bus_connection) as client:
        with client.get_queue_sender(queue_name=queue_name) as sender:
            for current_event in _iter_events(event):
                raw_payload = current_event.get_body().decode("utf-8")
                logger.info("Received attendance event from IoT ingress: %s", raw_payload)

                payload = json.loads(raw_payload)
                sender.send_messages(ServiceBusMessage(json.dumps(payload)))
