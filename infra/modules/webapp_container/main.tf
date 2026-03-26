terraform {
  required_version = ">= 1.6"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 3.110" }
    random  = { source = "hashicorp/random", version = "~> 3.6" }
    archive = { source = "hashicorp/archive", version = "~> 2.5" }
  }
}

locals {
  base_name = lower(replace(var.webapp_name, "-", ""))
  db_user   = "appadmin${random_integer.suffix.result}"
  key_vault_name = substr(
    "${substr(local.base_name, 0, 12)}${var.env_name}${random_integer.suffix.result}kv",
    0,
    24
  )
  postgresql_server_name = substr(
    "${substr(local.base_name, 0, 20)}-${var.env_name}-pg-${random_integer.suffix.result}",
    0,
    63
  )
  apim_name = substr(
    "${substr(local.base_name, 0, 20)}-${var.env_name}-apim-${random_integer.suffix.result}",
    0,
    50
  )
  app_insights_name = substr(
    "${substr(local.base_name, 0, 20)}-${var.env_name}-appi-${random_integer.suffix.result}",
    0,
    60
  )
  redis_name = substr(
    "${substr(local.base_name, 0, 18)}-${var.env_name}-redis-${random_integer.suffix.result}",
    0,
    63
  )
  servicebus_namespace_name = substr(
    "${substr(local.base_name, 0, 18)}-${var.env_name}-sb-${random_integer.suffix.result}",
    0,
    50
  )
  log_analytics_workspace_name = substr(
    "${substr(local.base_name, 0, 18)}-${var.env_name}-law-${random_integer.suffix.result}",
    0,
    63
  )
  attendance_queue_name = "attendance-events"
  iothub_name = substr(
    "${substr(local.base_name, 0, 18)}-${var.env_name}-iot-${random_integer.suffix.result}",
    0,
    50
  )
  function_plan_name = substr(
    "${substr(local.base_name, 0, 18)}-${var.env_name}-func-plan-${random_integer.suffix.result}",
    0,
    40
  )
  function_app_name = var.function_app_name != "" ? var.function_app_name : substr(
    "${substr(local.base_name, 0, 18)}-${var.env_name}-func-${random_integer.suffix.result}",
    0,
    60
  )
  function_storage_account_name = substr(
    "${substr(local.base_name, 0, 10)}${var.env_name}${random_integer.suffix.result}funcsa",
    0,
    24
  )
  vnet_name       = substr("${var.webapp_name}-${var.env_name}-vnet", 0, 64)
  app_subnet_name = "appsvc-integration"
  app_nsg_name    = substr("${var.webapp_name}-${var.env_name}-app-nsg", 0, 80)
  allow_rule_name = "AllowCurrentClientIp"
  base_app_settings = {
    "WEBSITES_ENABLE_APP_SERVICE_STORAGE" = "false"
    "PORT"                                = tostring(var.container_port)
    "ENV"                                 = var.env_name
    "DB_HOST"                             = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.db_host.versionless_id})"
    "DB_NAME"                             = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.db_name.versionless_id})"
    "DB_USERNAME"                         = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.db_username.versionless_id})"
    "DB_PASSWORD"                         = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.db_password.versionless_id})"
    "DATABASE_URL"                        = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.db_connection_string.versionless_id})"
    "CONTOSO_DATABASE_URL"                = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.contoso_db_connection_string.versionless_id})"
    "LITWARE_DATABASE_URL"                = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.litware_db_connection_string.versionless_id})"
    "AZURE_SUBSCRIPTION_ID"               = data.azurerm_client_config.current.subscription_id
    "AZURE_RESOURCE_GROUP"                = data.azurerm_resource_group.rg.name
    "AZURE_POSTGRES_SERVER_NAME"          = local.postgresql_server_name
    "AZURE_USE_MANAGED_IDENTITY"          = "true"
    "REDIS_URL"                           = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.redis_url.versionless_id})"
    "APIM_LOGIN_PATH"                     = "/auth/login"
    "APIM_SIGNUP_PATH"                    = "/auth/signup"
  }
  optional_app_settings = var.contoso_device_connection_string != "" ? {
    "CONTOSO_DEVICE_CONNECTION_STRING" = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.contoso_device_connection_string[0].versionless_id})"
  } : {}
  optional_litware_app_settings = var.litware_device_connection_string != "" ? {
    "LITWARE_DEVICE_CONNECTION_STRING" = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.litware_device_connection_string[0].versionless_id})"
  } : {}
  optional_app_insights_settings = var.application_insights_connection_string != "" ? {
    "APPLICATIONINSIGHTS_CONNECTION_STRING" = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.application_insights_connection_string[0].versionless_id})"
  } : {}
  optional_datadog_settings = merge(
    {
      "DD_SERVICE"        = var.dd_service != "" ? var.dd_service : var.webapp_name
      "DD_ENV"            = var.dd_env != "" ? var.dd_env : var.env_name
      "DD_VERSION"        = var.dd_version
      "DD_TRACE_ENABLED"  = tostring(var.dd_trace_enabled)
      "DD_LOGS_INJECTION" = tostring(var.dd_logs_injection)
    },
    var.dd_agent_host != "" ? {
      "DD_AGENT_HOST" = var.dd_agent_host
    } : {},
    var.dd_trace_agent_url != "" ? {
      "DD_TRACE_AGENT_URL" = var.dd_trace_agent_url
    } : {}
  )
  optional_github_dashboard_settings = var.github_dashboard_token != "" ? {
    "GITHUB_DASHBOARD_TOKEN" = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.github_dashboard_token[0].versionless_id})"
    "GITHUB_REPOSITORY"      = "Pranathi0203/praanthiapp"
  } : {}
  function_app_settings = merge(
    {
      "AzureWebJobsStorage"            = azurerm_storage_account.function.primary_connection_string
      "FUNCTIONS_WORKER_RUNTIME"       = "python"
      "SCM_DO_BUILD_DURING_DEPLOYMENT" = "true"
      "ENABLE_ORYX_BUILD"              = "true"
      "ATTENDANCE_QUEUE_NAME"          = azurerm_servicebus_queue.attendance.name
      "IOTHUB_EVENTHUB_NAME"           = azurerm_iothub.app.name
      "SERVICEBUS_CONNECTION"          = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.servicebus_connection_string.versionless_id})"
      "CONTOSO_DATABASE_URL"           = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.contoso_db_connection_string.versionless_id})"
      "LITWARE_DATABASE_URL"           = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.litware_db_connection_string.versionless_id})"
    },
    var.application_insights_connection_string != "" ? {
      "APPLICATIONINSIGHTS_CONNECTION_STRING" = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.application_insights_connection_string[0].versionless_id})"
    } : {},
    var.iothub_eventhub_connection_string != "" ? {
      "IOTHUB_EVENTHUB_CONNECTION" = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.iothub_eventhub_connection_string[0].versionless_id})"
    } : {}
  )
}

data "azurerm_resource_group" "rg" {
  name = var.rg_name
}

data "azurerm_client_config" "current" {}

data "azurerm_container_registry" "acr" {
  name                = var.acr_name
  resource_group_name = var.acr_rg_name
}

resource "random_integer" "suffix" {
  min = 1000
  max = 9999
}

resource "random_password" "db_admin_password" {
  length  = 24
  special = true
}

data "archive_file" "attendance_functions" {
  type        = "zip"
  source_dir  = "${path.module}/../../../functions"
  output_path = "/tmp/attendance-functions-${var.env_name}.zip"
}

resource "azurerm_virtual_network" "app" {
  name                = local.vnet_name
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name
  address_space       = var.vnet_address_space
}

resource "azurerm_network_security_group" "app" {
  name                = local.app_nsg_name
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name
}

resource "azurerm_subnet" "app_integration" {
  name                 = local.app_subnet_name
  resource_group_name  = data.azurerm_resource_group.rg.name
  virtual_network_name = azurerm_virtual_network.app.name
  address_prefixes     = [var.app_subnet_cidr]

  delegation {
    name = "appservice-delegation"

    service_delegation {
      name    = "Microsoft.Web/serverFarms"
      actions = ["Microsoft.Network/virtualNetworks/subnets/action"]
    }
  }
}

resource "azurerm_subnet_network_security_group_association" "app" {
  subnet_id                 = azurerm_subnet.app_integration.id
  network_security_group_id = azurerm_network_security_group.app.id
}

resource "azurerm_service_plan" "plan" {
  name                = var.plan_name
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location
  os_type             = "Linux"
  sku_name            = var.plan_sku
}

resource "azurerm_log_analytics_workspace" "observability" {
  name                = local.log_analytics_workspace_name
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_analytics_retention_days
}

resource "azurerm_redis_cache" "app" {
  name                 = local.redis_name
  location             = data.azurerm_resource_group.rg.location
  resource_group_name  = data.azurerm_resource_group.rg.name
  capacity             = var.redis_capacity
  family               = var.redis_family
  sku_name             = var.redis_sku_name
  minimum_tls_version  = "1.2"
  non_ssl_port_enabled = false
}

resource "azurerm_servicebus_namespace" "app" {
  name                = local.servicebus_namespace_name
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name
  sku                 = var.servicebus_sku
}

resource "azurerm_servicebus_queue" "attendance" {
  name         = local.attendance_queue_name
  namespace_id = azurerm_servicebus_namespace.app.id
}

resource "azurerm_iothub" "app" {
  name                = local.iothub_name
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location

  sku {
    name     = var.iothub_sku_name
    capacity = var.iothub_capacity
  }
}

# Create IoT Hub device identities and write their connection strings to Key Vault.
# Triggered whenever the IoT Hub is replaced so devices are always in sync.
resource "null_resource" "iothub_devices" {
  triggers = {
    iothub_id = azurerm_iothub.app.id
  }

  depends_on = [
    azurerm_iothub.app,
    azurerm_key_vault_access_policy.terraform_runner,
  ]

  provisioner "local-exec" {
    command = <<-EOT
      set -e

      echo "Installing azure-iot CLI extension..."
      az extension add --name azure-iot --only-show-errors 2>/dev/null || true

      echo "Creating contoso-device..."
      az iot hub device-identity create \
        --hub-name '${azurerm_iothub.app.name}' \
        --device-id 'contoso-device' \
        --only-show-errors 2>/dev/null || true

      echo "Creating litware-device..."
      az iot hub device-identity create \
        --hub-name '${azurerm_iothub.app.name}' \
        --device-id 'litware-device' \
        --only-show-errors 2>/dev/null || true

      echo "Fetching connection strings..."
      CONTOSO_CS=$(az iot hub device-identity connection-string show \
        --hub-name '${azurerm_iothub.app.name}' \
        --device-id 'contoso-device' \
        --query connectionString -o tsv)

      LITWARE_CS=$(az iot hub device-identity connection-string show \
        --hub-name '${azurerm_iothub.app.name}' \
        --device-id 'litware-device' \
        --query connectionString -o tsv)

      echo "Writing connection strings to Key Vault..."
      az keyvault secret set \
        --vault-name '${azurerm_key_vault.app.name}' \
        --name 'contoso-device-connection-string' \
        --value "$CONTOSO_CS" \
        --only-show-errors

      az keyvault secret set \
        --vault-name '${azurerm_key_vault.app.name}' \
        --name 'litware-device-connection-string' \
        --value "$LITWARE_CS" \
        --only-show-errors

      echo "IoT Hub devices ready."
    EOT
  }
}

resource "azurerm_storage_account" "function" {
  name                     = local.function_storage_account_name
  resource_group_name      = data.azurerm_resource_group.rg.name
  location                 = data.azurerm_resource_group.rg.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"
}

resource "azurerm_service_plan" "function" {
  name                = local.function_plan_name
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location
  os_type             = "Linux"
  sku_name            = var.function_app_service_plan_sku
}

resource "azurerm_linux_web_app" "app" {
  name                = var.webapp_name
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location
  service_plan_id     = azurerm_service_plan.plan.id

  identity { type = "SystemAssigned" }

  https_only                = true
  virtual_network_subnet_id = azurerm_subnet.app_integration.id

  site_config {
    always_on                               = true
    health_check_path                       = "/health"
    vnet_route_all_enabled                  = true
    ip_restriction_default_action           = "Deny"
    scm_ip_restriction_default_action       = "Deny"
    container_registry_use_managed_identity = true

    ip_restriction {
      name       = local.allow_rule_name
      priority   = 100
      action     = "Allow"
      ip_address = var.allowed_client_cidr
    }

    scm_ip_restriction {
      name       = local.allow_rule_name
      priority   = 100
      action     = "Allow"
      ip_address = var.allowed_client_cidr
    }

    application_stack {
      docker_registry_url = "https://${data.azurerm_container_registry.acr.login_server}"
      docker_image_name   = "${var.image_name}:${var.image_tag}"
    }
  }

  app_settings = merge(
    local.base_app_settings,
    local.optional_app_settings,
    local.optional_litware_app_settings,
    local.optional_app_insights_settings,
    local.optional_datadog_settings,
    local.optional_github_dashboard_settings,
    var.app_settings
  )
}

resource "azurerm_linux_web_app_slot" "staging" {
  name           = var.staging_slot_name
  app_service_id = azurerm_linux_web_app.app.id

  identity { type = "SystemAssigned" }

  https_only                = true
  virtual_network_subnet_id = azurerm_subnet.app_integration.id

  site_config {
    always_on                               = true
    health_check_path                       = "/health"
    vnet_route_all_enabled                  = true
    ip_restriction_default_action           = "Deny"
    scm_ip_restriction_default_action       = "Deny"
    container_registry_use_managed_identity = true

    ip_restriction {
      name       = local.allow_rule_name
      priority   = 100
      action     = "Allow"
      ip_address = var.allowed_client_cidr
    }

    scm_ip_restriction {
      name       = local.allow_rule_name
      priority   = 100
      action     = "Allow"
      ip_address = var.allowed_client_cidr
    }

    application_stack {
      docker_registry_url = "https://${data.azurerm_container_registry.acr.login_server}"
      docker_image_name   = "${var.image_name}:${var.image_tag}"
    }
  }

  app_settings = merge(
    local.base_app_settings,
    local.optional_app_settings,
    local.optional_litware_app_settings,
    local.optional_app_insights_settings,
    local.optional_datadog_settings,
    local.optional_github_dashboard_settings,
    var.app_settings,
    { "ENV" = "${var.env_name}-staging" }
  )
}

resource "azurerm_role_assignment" "acr_pull" {
  scope                = data.azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_linux_web_app.app.identity[0].principal_id
}

resource "azurerm_role_assignment" "acr_pull_staging" {
  scope                = data.azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_linux_web_app_slot.staging.identity[0].principal_id
}

resource "azurerm_role_assignment" "webapp_rg_contributor" {
  scope                = data.azurerm_resource_group.rg.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_linux_web_app.app.identity[0].principal_id
}

resource "azurerm_role_assignment" "webapp_staging_rg_contributor" {
  scope                = data.azurerm_resource_group.rg.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_linux_web_app_slot.staging.identity[0].principal_id
}

resource "azurerm_postgresql_flexible_server" "db" {
  name                   = local.postgresql_server_name
  resource_group_name    = data.azurerm_resource_group.rg.name
  location               = data.azurerm_resource_group.rg.location
  version                = var.postgres_version
  administrator_login    = local.db_user
  administrator_password = random_password.db_admin_password.result
  sku_name               = var.postgres_sku_name
  storage_mb             = var.postgres_storage_mb

  backup_retention_days = 7

  lifecycle {
    ignore_changes = [
      zone,
      high_availability,
    ]
  }
}

resource "azurerm_postgresql_flexible_server_firewall_rule" "allow_azure_services" {
  name             = "AllowAzureServices"
  server_id        = azurerm_postgresql_flexible_server.db.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_postgresql_flexible_server_database" "app" {
  name      = var.postgres_database_name
  server_id = azurerm_postgresql_flexible_server.db.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_postgresql_flexible_server_database" "contoso" {
  name      = var.contoso_database_name
  server_id = azurerm_postgresql_flexible_server.db.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_postgresql_flexible_server_database" "litware" {
  name      = var.litware_database_name
  server_id = azurerm_postgresql_flexible_server.db.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# ─── PostgreSQL monitoring parameters ──────────────────────────────────────────
# These parameters enable logging, query stats, and wait-event sampling needed
# for Azure Monitor alerts (Layer 2) and Datadog Database Monitoring (Layer 3).

# Allow pg_stat_statements to be created in databases (Azure extension allowlist).
resource "azurerm_postgresql_flexible_server_configuration" "pg_azure_extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.db.id
  value     = "pg_stat_statements"
}

# Load pg_stat_statements at server startup.
# NOTE: changing shared_preload_libraries triggers an automatic server restart.
resource "azurerm_postgresql_flexible_server_configuration" "pg_shared_preload_libraries" {
  name      = "shared_preload_libraries"
  server_id = azurerm_postgresql_flexible_server.db.id
  value     = "pg_stat_statements"
}

# Track all statements including those inside functions and stored procedures.
resource "azurerm_postgresql_flexible_server_configuration" "pg_stat_statements_track" {
  name       = "pg_stat_statements.track"
  server_id  = azurerm_postgresql_flexible_server.db.id
  value      = "all"
  depends_on = [azurerm_postgresql_flexible_server_configuration.pg_shared_preload_libraries]
}

# Log any query exceeding this duration in milliseconds.
# Feeds into the "slow query" alert and the Query Store.
resource "azurerm_postgresql_flexible_server_configuration" "pg_log_min_duration_statement" {
  name      = "log_min_duration_statement"
  server_id = azurerm_postgresql_flexible_server.db.id
  value     = tostring(var.pg_log_min_duration_ms)
}

# Log checkpoint activity — indicates WAL pressure or I/O saturation.
resource "azurerm_postgresql_flexible_server_configuration" "pg_log_checkpoints" {
  name      = "log_checkpoints"
  server_id = azurerm_postgresql_flexible_server.db.id
  value     = "on"
}

# Log every new connection — lets you see connection spikes and churn.
resource "azurerm_postgresql_flexible_server_configuration" "pg_log_connections" {
  name      = "log_connections"
  server_id = azurerm_postgresql_flexible_server.db.id
  value     = "on"
}

# Log every disconnection — pairs with log_connections to detect pool exhaustion.
resource "azurerm_postgresql_flexible_server_configuration" "pg_log_disconnections" {
  name      = "log_disconnections"
  server_id = azurerm_postgresql_flexible_server.db.id
  value     = "on"
}

# Log lock waits exceeding deadlock_timeout — surfaces contention before it escalates.
resource "azurerm_postgresql_flexible_server_configuration" "pg_log_lock_waits" {
  name      = "log_lock_waits"
  server_id = azurerm_postgresql_flexible_server.db.id
  value     = "on"
}

# How long a transaction waits for a lock (ms) before deadlock detection runs.
# Lowering this makes deadlocks detected and logged faster.
resource "azurerm_postgresql_flexible_server_configuration" "pg_deadlock_timeout" {
  name      = "deadlock_timeout"
  server_id = azurerm_postgresql_flexible_server.db.id
  value     = tostring(var.pg_deadlock_timeout_ms)
}

# Log creation of any temporary file above this size (KB). 0 = log all temp files.
# Temp files mean a sort or hash join spilled to disk — usually a missing index.
resource "azurerm_postgresql_flexible_server_configuration" "pg_log_temp_files" {
  name      = "log_temp_files"
  server_id = azurerm_postgresql_flexible_server.db.id
  value     = tostring(var.pg_log_temp_files_kb)
}

# Azure Query Store — persists query performance history for trend analysis
# and surfaces regressions across deployments.
resource "azurerm_postgresql_flexible_server_configuration" "pg_qs_query_capture_mode" {
  name      = "pg_qs.query_capture_mode"
  server_id = azurerm_postgresql_flexible_server.db.id
  value     = "all"
}

resource "azurerm_postgresql_flexible_server_configuration" "pg_qs_retention_period" {
  name      = "pg_qs.retention_period_in_days"
  server_id = azurerm_postgresql_flexible_server.db.id
  value     = tostring(var.pg_qs_retention_days)
}

# Azure wait event sampling — shows what queries are blocked on (locks, I/O, CPU).
# Prerequisite for the "Intelligent Performance" blade in the Azure portal.
resource "azurerm_postgresql_flexible_server_configuration" "pgms_wait_sampling_query_capture_mode" {
  name      = "pgms_wait_sampling.query_capture_mode"
  server_id = azurerm_postgresql_flexible_server.db.id
  value     = "all"
}

resource "azurerm_key_vault" "app" {
  name                = local.key_vault_name
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"
}

resource "azurerm_key_vault_access_policy" "terraform_runner" {
  key_vault_id = azurerm_key_vault.app.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = data.azurerm_client_config.current.object_id

  secret_permissions = ["Get", "List", "Set", "Delete", "Recover", "Purge"]
}

resource "azurerm_key_vault_access_policy" "webapp_identity" {
  key_vault_id = azurerm_key_vault.app.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_linux_web_app.app.identity[0].principal_id

  secret_permissions = ["Get", "List"]
}

resource "azurerm_key_vault_access_policy" "webapp_staging_identity" {
  key_vault_id = azurerm_key_vault.app.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_linux_web_app_slot.staging.identity[0].principal_id

  secret_permissions = ["Get", "List"]
}

resource "azurerm_linux_function_app" "attendance" {
  name                = local.function_app_name
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location
  service_plan_id     = azurerm_service_plan.function.id

  storage_account_name       = azurerm_storage_account.function.name
  storage_account_access_key = azurerm_storage_account.function.primary_access_key

  identity { type = "SystemAssigned" }

  functions_extension_version = "~4"
  https_only                  = true
  zip_deploy_file             = data.archive_file.attendance_functions.output_path

  site_config {
    application_stack {
      python_version = var.function_python_version
    }
  }

  app_settings = local.function_app_settings
}

data "azurerm_monitor_diagnostic_categories" "webapp" {
  resource_id = azurerm_linux_web_app.app.id
}

data "azurerm_monitor_diagnostic_categories" "webapp_staging" {
  resource_id = azurerm_linux_web_app_slot.staging.id
}

data "azurerm_monitor_diagnostic_categories" "function_app" {
  resource_id = azurerm_linux_function_app.attendance.id
}

data "azurerm_monitor_diagnostic_categories" "servicebus" {
  resource_id = azurerm_servicebus_namespace.app.id
}

data "azurerm_monitor_diagnostic_categories" "redis" {
  resource_id = azurerm_redis_cache.app.id
}

data "azurerm_monitor_diagnostic_categories" "postgres" {
  resource_id = azurerm_postgresql_flexible_server.db.id
}

data "azurerm_monitor_diagnostic_categories" "iothub" {
  resource_id = azurerm_iothub.app.id
}

resource "azurerm_monitor_diagnostic_setting" "webapp" {
  name                           = "${var.webapp_name}-${var.env_name}-diag"
  target_resource_id             = azurerm_linux_web_app.app.id
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.observability.id
  log_analytics_destination_type = "Dedicated"

  dynamic "enabled_log" {
    for_each = toset(data.azurerm_monitor_diagnostic_categories.webapp.log_category_types)
    content {
      category = enabled_log.value
    }
  }

  dynamic "metric" {
    for_each = toset(data.azurerm_monitor_diagnostic_categories.webapp.metrics)
    content {
      category = metric.value
      enabled  = true
    }
  }
}

resource "azurerm_monitor_diagnostic_setting" "webapp_staging" {
  name                           = "${var.webapp_name}-${var.env_name}-staging-diag"
  target_resource_id             = azurerm_linux_web_app_slot.staging.id
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.observability.id
  log_analytics_destination_type = "Dedicated"

  dynamic "enabled_log" {
    for_each = toset(data.azurerm_monitor_diagnostic_categories.webapp_staging.log_category_types)
    content {
      category = enabled_log.value
    }
  }

  dynamic "metric" {
    for_each = toset(data.azurerm_monitor_diagnostic_categories.webapp_staging.metrics)
    content {
      category = metric.value
      enabled  = true
    }
  }
}

resource "azurerm_monitor_diagnostic_setting" "function_app" {
  name                           = "${local.function_app_name}-diag"
  target_resource_id             = azurerm_linux_function_app.attendance.id
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.observability.id
  log_analytics_destination_type = "Dedicated"

  dynamic "enabled_log" {
    for_each = toset(data.azurerm_monitor_diagnostic_categories.function_app.log_category_types)
    content {
      category = enabled_log.value
    }
  }

  dynamic "metric" {
    for_each = toset(data.azurerm_monitor_diagnostic_categories.function_app.metrics)
    content {
      category = metric.value
      enabled  = true
    }
  }
}

resource "azurerm_monitor_diagnostic_setting" "servicebus" {
  name                           = "${azurerm_servicebus_namespace.app.name}-diag"
  target_resource_id             = azurerm_servicebus_namespace.app.id
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.observability.id
  log_analytics_destination_type = "Dedicated"

  dynamic "enabled_log" {
    for_each = toset(data.azurerm_monitor_diagnostic_categories.servicebus.log_category_types)
    content {
      category = enabled_log.value
    }
  }

  dynamic "metric" {
    for_each = toset(data.azurerm_monitor_diagnostic_categories.servicebus.metrics)
    content {
      category = metric.value
      enabled  = true
    }
  }
}

resource "azurerm_monitor_diagnostic_setting" "redis" {
  name                           = "${azurerm_redis_cache.app.name}-diag"
  target_resource_id             = azurerm_redis_cache.app.id
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.observability.id
  log_analytics_destination_type = "Dedicated"

  dynamic "enabled_log" {
    for_each = toset(data.azurerm_monitor_diagnostic_categories.redis.log_category_types)
    content {
      category = enabled_log.value
    }
  }

  dynamic "metric" {
    for_each = toset(data.azurerm_monitor_diagnostic_categories.redis.metrics)
    content {
      category = metric.value
      enabled  = true
    }
  }
}

resource "azurerm_monitor_diagnostic_setting" "postgres" {
  name                           = "${azurerm_postgresql_flexible_server.db.name}-diag"
  target_resource_id             = azurerm_postgresql_flexible_server.db.id
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.observability.id
  log_analytics_destination_type = "Dedicated"

  dynamic "enabled_log" {
    for_each = toset(data.azurerm_monitor_diagnostic_categories.postgres.log_category_types)
    content {
      category = enabled_log.value
    }
  }

  dynamic "metric" {
    for_each = toset(data.azurerm_monitor_diagnostic_categories.postgres.metrics)
    content {
      category = metric.value
      enabled  = true
    }
  }
}

resource "azurerm_monitor_diagnostic_setting" "iothub" {
  name                           = "${azurerm_iothub.app.name}-diag"
  target_resource_id             = azurerm_iothub.app.id
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.observability.id
  log_analytics_destination_type = "Dedicated"

  dynamic "enabled_log" {
    for_each = toset(data.azurerm_monitor_diagnostic_categories.iothub.log_category_types)
    content {
      category = enabled_log.value
    }
  }

  dynamic "metric" {
    for_each = toset(data.azurerm_monitor_diagnostic_categories.iothub.metrics)
    content {
      category = metric.value
      enabled  = true
    }
  }
}

resource "azurerm_key_vault_access_policy" "function_identity" {
  key_vault_id = azurerm_key_vault.app.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_linux_function_app.attendance.identity[0].principal_id

  secret_permissions = ["Get", "List"]
}

resource "azurerm_key_vault_secret" "db_host" {
  name         = "db-host"
  value        = azurerm_postgresql_flexible_server.db.fqdn
  key_vault_id = azurerm_key_vault.app.id
  depends_on   = [azurerm_key_vault_access_policy.terraform_runner]
}

resource "azurerm_key_vault_secret" "db_name" {
  name         = "db-name"
  value        = azurerm_postgresql_flexible_server_database.app.name
  key_vault_id = azurerm_key_vault.app.id
  depends_on   = [azurerm_key_vault_access_policy.terraform_runner]
}

resource "azurerm_key_vault_secret" "db_username" {
  name         = "db-username"
  value        = azurerm_postgresql_flexible_server.db.administrator_login
  key_vault_id = azurerm_key_vault.app.id
  depends_on   = [azurerm_key_vault_access_policy.terraform_runner]
}

resource "azurerm_key_vault_secret" "db_password" {
  name         = "db-password"
  value        = random_password.db_admin_password.result
  key_vault_id = azurerm_key_vault.app.id
  depends_on   = [azurerm_key_vault_access_policy.terraform_runner]
}

resource "azurerm_key_vault_secret" "db_connection_string" {
  name         = "db-connection-string"
  value        = "postgresql://${azurerm_postgresql_flexible_server.db.administrator_login}:${urlencode(random_password.db_admin_password.result)}@${azurerm_postgresql_flexible_server.db.fqdn}:5432/${azurerm_postgresql_flexible_server_database.app.name}?sslmode=require"
  key_vault_id = azurerm_key_vault.app.id
  depends_on   = [azurerm_key_vault_access_policy.terraform_runner]
}

resource "azurerm_key_vault_secret" "contoso_db_connection_string" {
  name         = "contoso-db-connection-string"
  value        = "postgresql://${azurerm_postgresql_flexible_server.db.administrator_login}:${urlencode(random_password.db_admin_password.result)}@${azurerm_postgresql_flexible_server.db.fqdn}:5432/${azurerm_postgresql_flexible_server_database.contoso.name}?sslmode=require"
  key_vault_id = azurerm_key_vault.app.id
  depends_on   = [azurerm_key_vault_access_policy.terraform_runner]
}

resource "azurerm_key_vault_secret" "litware_db_connection_string" {
  name         = "litware-db-connection-string"
  value        = "postgresql://${azurerm_postgresql_flexible_server.db.administrator_login}:${urlencode(random_password.db_admin_password.result)}@${azurerm_postgresql_flexible_server.db.fqdn}:5432/${azurerm_postgresql_flexible_server_database.litware.name}?sslmode=require"
  key_vault_id = azurerm_key_vault.app.id
  depends_on   = [azurerm_key_vault_access_policy.terraform_runner]
}

resource "azurerm_key_vault_secret" "redis_url" {
  name         = "redis-url"
  value        = "rediss://:${azurerm_redis_cache.app.primary_access_key}@${azurerm_redis_cache.app.hostname}:${azurerm_redis_cache.app.ssl_port}/0"
  key_vault_id = azurerm_key_vault.app.id
  depends_on   = [azurerm_key_vault_access_policy.terraform_runner]
}

resource "azurerm_key_vault_secret" "servicebus_connection_string" {
  name         = "servicebus-connection-string"
  value        = azurerm_servicebus_namespace.app.default_primary_connection_string
  key_vault_id = azurerm_key_vault.app.id
  depends_on   = [azurerm_key_vault_access_policy.terraform_runner]
}

resource "azurerm_key_vault_secret" "contoso_device_connection_string" {
  count        = var.contoso_device_connection_string != "" ? 1 : 0
  name         = "contoso-device-connection-string"
  value        = var.contoso_device_connection_string
  key_vault_id = azurerm_key_vault.app.id
  depends_on   = [azurerm_key_vault_access_policy.terraform_runner]
}

resource "azurerm_key_vault_secret" "litware_device_connection_string" {
  count        = var.litware_device_connection_string != "" ? 1 : 0
  name         = "litware-device-connection-string"
  value        = var.litware_device_connection_string
  key_vault_id = azurerm_key_vault.app.id
  depends_on   = [azurerm_key_vault_access_policy.terraform_runner]
}

resource "azurerm_key_vault_secret" "iothub_eventhub_connection_string" {
  count        = var.iothub_eventhub_connection_string != "" ? 1 : 0
  name         = "iothub-eventhub-connection-string"
  value        = var.iothub_eventhub_connection_string
  key_vault_id = azurerm_key_vault.app.id
  depends_on   = [azurerm_key_vault_access_policy.terraform_runner]
}

resource "azurerm_key_vault_secret" "application_insights_connection_string" {
  count        = var.application_insights_connection_string != "" ? 1 : 0
  name         = "applicationinsights-connection-string"
  value        = var.application_insights_connection_string
  key_vault_id = azurerm_key_vault.app.id
  depends_on   = [azurerm_key_vault_access_policy.terraform_runner]
}

resource "azurerm_key_vault_secret" "github_dashboard_token" {
  count        = var.github_dashboard_token != "" ? 1 : 0
  name         = "github-dashboard-token"
  value        = var.github_dashboard_token
  key_vault_id = azurerm_key_vault.app.id
  depends_on   = [azurerm_key_vault_access_policy.terraform_runner]
}

# ─── Database Monitoring Alerts ────────────────────────────────────────────────
# All alert resources are conditional on alert_email being set.
# Set var.alert_email in the environment to enable alerting.

resource "azurerm_monitor_action_group" "db_alerts" {
  count               = var.alert_email != "" ? 1 : 0
  name                = "${var.webapp_name}-${var.env_name}-db-ag"
  resource_group_name = data.azurerm_resource_group.rg.name
  short_name          = "db-alerts"

  email_receiver {
    name                    = "ops-email"
    email_address           = var.alert_email
    use_common_alert_schema = true
  }

  dynamic "webhook_receiver" {
    for_each = var.alert_webhook_url != "" ? [1] : []
    content {
      name                    = "ops-webhook"
      service_uri             = var.alert_webhook_url
      use_common_alert_schema = true
    }
  }
}

# ── Metric Alerts ──────────────────────────────────────────────────────────────
# These fire on Azure platform numbers directly — no logs needed.

# Fires when active connections approach the B1ms hard limit (~50).
# Root cause: no connection pooling — each FastAPI/Function request opens a new connection.
resource "azurerm_monitor_metric_alert" "pg_high_connections" {
  count               = var.alert_email != "" ? 1 : 0
  name                = "${var.webapp_name}-${var.env_name}-pg-connections"
  resource_group_name = data.azurerm_resource_group.rg.name
  scopes              = [azurerm_postgresql_flexible_server.db.id]
  description         = "PostgreSQL active connections above ${var.pg_alert_max_connections}. Risk of FATAL: too many connections."
  severity            = 2
  frequency           = "PT1M"
  window_size         = "PT5M"

  criteria {
    metric_namespace = "Microsoft.DBforPostgreSQL/flexibleServers"
    metric_name      = "active_connections"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = var.pg_alert_max_connections
  }

  action {
    action_group_id = azurerm_monitor_action_group.db_alerts[0].id
  }
}

# Fires when CPU is sustained high — indicates a runaway query or missing index causing full scans.
resource "azurerm_monitor_metric_alert" "pg_high_cpu" {
  count               = var.alert_email != "" ? 1 : 0
  name                = "${var.webapp_name}-${var.env_name}-pg-cpu"
  resource_group_name = data.azurerm_resource_group.rg.name
  scopes              = [azurerm_postgresql_flexible_server.db.id]
  description         = "PostgreSQL CPU above ${var.pg_alert_cpu_threshold}% for 15 minutes."
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"

  criteria {
    metric_namespace = "Microsoft.DBforPostgreSQL/flexibleServers"
    metric_name      = "cpu_percent"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = var.pg_alert_cpu_threshold
  }

  action {
    action_group_id = azurerm_monitor_action_group.db_alerts[0].id
  }
}

# Fires when storage is filling up — attendance_events grows unbounded without archival.
resource "azurerm_monitor_metric_alert" "pg_high_storage" {
  count               = var.alert_email != "" ? 1 : 0
  name                = "${var.webapp_name}-${var.env_name}-pg-storage"
  resource_group_name = data.azurerm_resource_group.rg.name
  scopes              = [azurerm_postgresql_flexible_server.db.id]
  description         = "PostgreSQL storage above ${var.pg_alert_storage_threshold}%."
  severity            = 1
  frequency           = "PT15M"
  window_size         = "PT1H"

  criteria {
    metric_namespace = "Microsoft.DBforPostgreSQL/flexibleServers"
    metric_name      = "storage_percent"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = var.pg_alert_storage_threshold
  }

  action {
    action_group_id = azurerm_monitor_action_group.db_alerts[0].id
  }
}

# Fires when I/O is saturated — symptom of sequential scans from missing indexes.
resource "azurerm_monitor_metric_alert" "pg_high_iops" {
  count               = var.alert_email != "" ? 1 : 0
  name                = "${var.webapp_name}-${var.env_name}-pg-iops"
  resource_group_name = data.azurerm_resource_group.rg.name
  scopes              = [azurerm_postgresql_flexible_server.db.id]
  description         = "PostgreSQL IOPS above ${var.pg_alert_iops_threshold}%."
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT5M"

  criteria {
    metric_namespace = "Microsoft.DBforPostgreSQL/flexibleServers"
    metric_name      = "disk_iops_consumed_percentage"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = var.pg_alert_iops_threshold
  }

  action {
    action_group_id = azurerm_monitor_action_group.db_alerts[0].id
  }
}

# Fires on repeated failed connections — could be bad credentials, SSL issues, or pool exhaustion.
resource "azurerm_monitor_metric_alert" "pg_failed_connections" {
  count               = var.alert_email != "" ? 1 : 0
  name                = "${var.webapp_name}-${var.env_name}-pg-conn-failures"
  resource_group_name = data.azurerm_resource_group.rg.name
  scopes              = [azurerm_postgresql_flexible_server.db.id]
  description         = "PostgreSQL connection failures spiking."
  severity            = 2
  frequency           = "PT1M"
  window_size         = "PT5M"

  criteria {
    metric_namespace = "Microsoft.DBforPostgreSQL/flexibleServers"
    metric_name      = "connections_failed"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 5
  }

  action {
    action_group_id = azurerm_monitor_action_group.db_alerts[0].id
  }
}

# ── Log-Based Alerts (Scheduled Query Rules) ───────────────────────────────────
# These run KQL queries against Log Analytics every 5 minutes.
# Requires Layer 1 server parameters to be applied first.

# Fires the moment a deadlock is detected in PostgreSQL logs.
# Enabled by: log_lock_waits = on, deadlock_timeout = 1000
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "pg_deadlock" {
  count               = var.alert_email != "" ? 1 : 0
  name                = "${var.webapp_name}-${var.env_name}-pg-deadlock"
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location
  description         = "A deadlock was detected in PostgreSQL. Check attendance_events concurrent writes."
  severity            = 1
  evaluation_frequency = "PT5M"
  window_duration      = "PT5M"
  scopes               = [azurerm_log_analytics_workspace.observability.id]

  criteria {
    query                   = <<-QUERY
      AzureDiagnostics
      | where ResourceProvider == "MICROSOFT.DBFORPOSTGRESQL"
      | where Category == "PostgreSQLLogs"
      | where Message contains "deadlock detected"
    QUERY
    time_aggregation_method = "Count"
    threshold               = 0
    operator                = "GreaterThan"

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.db_alerts[0].id]
  }
}

# Fires when 3 or more queries exceed 5 seconds in a 5-minute window.
# Enabled by: log_min_duration_statement = 1000 (logs all queries > 1s)
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "pg_slow_query" {
  count               = var.alert_email != "" ? 1 : 0
  name                = "${var.webapp_name}-${var.env_name}-pg-slow-query"
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location
  description         = "Multiple queries exceeding 5 seconds. Likely missing index or full table scan."
  severity            = 2
  evaluation_frequency = "PT5M"
  window_duration      = "PT5M"
  scopes               = [azurerm_log_analytics_workspace.observability.id]

  criteria {
    query                   = <<-QUERY
      AzureDiagnostics
      | where ResourceProvider == "MICROSOFT.DBFORPOSTGRESQL"
      | where Category == "PostgreSQLLogs"
      | where Message contains "duration: "
      | parse Message with * "duration: " dur_str " ms" *
      | where todouble(dur_str) >= 5000
    QUERY
    time_aggregation_method = "Count"
    threshold               = 2
    operator                = "GreaterThan"

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.db_alerts[0].id]
  }
}

# Fires when any transaction is logged waiting for a lock.
# Enabled by: log_lock_waits = on, deadlock_timeout = 1000
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "pg_lock_wait" {
  count               = var.alert_email != "" ? 1 : 0
  name                = "${var.webapp_name}-${var.env_name}-pg-lock-wait"
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location
  description         = "Lock wait detected in PostgreSQL. Possible contention on attendance_events or app_users."
  severity            = 2
  evaluation_frequency = "PT5M"
  window_duration      = "PT5M"
  scopes               = [azurerm_log_analytics_workspace.observability.id]

  criteria {
    query                   = <<-QUERY
      AzureDiagnostics
      | where ResourceProvider == "MICROSOFT.DBFORPOSTGRESQL"
      | where Category == "PostgreSQLLogs"
      | where Message contains "still waiting for lock"
          or Message contains "lock wait timeout"
    QUERY
    time_aggregation_method = "Count"
    threshold               = 0
    operator                = "GreaterThan"

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.db_alerts[0].id]
  }
}

# Fires when the application starts hitting connection limits.
# Root cause: no connection pooling in get_conn() — each request opens a new psycopg connection.
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "app_connection_exhaustion" {
  count               = var.alert_email != "" ? 1 : 0
  name                = "${var.webapp_name}-${var.env_name}-pg-conn-exhaustion"
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location
  description         = "Application receiving FATAL: too many connections from PostgreSQL."
  severity            = 1
  evaluation_frequency = "PT5M"
  window_duration      = "PT5M"
  scopes               = [azurerm_log_analytics_workspace.observability.id]

  criteria {
    query                   = <<-QUERY
      AppTraces
      | where Message contains "too many connections"
          or Message contains "remaining connection slots"
    QUERY
    time_aggregation_method = "Count"
    threshold               = 0
    operator                = "GreaterThan"

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.db_alerts[0].id]
  }
}

# Fires when the attendance Azure Function fails to write events to PostgreSQL.
# Covers: deadlock kills, connection errors, schema lock timeouts from ensure_schema().
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "attendance_processor_failures" {
  count               = var.alert_email != "" ? 1 : 0
  name                = "${var.webapp_name}-${var.env_name}-attendance-failures"
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location
  description         = "Attendance processor failing to write to database. Events may be lost."
  severity            = 1
  evaluation_frequency = "PT5M"
  window_duration      = "PT5M"
  scopes               = [azurerm_log_analytics_workspace.observability.id]

  criteria {
    query                   = <<-QUERY
      FunctionAppLogs
      | where Level == "Error"
      | where Message contains "attendance_processor_failed"
    QUERY
    time_aggregation_method = "Count"
    threshold               = 2
    operator                = "GreaterThan"

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.db_alerts[0].id]
  }
}

resource "azurerm_api_management" "app" {
  name                = local.apim_name
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name
  publisher_name      = var.apim_publisher_name
  publisher_email     = var.apim_publisher_email
  sku_name            = var.apim_sku_name
}

resource "azurerm_api_management_api" "auth" {
  name                = "auth-api"
  resource_group_name = data.azurerm_resource_group.rg.name
  api_management_name = azurerm_api_management.app.name
  revision            = "1"
  display_name        = "Auth API"
  path                = "auth"
  protocols           = ["https"]
  service_url         = "https://${azurerm_linux_web_app.app.default_hostname}"
}

resource "azurerm_api_management_api_operation" "login" {
  operation_id        = "login"
  api_name            = azurerm_api_management_api.auth.name
  api_management_name = azurerm_api_management.app.name
  resource_group_name = data.azurerm_resource_group.rg.name
  display_name        = "Login"
  method              = "POST"
  url_template        = "/login"

  response {
    status_code = 200
  }
}

resource "azurerm_api_management_api_operation" "signup" {
  operation_id        = "signup"
  api_name            = azurerm_api_management_api.auth.name
  api_management_name = azurerm_api_management.app.name
  resource_group_name = data.azurerm_resource_group.rg.name
  display_name        = "Signup"
  method              = "POST"
  url_template        = "/signup"

  response {
    status_code = 200
  }
}
