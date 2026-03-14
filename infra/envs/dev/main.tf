provider "azurerm" {
  features {}
  skip_provider_registration = true
}
module "app" {
  source   = "../../modules/webapp_container"
  location = var.location
  rg_name  = var.rg_name
  env_name = "dev"

  acr_name    = var.acr_name
  acr_rg_name = var.acr_rg_name

  plan_name   = var.plan_name
  plan_sku    = var.plan_sku
  webapp_name = var.webapp_name

  image_name = var.image_name
  image_tag  = var.image_tag

  container_port      = var.container_port
  allowed_client_cidr = var.allowed_client_cidr
  contoso_device_connection_string = var.contoso_device_connection_string
  litware_device_connection_string = var.litware_device_connection_string
  iothub_eventhub_connection_string = var.iothub_eventhub_connection_string
}

output "webapp_url" { value = module.app.webapp_url }
output "staging_slot_url" { value = module.app.staging_slot_url }
output "key_vault_name" { value = module.app.key_vault_name }
output "postgres_fqdn" { value = module.app.postgres_fqdn }
output "apim_gateway_url" { value = module.app.apim_gateway_url }
output "redis_hostname" { value = module.app.redis_hostname }
output "servicebus_namespace_name" { value = module.app.servicebus_namespace_name }
output "attendance_queue_name" { value = module.app.attendance_queue_name }
output "iothub_name" { value = module.app.iothub_name }
output "function_app_name" { value = module.app.function_app_name }
