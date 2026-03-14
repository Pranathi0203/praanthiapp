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
    "WEBSITES_ENABLE_APP_SERVICE_STORAGE"   = "false"
    "PORT"                                  = tostring(var.container_port)
    "ENV"                                   = var.env_name
    "DB_HOST"                               = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.db_host.versionless_id})"
    "DB_NAME"                               = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.db_name.versionless_id})"
    "DB_USERNAME"                           = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.db_username.versionless_id})"
    "DB_PASSWORD"                           = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.db_password.versionless_id})"
    "DATABASE_URL"                          = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.db_connection_string.versionless_id})"
    "CONTOSO_DATABASE_URL"                  = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.contoso_db_connection_string.versionless_id})"
    "LITWARE_DATABASE_URL"                  = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.litware_db_connection_string.versionless_id})"
    "REDIS_URL"                             = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.redis_url.versionless_id})"
    "APIM_LOGIN_PATH"                       = "/auth/login"
    "APIM_SIGNUP_PATH"                      = "/auth/signup"
  }
  optional_app_settings = var.contoso_device_connection_string != "" ? {
    "CONTOSO_DEVICE_CONNECTION_STRING" = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.contoso_device_connection_string[0].versionless_id})"
  } : {}
  optional_litware_app_settings = var.litware_device_connection_string != "" ? {
    "LITWARE_DEVICE_CONNECTION_STRING" = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.litware_device_connection_string[0].versionless_id})"
  } : {}
  function_app_settings = merge(
    {
      "AzureWebJobsStorage"      = azurerm_storage_account.function.primary_connection_string
      "FUNCTIONS_WORKER_RUNTIME" = "python"
      "WEBSITE_RUN_FROM_PACKAGE" = "1"
      "ATTENDANCE_QUEUE_NAME"    = azurerm_servicebus_queue.attendance.name
      "SERVICEBUS_CONNECTION"    = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.servicebus_connection_string.versionless_id})"
      "CONTOSO_DATABASE_URL"     = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.contoso_db_connection_string.versionless_id})"
      "LITWARE_DATABASE_URL"     = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.litware_db_connection_string.versionless_id})"
    },
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

resource "azurerm_redis_cache" "app" {
  name                = local.redis_name
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name
  capacity            = var.redis_capacity
  family              = var.redis_family
  sku_name            = var.redis_sku_name
  minimum_tls_version = "1.2"
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
