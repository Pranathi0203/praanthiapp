terraform {
  required_version = ">= 1.6"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.110"
    }
  }
}

provider "azurerm" {
  features {}
  skip_provider_registration = true
}

module "app" {
  source = "../../modules/webapp_container"

  location = var.location
  rg_name  = var.rg_name
  env_name = "qa"

  acr_name    = var.acr_name
  acr_rg_name = var.acr_rg_name

  plan_name   = var.plan_name
  plan_sku    = var.plan_sku
  webapp_name = var.webapp_name

  image_name = var.image_name
  image_tag  = var.image_tag

  container_port                             = var.container_port
  allowed_client_cidr                        = var.allowed_client_cidr
  contoso_device_connection_string           = var.contoso_device_connection_string
  litware_device_connection_string           = var.litware_device_connection_string
  iothub_eventhub_connection_string          = var.iothub_eventhub_connection_string
  application_insights_connection_string     = var.application_insights_connection_string
  azure_openai_sku_name                      = var.azure_openai_sku_name
  azure_openai_custom_subdomain              = var.azure_openai_custom_subdomain
  azure_openai_public_network_access_enabled = var.azure_openai_public_network_access_enabled
  azure_openai_deployment_name               = var.azure_openai_deployment_name
  azure_openai_model_name                    = var.azure_openai_model_name
  azure_openai_model_version                 = var.azure_openai_model_version
  azure_openai_deployment_sku_name           = var.azure_openai_deployment_sku_name
  azure_openai_deployment_capacity           = var.azure_openai_deployment_capacity
  dd_service                                 = var.dd_service
  dd_env                                     = var.dd_env
  dd_version                                 = var.dd_version
  dd_trace_enabled                           = var.dd_trace_enabled
  dd_logs_injection                          = var.dd_logs_injection
  dd_agent_host                              = var.dd_agent_host
  dd_trace_agent_url                         = var.dd_trace_agent_url
  github_dashboard_token                     = var.github_dashboard_token
  alert_email                                = var.alert_email
  pg_alert_cpu_threshold                     = var.pg_alert_cpu_threshold
  iothub_data_contributor_principal_id       = var.iothub_data_contributor_principal_id

  # Optional extra settings
  app_settings = {
    "ENV" = "qa"
  }
}

output "webapp_url" {
  value = module.app.webapp_url
}
output "staging_slot_url" {
  value = module.app.staging_slot_url
}
output "key_vault_name" {
  value = module.app.key_vault_name
}
output "postgres_fqdn" {
  value = module.app.postgres_fqdn
}
output "apim_gateway_url" {
  value = module.app.apim_gateway_url
}
output "redis_hostname" {
  value = module.app.redis_hostname
}
output "servicebus_namespace_name" {
  value = module.app.servicebus_namespace_name
}
output "attendance_queue_name" {
  value = module.app.attendance_queue_name
}
output "iothub_name" {
  value = module.app.iothub_name
}
output "function_app_name" {
  value = module.app.function_app_name
}
output "log_analytics_workspace_id" {
  value = module.app.log_analytics_workspace_id
}
output "log_analytics_workspace_name" {
  value = module.app.log_analytics_workspace_name
}
output "application_insights_name" {
  value = module.app.application_insights_name
}
output "application_insights_connection_string" {
  value     = module.app.application_insights_connection_string
  sensitive = true
}
output "azure_openai_name" { value = module.app.azure_openai_name }
output "azure_openai_endpoint" { value = module.app.azure_openai_endpoint }
output "azure_openai_deployment_name" { value = module.app.azure_openai_deployment_name }
output "azure_openai_api_key_secret_name" { value = module.app.azure_openai_api_key_secret_name }
