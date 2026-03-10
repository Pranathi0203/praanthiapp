variable "location" { type = string }
variable "rg_name" { type = string }
variable "env_name" { type = string }

variable "acr_name" { type = string }
variable "acr_rg_name" { type = string }

variable "plan_name" { type = string }
variable "plan_sku" { type = string }
variable "webapp_name" { type = string }
variable "staging_slot_name" {
  type    = string
  default = "staging"
}

variable "image_name" { type = string }
variable "image_tag" { type = string }

variable "container_port" {
  type    = number
  default = 8000
}

variable "allowed_client_cidr" {
  type = string
}

variable "vnet_address_space" {
  type    = list(string)
  default = ["10.20.0.0/16"]
}

variable "app_subnet_cidr" {
  type    = string
  default = "10.20.1.0/24"
}

variable "db_subnet_cidr" {
  type    = string
  default = "10.20.2.0/24"
}

variable "app_settings" {
  type    = map(string)
  default = {}
}

variable "postgres_version" {
  type    = string
  default = "14"
}

variable "postgres_sku_name" {
  type    = string
  default = "B_Standard_B1ms"
}

variable "postgres_storage_mb" {
  type    = number
  default = 32768
}

variable "postgres_database_name" {
  type    = string
  default = "appdb"
}

variable "contoso_database_name" {
  type    = string
  default = "contoso_db"
}

variable "litware_database_name" {
  type    = string
  default = "litware_db"
}

variable "apim_sku_name" {
  type    = string
  default = "Developer_1"
}

variable "apim_publisher_name" {
  type    = string
  default = "Pranathi App"
}

variable "apim_publisher_email" {
  type    = string
  default = "admin@pranathiapp.local"
}
