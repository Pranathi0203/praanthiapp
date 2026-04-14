import json
import subprocess
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from config import (
    AZURE_SUBSCRIPTION_ID,
    AZURE_USE_MANAGED_IDENTITY,
)
from telemetry import log_event

_BLOCKED_AZ_SUBCOMMANDS = {"delete", "remove", "purge", "force-delete", "drop"}


def ensure_azure_cli_login():
    show = subprocess.run(
        ["az", "account", "show", "-o", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if show.returncode != 0:
        if not AZURE_USE_MANAGED_IDENTITY:
            raise RuntimeError("Azure CLI is not logged in and managed identity is disabled.")
        subprocess.run(
            ["az", "login", "--identity", "--output", "none", "--only-show-errors"],
            capture_output=True,
            text=True,
            check=True,
        )

    if AZURE_SUBSCRIPTION_ID:
        subprocess.run(
            ["az", "account", "set", "--subscription", AZURE_SUBSCRIPTION_ID, "--only-show-errors"],
            capture_output=True,
            text=True,
            check=True,
        )


def run_azure_cli_json(args: list[str]) -> dict | list:
    try:
        ensure_azure_cli_login()
        completed = subprocess.run(
            ["az", *args, "-o", "json", "--only-show-errors"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Azure CLI is not installed in the web app container.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or "Unknown Azure CLI error."
        raise RuntimeError(detail) from exc

    raw = (completed.stdout or "").strip()
    if not raw:
        return {}

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Azure CLI returned non-JSON output: {raw}") from exc


def run_azure_cli_access_token(resource: str) -> str:
    payload = run_azure_cli_json(["account", "get-access-token", "--resource", resource])
    token = str((payload or {}).get("accessToken", "")).strip()
    if not token:
        raise RuntimeError(f"Azure CLI did not return an access token for resource {resource}.")
    return token


def arm_get(resource_id: str, api_version: str) -> dict[str, Any]:
    token = run_azure_cli_access_token("https://management.azure.com/")
    url = "https://management.azure.com{}?api-version={}".format(
        resource_id,
        urllib_parse.quote(api_version, safe=""),
    )
    req = urllib_request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib_request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"ARM request failed for {resource_id}: HTTP {exc.code} {body}") from exc


def post_json_with_token(url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    return post_json_with_headers(
        url,
        {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        payload,
    )


def post_json_with_headers(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int = 60
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Log query failed: HTTP {exc.code} {detail}") from exc


def validate_and_run_az_command(command: str) -> str:
    """Validate an AI-generated az command is safe, then execute it. Returns output."""
    command = command.strip()
    if not command.startswith("az "):
        raise RuntimeError("Only az CLI commands are permitted.")
    parts = command.split()
    for part in parts:
        if part.lower() in _BLOCKED_AZ_SUBCOMMANDS:
            raise RuntimeError(f"Command contains a destructive verb '{part}' — blocked for safety.")
    args = parts[1:]  # strip leading "az"
    result = run_azure_cli_json(args)
    return json.dumps(result) if result else "Command completed successfully."
