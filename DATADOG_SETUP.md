# Datadog Setup

This app now supports optional Datadog APM tracing and log correlation.

## What was added

- `ddtrace` in [src/requirements.txt](/Users/pranathireddy/Downloads/pranathi-app/src/requirements.txt)
- opt-in Datadog tracing in [src/main.py](/Users/pranathireddy/Downloads/pranathi-app/src/main.py)
- Terraform variables and app settings for Datadog in `infra/envs/*` and `infra/modules/webapp_container`

## Minimum app settings

Set these values on the web app:

- `DD_TRACE_ENABLED=true`
- `DD_SERVICE=pranathi-app`
- `DD_ENV=dev` or `qa`
- `DD_VERSION=<release-or-image-tag>`
- `DD_LOGS_INJECTION=true`

Then configure one of these so the app can reach the Datadog trace intake:

- `DD_AGENT_HOST=<datadog-agent-hostname-or-ip>`
- `DD_TRACE_AGENT_URL=http://<host>:8126`

If both are empty, traces are generated in-process but cannot be exported anywhere.

## Recommended first rollout

1. Keep Azure Monitor enabled for now.
2. Turn on Datadog only in one environment first, such as `dev`.
3. Confirm that requests to `/health` and `/hello/{name}` appear in Datadog APM.
4. After traces appear, use the Datadog UI to build monitors for:
   - error rate
   - p95 latency
   - request volume

## Datadog-side setup

Because this app runs on Azure App Service, you still need Datadog intake on the platform side.

- Install the Azure integration in Datadog to pull Azure App Service metrics.
- Run a Datadog Agent or sidecar that the app can send traces to.
- Point `DD_AGENT_HOST` or `DD_TRACE_AGENT_URL` at that agent.

Official docs:

- Azure App Service integration: https://docs.datadoghq.com/integrations/azure-app-services/
- Python tracing: https://docs.datadoghq.com/tracing/trace_collection/automatic_instrumentation/dd_libraries/python/

## Terraform example

Example `terraform.tfvars` values for `dev`:

```hcl
dd_trace_enabled = true
dd_service       = "pranathi-app"
dd_env           = "dev"
dd_version       = "2026-03-22"
dd_logs_injection = true
dd_agent_host    = "10.0.0.10"
```
