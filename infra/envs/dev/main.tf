provider "azurerm" {
  features {}
  resource_provider_registrations = "none"
}
module "app" {
  source         = "../../modules/webapp_container"
  location       = var.location
  rg_name        = var.rg_name
  env_name       = "dev"

  acr_name       = var.acr_name
  acr_rg_name    = var.acr_rg_name

  plan_name      = var.plan_name
  plan_sku       = var.plan_sku
  webapp_name    = var.webapp_name

  image_name     = var.image_name
  image_tag      = var.image_tag

  container_port = var.container_port
}

output "webapp_url" { value = module.app.webapp_url }
