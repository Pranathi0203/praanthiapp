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

  location       = var.location
  rg_name        = var.rg_name
  env_name       = "qa"

  acr_name       = var.acr_name
  acr_rg_name    = var.acr_rg_name

  plan_name      = var.plan_name
  plan_sku       = var.plan_sku
  webapp_name    = var.webapp_name

  image_name     = var.image_name
  image_tag      = var.image_tag

  container_port = var.container_port

  # Optional extra settings
  app_settings = {
    "ENV" = "qa"
  }
}

output "webapp_url" {
  value = module.app.webapp_url
}
