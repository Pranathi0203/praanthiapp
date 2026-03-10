terraform {
  required_version = ">= 1.6"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 3.110" }
    random  = { source = "hashicorp/random", version = "~> 3.6" }
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
    "APIM_LOGIN_PATH"                       = "/auth/login"
    "APIM_SIGNUP_PATH"                      = "/auth/signup"
    "APPLICATIONINSIGHTS_CONNECTION_STRING" = azurerm_application_insights.app.connection_string
    "APPINSIGHTS_INSTRUMENTATIONKEY"        = azurerm_application_insights.app.instrumentation_key
  }
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

resource "azurerm_application_insights" "app" {
  name                = local.app_insights_name
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name
  application_type    = "web"
  workspace_id        = "/subscriptions/df4637b0-01eb-4650-9feb-73300318eb52/resourceGroups/ai_myappdev0203-dev-appi-6760_05bf5ebf-8990-4306-ae01-9826506ffbb6_managed/providers/Microsoft.OperationalInsights/workspaces/managed-myappdev0203-dev-appi-6760-ws"
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

  app_settings = merge(local.base_app_settings, var.app_settings)
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
  value        = "postgresql://${azurerm_postgresql_flexible_server.db.administrator_login}:${random_password.db_admin_password.result}@${azurerm_postgresql_flexible_server.db.fqdn}:5432/${azurerm_postgresql_flexible_server_database.app.name}?sslmode=require"
  key_vault_id = azurerm_key_vault.app.id
  depends_on   = [azurerm_key_vault_access_policy.terraform_runner]
}

resource "azurerm_key_vault_secret" "contoso_db_connection_string" {
  name         = "contoso-db-connection-string"
  value        = "postgresql://${azurerm_postgresql_flexible_server.db.administrator_login}:${random_password.db_admin_password.result}@${azurerm_postgresql_flexible_server.db.fqdn}:5432/${azurerm_postgresql_flexible_server_database.contoso.name}?sslmode=require"
  key_vault_id = azurerm_key_vault.app.id
  depends_on   = [azurerm_key_vault_access_policy.terraform_runner]
}

resource "azurerm_key_vault_secret" "litware_db_connection_string" {
  name         = "litware-db-connection-string"
  value        = "postgresql://${azurerm_postgresql_flexible_server.db.administrator_login}:${random_password.db_admin_password.result}@${azurerm_postgresql_flexible_server.db.fqdn}:5432/${azurerm_postgresql_flexible_server_database.litware.name}?sslmode=require"
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
