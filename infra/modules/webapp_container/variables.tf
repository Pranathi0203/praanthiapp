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

variable "function_app_name" {
  type    = string
  default = ""
}

variable "servicebus_sku" {
  type    = string
  default = "Standard"
}

variable "redis_capacity" {
  type    = number
  default = 0
}

variable "redis_family" {
  type    = string
  default = "C"
}

variable "redis_sku_name" {
  type    = string
  default = "Basic"
}

variable "iothub_sku_name" {
  type    = string
  default = "S1"
}

variable "iothub_capacity" {
  type    = number
  default = 1
}

variable "function_app_service_plan_sku" {
  type    = string
  default = "Y1"
}

variable "function_python_version" {
  type    = string
  default = "3.11"
}

variable "contoso_device_connection_string" {
  type      = string
  default   = ""
  sensitive = true
}

variable "litware_device_connection_string" {
  type      = string
  default   = ""
  sensitive = true
}

variable "iothub_eventhub_connection_string" {
  type      = string
  default   = ""
  sensitive = true
}

variable "application_insights_connection_string" {
  type      = string
  default   = ""
  sensitive = true
}

variable "github_dashboard_token" {
  type      = string
  default   = ""
  sensitive = true
}

variable "log_analytics_retention_days" {
  type    = number
  default = 30
}
