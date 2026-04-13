variable "location" { type = string }
variable "rg_name" { type = string }

variable "acr_name" { type = string }
variable "acr_rg_name" { type = string }

variable "plan_name" { type = string }
variable "plan_sku" { type = string }
variable "webapp_name" { type = string }

variable "image_name" { type = string }
variable "image_tag" { type = string }

variable "container_port" { type = number }
variable "allowed_client_cidr" {
  type    = string
  default = "50.54.243.250/32"
}
variable "contoso_device_connection_string" {
  type      = string
  sensitive = true
}
variable "litware_device_connection_string" {
  type      = string
  sensitive = true
}
variable "iothub_eventhub_connection_string" {
  type      = string
  sensitive = true
}
variable "application_insights_connection_string" {
  type      = string
  default   = ""
  sensitive = true
}
variable "azure_openai_sku_name" {
  type    = string
  default = "S0"
}
variable "azure_openai_custom_subdomain" {
  type    = string
  default = ""
}
variable "azure_openai_public_network_access_enabled" {
  type    = bool
  default = true
}
variable "azure_openai_location" {
  type    = string
  default = "eastus2"
}
variable "azure_openai_deployment_name" {
  type    = string
  default = "gpt-4o-mini"
}
variable "azure_openai_model_name" {
  type    = string
  default = "gpt-4o"
}
variable "azure_openai_model_version" {
  type    = string
  default = "2024-11-20"
}
variable "azure_openai_deployment_sku_name" {
  type    = string
  default = "Standard"
}
variable "azure_openai_deployment_capacity" {
  type    = number
  default = 1
}
variable "dd_service" {
  type    = string
  default = ""
}
variable "dd_env" {
  type    = string
  default = ""
}
variable "dd_version" {
  type    = string
  default = ""
}
variable "dd_trace_enabled" {
  type    = bool
  default = false
}
variable "dd_logs_injection" {
  type    = bool
  default = true
}
variable "dd_agent_host" {
  type    = string
  default = ""
}
variable "dd_trace_agent_url" {
  type    = string
  default = ""
}
variable "github_dashboard_token" {
  type      = string
  sensitive = true
  default   = ""
}

variable "github_repository" {
  type    = string
  default = "Pranathi0203/praanthiapp"
}

variable "admin_email" {
  type    = string
  default = "pranymunnangi@gmail.com"
}

variable "admin_password" {
  type      = string
  sensitive = true
}

variable "alert_email" {
  type    = string
  default = "pranymunnangi@gmail.com"
}

variable "pg_alert_cpu_threshold" {
  type    = number
  default = 5
}

variable "iothub_data_contributor_principal_id" {
  type    = string
  default = ""
}
