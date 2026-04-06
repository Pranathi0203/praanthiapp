# Claude Desktop App Insights MCP

This repo now includes a read-only MCP server for Claude Desktop:

- [claude_app_insights_mcp.py](/Users/pranathireddy/Downloads/pranathi-app/scripts/claude_app_insights_mcp.py)

It lets Claude Desktop:

- read recent App Insights / Log Analytics error signals
- group them
- explain the likely cause
- propose a safe operational fix for some known Azure failures
- apply a supported fix after explicit user approval

It does not perform broad or destructive remediation. The current automation is intentionally narrow and confirmation-gated.

## Prerequisites

1. Install Azure CLI and sign in:

```bash
az login
```

2. Make sure Claude Desktop can reach Python 3 on your machine.

3. Set either:

- `LOG_ANALYTICS_WORKSPACE_ID`

or:

- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `APPLICATION_INSIGHTS_NAME`

The second option is easier if you want the server to resolve the workspace from the App Insights resource automatically.

4. If you want Claude to apply supported fixes, also set:

- `WEBAPP_NAME`
- `FUNCTION_APP_NAME`
- `AZURE_POSTGRES_SERVER_NAME`

Optional safety switches:

- `ALLOW_WEBAPP_RESTART=true`
- `ALLOW_FUNCTIONAPP_RESTART=true`
- `ALLOW_POSTGRES_RESTART=true`

Set any of those to `false` to disable that remediation path.

## Claude Desktop config

Add this to your Claude Desktop MCP config file.

macOS path:

```text
~/Library/Application Support/Claude/claude_desktop_config.json
```

Example:

```json
{
  "mcpServers": {
    "pranathi-app-insights": {
      "command": "python3",
      "args": [
        "/Users/pranathireddy/Downloads/pranathi-app/scripts/claude_app_insights_mcp.py"
      ],
      "env": {
        "AZURE_SUBSCRIPTION_ID": "<subscription-id>",
        "AZURE_RESOURCE_GROUP": "<resource-group>",
        "APPLICATION_INSIGHTS_NAME": "myappdev8017f-dev-appi-6240",
        "WEBAPP_NAME": "<web-app-name>",
        "FUNCTION_APP_NAME": "<function-app-name>",
        "AZURE_POSTGRES_SERVER_NAME": "<postgres-server-name>",
        "ALLOW_WEBAPP_RESTART": "true",
        "ALLOW_FUNCTIONAPP_RESTART": "true",
        "ALLOW_POSTGRES_RESTART": "true"
      }
    }
  }
}
```

If you already know the workspace id, you can use:

```json
{
  "mcpServers": {
    "pranathi-app-insights": {
      "command": "python3",
      "args": [
        "/Users/pranathireddy/Downloads/pranathi-app/scripts/claude_app_insights_mcp.py"
      ],
      "env": {
        "LOG_ANALYTICS_WORKSPACE_ID": "<workspace-customer-id>"
      }
    }
  }
}
```

Restart Claude Desktop after saving the config.

## Tools exposed to Claude

### `find_recent_errors`

Inputs:

- `minutes`
- `limit`

Returns grouped recent errors from:

- `AppExceptions`
- `AppTraces`
- `FunctionAppLogs`
- `AzureDiagnostics`
- `PGSQLServerLogs`

### `analyze_recent_errors`

Inputs:

- `minutes`
- `limit`

Returns:

- top recent failures
- affected component
- likely cause
- why it is probably happening
- confidence

### `propose_fix_for_error`

Inputs:

- `source_table`
- `error_type`
- `example_message`

Returns:

- safest supported fix type
- targeted Azure component
- whether automation is available in the current config
- reminder that user approval is required

### `apply_fix_for_error`

Inputs:

- `fix_type`
- `confirmed`
- `source_table`
- `error_type`
- `example_message`

Current supported fix types:

- `restart_webapp`
- `restart_function_app`
- `start_postgres_server`
- `restart_postgres_server`

This tool refuses to run unless `confirmed=true`.

## Example prompts for Claude Desktop

- `Check the last 60 minutes of App Insights errors and tell me the most likely root cause.`
- `Analyze recent failures and explain which component is affected and why.`
- `Summarize the top recurring errors from the last 2 hours.`
- `Analyze the latest error and ask me before applying any fix.`
- `If a safe fix is available, propose it first and wait for my approval.`

## Notes

- The diagnostics tools are read-only, but the remediation tool can perform a small set of Azure restart actions after approval.
- It uses `az account get-access-token` for authentication.
- Restart-based remediation requires Azure CLI access to the target resources.
- Claude will be best at diagnosis when your custom telemetry is structured, which this app already emits through `log_event(...)`.
