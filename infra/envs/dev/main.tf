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
}

output "webapp_url" { value = module.app.webapp_url }
output "staging_slot_url" { value = module.app.staging_slot_url }
output "key_vault_name" { value = module.app.key_vault_name }
output "postgres_fqdn" { value = module.app.postgres_fqdn }
output "apim_gateway_url" { value = module.app.apim_gateway_url }
output "application_insights_name" { value = module.app.application_insights_name }
