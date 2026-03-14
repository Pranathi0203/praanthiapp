import json
import logging
import os

from azure.servicebus import ServiceBusClient, ServiceBusMessage

logger = logging.getLogger(__name__)


def main(event):
    raw_payload = event.get_body().decode("utf-8")
    logger.info("Received attendance event from IoT ingress: %s", raw_payload)

    payload = json.loads(raw_payload)
    queue_name = os.environ["ATTENDANCE_QUEUE_NAME"]
    service_bus_connection = os.environ["SERVICEBUS_CONNECTION"]

    with ServiceBusClient.from_connection_string(service_bus_connection) as client:
        with client.get_queue_sender(queue_name=queue_name) as sender:
            sender.send_messages(ServiceBusMessage(json.dumps(payload)))
