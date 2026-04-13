"""
Simulate a realistic application error and send it to App Insights.
Run with: python tests/simulate_error.py

The error will appear in AppExceptions in Log Analytics within ~2 minutes,
then show up on the /admin/errors dashboard for AI diagnosis.
"""

import os
import traceback
import logging

APPINSIGHTS_CONNECTION_STRING = os.getenv(
    "APPLICATIONINSIGHTS_CONNECTION_STRING",
    "InstrumentationKey=4ae5b1ae-a7f5-470c-9f5f-cd32578c9317;"
    "IngestionEndpoint=https://westus3-1.in.applicationinsights.azure.com/;"
    "LiveEndpoint=https://westus3.livediagnostics.monitor.azure.com/;"
    "ApplicationId=dbd19f4b-6b8f-4e38-b20b-6b845ca21d19",
)

from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace
from opentelemetry.trace.status import Status, StatusCode

configure_azure_monitor(connection_string=APPINSIGHTS_CONNECTION_STRING)

tracer = trace.get_tracer("simulate_error")
logger = logging.getLogger("main")
logging.basicConfig(level=logging.INFO)


def process_attendance_record(record: dict) -> dict:
    """Parse an attendance check-in record. Expects 'employee_id' field."""
    employee_id = record["employee_id"]  # KeyError if field missing
    org = record.get("org", "unknown")
    return {"employee_id": employee_id, "org": org, "status": "processed"}


def main():
    print("Simulating attendance processing error...")

    with tracer.start_as_current_span("process_attendance_record") as span:
        try:
            # Missing required 'employee_id' field — realistic bug
            record = {
                "timestamp": "2026-04-13T03:00:00Z",
                "org": "contoso",
                "action": "checkin",
                # 'employee_id' intentionally omitted
            }
            result = process_attendance_record(record)
            print(f"Processed: {result}")
        except KeyError as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            logger.error(
                "attendance_processing_failed: missing required field %s in record. "
                "File: src/main.py, function: process_attendance_record. "
                "Record: %s",
                exc,
                record,
                exc_info=True,
            )
            print(f"\nError recorded to App Insights: KeyError {exc}")
            print("Stack trace:")
            traceback.print_exc()
            print("\nWait ~2 minutes then visit /admin/errors to see the AI diagnosis.")


if __name__ == "__main__":
    import time
    main()
    # Give the exporter time to flush
    print("\nFlushing telemetry...")
    time.sleep(10)
    print("Done.")
