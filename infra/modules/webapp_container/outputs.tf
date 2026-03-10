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

output "application_insights_name" {
  value = azurerm_application_insights.app.name
}

output "application_insights_connection_string" {
  value     = azurerm_application_insights.app.connection_string
  sensitive = true
}
