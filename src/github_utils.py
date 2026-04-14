import json
import os
import time
from datetime import datetime, timezone
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from config import (
    GITHUB_API_URL,
    GITHUB_BRANCH,
    GITHUB_DEFAULT_BRANCH,
    GITHUB_DASHBOARD_TOKEN,
    GITHUB_REPOSITORY,
    GITHUB_DEPLOY_QA_DEFAULT,
)
from telemetry import record_exception_to_telemetry
from ai_analysis import call_azure_openai, sanitize_text, parse_json_response_text, json_dumps_compact


def is_github_dashboard_configured() -> bool:
    return bool(GITHUB_DASHBOARD_TOKEN and "/" in GITHUB_REPOSITORY)


def parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_github_repo_parts() -> tuple[str, str]:
    if "/" not in GITHUB_REPOSITORY:
        raise RuntimeError("GITHUB_REPOSITORY must use owner/repo format.")
    owner, repo = GITHUB_REPOSITORY.split("/", 1)
    return owner, repo


def github_api_request(
    method: str,
    path: str,
    *,
    query: dict | None = None,
    payload: dict | None = None,
    expected_statuses: tuple[int, ...] = (200,),
) -> dict | list | None:
    if not is_github_dashboard_configured():
        raise RuntimeError("GitHub dashboard integration is not configured.")

    url = f"{GITHUB_API_URL}{path}"
    if query:
        url = f"{url}?{urllib_parse.urlencode(query)}"

    body = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_DASHBOARD_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pranathi-admin-dashboard",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib_request.Request(url, data=body, method=method, headers=headers)

    try:
        with urllib_request.urlopen(request, timeout=30) as response:
            status = response.getcode()
            raw = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        detail = raw
        try:
            detail = json.loads(raw).get("message", raw)
        except json.JSONDecodeError:
            pass
        raise RuntimeError(f"GitHub API request failed ({exc.code}): {detail} [url={method} {url}]") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"GitHub API request failed: {exc.reason}") from exc

    if status not in expected_statuses:
        raise RuntimeError(f"GitHub API request returned unexpected status {status}.")

    if not raw:
        return None

    return json.loads(raw)


def github_get_file_on_branch(file_path: str, branch: str) -> tuple[str, str]:
    """Fetch file content from a specific branch. Returns (content, sha)."""
    owner, repo = get_github_repo_parts()
    data = github_api_request(
        "GET",
        f"/repos/{owner}/{repo}/contents/{file_path.lstrip('/')}",
        query={"ref": branch},
    )
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected response fetching {file_path} on {branch}")
    import base64 as _b64
    content = _b64.b64decode(data["content"]).decode("utf-8")
    return content, data["sha"]


def github_get_file(file_path: str) -> tuple[str, str]:
    """Return (decoded content, blob sha) for a file in the repo.
    Tries GITHUB_DEFAULT_BRANCH first, then GITHUB_BRANCH as fallback."""
    branches = list(dict.fromkeys([GITHUB_DEFAULT_BRANCH, GITHUB_BRANCH]))  # dedupe, preserve order
    owner, repo = get_github_repo_parts()
    last_exc: Exception = RuntimeError("No branches to try")
    for branch in branches:
        try:
            data = github_api_request(
                "GET",
                f"/repos/{owner}/{repo}/contents/{file_path.lstrip('/')}",
                query={"ref": branch},
            )
            if not isinstance(data, dict):
                raise RuntimeError(f"Unexpected response fetching {file_path}")
            import base64 as _b64
            content = _b64.b64decode(data["content"]).decode("utf-8")
            return content, data["sha"]
        except Exception as exc:
            last_exc = exc
    raise last_exc


def github_create_branch(branch_name: str) -> None:
    """Create a new branch off GITHUB_BRANCH (the deployed branch)."""
    owner, repo = get_github_repo_parts()
    ref_data = github_api_request("GET", f"/repos/{owner}/{repo}/git/refs/heads/{GITHUB_BRANCH}")
    base_sha = (ref_data.get("object") or {}).get("sha", "") if isinstance(ref_data, dict) else ""
    if not base_sha:
        raise RuntimeError(f"Could not resolve SHA for branch '{GITHUB_BRANCH}'.")
    github_api_request(
        "POST",
        f"/repos/{owner}/{repo}/git/refs",
        payload={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
        expected_statuses=(201,),
    )


def github_update_file(file_path: str, new_content: str, blob_sha: str, branch_name: str, commit_message: str) -> None:
    import base64 as _b64
    owner, repo = get_github_repo_parts()
    github_api_request(
        "PUT",
        f"/repos/{owner}/{repo}/contents/{file_path.lstrip('/')}",
        payload={
            "message": commit_message,
            "content": _b64.b64encode(new_content.encode("utf-8")).decode("ascii"),
            "sha": blob_sha,
            "branch": branch_name,
        },
        expected_statuses=(200, 201),
    )


def github_create_pr(title: str, body: str, head_branch: str) -> str:
    """Create a PR and return its HTML URL."""
    owner, repo = get_github_repo_parts()
    data = github_api_request(
        "POST",
        f"/repos/{owner}/{repo}/pulls",
        payload={"title": title, "body": body, "head": head_branch, "base": GITHUB_BRANCH},
        expected_statuses=(201,),
    )
    return (data or {}).get("html_url", "")


def load_open_pull_requests() -> list[dict]:
    owner, repo = get_github_repo_parts()
    data = github_api_request(
        "GET",
        f"/repos/{owner}/{repo}/pulls",
        query={"state": "open", "sort": "updated", "direction": "desc", "per_page": 25},
    )
    pulls = []
    for item in data or []:
        pulls.append(
            {
                "number": item["number"],
                "title": item["title"],
                "author": (item.get("user") or {}).get("login", "unknown"),
                "branch": (item.get("head") or {}).get("ref", ""),
                "head_sha": (item.get("head") or {}).get("sha", ""),
                "base_branch": (item.get("base") or {}).get("ref", ""),
                "updated_at": item.get("updated_at"),
                "html_url": item.get("html_url"),
                "draft": item.get("draft", False),
            }
        )
    return pulls


def merge_pull_request(pr_number: int, head_sha: str) -> str:
    owner, repo = get_github_repo_parts()
    response = github_api_request(
        "PUT",
        f"/repos/{owner}/{repo}/pulls/{pr_number}/merge",
        payload={
            "sha": head_sha,
            "merge_method": "merge",
            "commit_title": f"Merge pull request #{pr_number} via admin dashboard",
        },
        expected_statuses=(200, 201),
    )
    merge_sha = (response or {}).get("sha")
    if not merge_sha:
        raise RuntimeError("GitHub merged the pull request but did not return a merge commit SHA.")
    return merge_sha


def close_pull_request(pr_number: int):
    owner, repo = get_github_repo_parts()
    github_api_request(
        "PATCH",
        f"/repos/{owner}/{repo}/pulls/{pr_number}",
        payload={"state": "closed"},
        expected_statuses=(200,),
    )


def trigger_github_workflow(workflow_name: str, ref: str, inputs: dict | None = None):
    owner, repo = get_github_repo_parts()
    github_api_request(
        "POST",
        f"/repos/{owner}/{repo}/actions/workflows/{workflow_name}/dispatches",
        payload={"ref": ref, "inputs": inputs or {}},
        expected_statuses=(204,),
    )


def wait_for_workflow_run(
    workflow_name: str,
    *,
    head_sha: str,
    event: str,
    created_after: str,
    timeout_seconds: int = 420,
    poll_interval_seconds: int = 5,
) -> dict:
    owner, repo = get_github_repo_parts()
    created_after_dt = parse_iso_datetime(created_after)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        data = github_api_request(
            "GET",
            f"/repos/{owner}/{repo}/actions/workflows/{workflow_name}/runs",
            query={"event": event, "per_page": 20},
        )
        for run in (data or {}).get("workflow_runs", []):
            if run.get("head_sha") != head_sha:
                continue
            created_at = run.get("created_at")
            if not created_at or parse_iso_datetime(created_at) < created_after_dt:
                continue
            return {
                "id": run.get("id"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "html_url": run.get("html_url"),
            }
        time.sleep(poll_interval_seconds)
    raise RuntimeError(f"Timed out waiting for workflow {workflow_name} to start.")


def wait_for_workflow_completion(
    workflow_name: str,
    *,
    run_id: int,
    timeout_seconds: int = 900,
    poll_interval_seconds: int = 10,
) -> dict:
    owner, repo = get_github_repo_parts()
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        run = github_api_request(
            "GET",
            f"/repos/{owner}/{repo}/actions/runs/{run_id}",
            expected_statuses=(200,),
        )
        if (run or {}).get("status") == "completed":
            return {
                "id": run.get("id"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "html_url": run.get("html_url"),
            }
        time.sleep(poll_interval_seconds)
    raise RuntimeError(f"Timed out waiting for workflow {workflow_name} to finish.")


_CODE_FIX_SYSTEM_PROMPT = """\
You are fixing a specific bug in a source file.

Generate the minimal, targeted fix — the smallest possible change to resolve the specific error.

Return JSON exactly:
{
  "fixed_content": "complete fixed file content as a string",
  "pr_title": "Fix: <short description under 60 chars>",
  "pr_body": "## What this fixes\\n<one paragraph>\\n\\n## Change made\\n<one paragraph>",
  "change_summary": "one-line description of the exact change made"
}

Rules:
- Make the SMALLEST possible change. Do not refactor or improve unrelated code.
- Do not change function signatures, public APIs, or imports unless that is the fix.
- If you cannot safely generate a fix, return fixed_content as an empty string and explain in change_summary."""


def generate_code_fix(file_path: str, file_content: str, error_context: dict[str, Any]) -> dict[str, Any]:
    user_message = json_dumps_compact({
        "file_path": file_path,
        "error": {
            "what_happened": error_context.get("what_happened", ""),
            "why_it_occurred": error_context.get("why_it_occurred", ""),
            "line_number": error_context.get("line_number", 0),
            "error_message": error_context.get("error_message", ""),
            "fix_description": error_context.get("fix_description", ""),
        },
        "file_content": file_content,
    })
    return call_azure_openai(_CODE_FIX_SYSTEM_PROMPT, user_message)


def create_code_fix_pr(
    issue_id: str,
    file_path: str,
    line_number: int,
    what_happened: str,
    why_it_occurred: str,
    fix_description: str,
    error_message: str,
) -> str:
    """Fetch file, ask AI to fix it, push to a new branch, open PR. Returns PR URL."""
    from ai_analysis import normalize_repo_file_path
    file_path = normalize_repo_file_path(file_path)
    # Try the default branch first; fall back to the currently deployed branch
    try:
        file_content, blob_sha = github_get_file(file_path)
    except Exception:
        deployed_branch = os.getenv("GITHUB_BRANCH", GITHUB_DEFAULT_BRANCH)
        if deployed_branch != GITHUB_DEFAULT_BRANCH:
            file_content, blob_sha = github_get_file_on_branch(file_path, deployed_branch)
        else:
            raise
    fix = generate_code_fix(
        file_path,
        file_content,
        {
            "what_happened": what_happened,
            "why_it_occurred": why_it_occurred,
            "line_number": line_number,
            "error_message": error_message,
            "fix_description": fix_description,
        },
    )
    fixed_content = fix.get("fixed_content", "")
    if not fixed_content:
        raise RuntimeError(f"AI could not generate a safe fix: {fix.get('change_summary', 'no reason given')}")

    import re as _re
    safe_id = _re.sub(r"[^a-z0-9-]", "-", issue_id.lower())
    branch_name = f"ai-fix/{safe_id}-{int(datetime.now(timezone.utc).timestamp())}"
    github_create_branch(branch_name)
    commit_msg = fix.get("change_summary") or f"Fix {issue_id}: {fix_description[:60]}"
    github_update_file(file_path, fixed_content, blob_sha, branch_name, commit_msg)
    pr_url = github_create_pr(
        title=fix.get("pr_title") or f"Fix: {fix_description[:60]}",
        body=fix.get("pr_body") or f"Automated fix for `{file_path}` line {line_number}.\n\n{fix_description}",
        head_branch=branch_name,
    )
    return pr_url


def load_github_dashboard_context(request, msg: str = "", error: str = ""):
    from auth_helpers import require_admin_session
    from fastapi.responses import RedirectResponse

    session_admin = require_admin_session(request)
    if not session_admin:
        return RedirectResponse("/admin/login?msg=Please+log+in+as+an+admin", status_code=303)

    pulls = []
    github_error = error
    if is_github_dashboard_configured():
        try:
            pulls = load_open_pull_requests()
        except Exception as exc:
            record_exception_to_telemetry(exc)
            github_error = str(exc)

    from main import templates
    return templates.TemplateResponse(
        request,
        "admin_github.html",
        {
            "request": request,
            "admin_email": session_admin["email"],
            "msg": msg,
            "error": github_error,
            "pull_requests": pulls,
            "github_repository": GITHUB_REPOSITORY,
            "github_configured": is_github_dashboard_configured(),
            "deploy_qa_default": GITHUB_DEPLOY_QA_DEFAULT,
        },
    )
