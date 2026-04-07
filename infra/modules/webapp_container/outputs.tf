output "webapp_url" {
  value = "https://${azurerm_linux_web_app.app.default_hostname}"
}

output "staging_slot_url" {
  value = "https://${azurerm_linux_web_app_slot.staging.default_hostname}"
}

output "key_vault_name" {
  value = azurerm_key_vault.app.name
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.db.fqdn
}

output "postgres_database_name" {
  value = azurerm_postgresql_flexible_server_database.app.name
}

output "apim_gateway_url" {
  value = azurerm_api_management.app.gateway_url
}

output "redis_hostname" {
  value = azurerm_redis_cache.app.hostname
}

output "servicebus_namespace_name" {
  value = azurerm_servicebus_namespace.app.name
}

output "attendance_queue_name" {
  value = azurerm_servicebus_queue.attendance.name
}

output "iothub_name" {
  value = azurerm_iothub.app.name
}

output "function_app_name" {
  value = azurerm_linux_function_app.attendance.name
}

output "log_analytics_workspace_id" {
  value = azurerm_log_analytics_workspace.observability.id
}

output "log_analytics_workspace_name" {
  value = azurerm_log_analytics_workspace.observability.name
}

output "application_insights_name" {
  value = azurerm_application_insights.observability.name
}

output "application_insights_connection_string" {
  value     = azurerm_application_insights.observability.connection_string
  sensitive = true
}

output "azure_openai_name" {
  value = azurerm_cognitive_account.openai.name
}

output "azure_openai_endpoint" {
  value = azurerm_cognitive_account.openai.endpoint
}

output "azure_openai_deployment_name" {
  value = try(azurerm_cognitive_deployment.openai[0].name, "")
}

output "azure_openai_api_key_secret_name" {
  value = azurerm_key_vault_secret.azure_openai_api_key.name
}
