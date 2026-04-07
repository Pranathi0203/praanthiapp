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

variable "azure_openai_deployment_name" {
  type    = string
  default = "gpt-4o-mini"
}

variable "azure_openai_model_name" {
  type    = string
  default = "gpt-4o-mini"
}

variable "azure_openai_model_version" {
  type    = string
  default = "2024-07-18"
}

variable "azure_openai_deployment_sku_name" {
  type    = string
  default = "Standard"
}

variable "azure_openai_deployment_capacity" {
  type    = number
  default = 1
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
  type        = string
  default     = ""
  sensitive   = true
  description = "Deprecated override. The module now provisions its own workspace-based Application Insights resource and uses that connection string."
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
  default   = ""
  sensitive = true
}

variable "log_analytics_retention_days" {
  type    = number
  default = 30
}

variable "pg_log_min_duration_ms" {
  type        = number
  default     = 1000
  description = "Log queries slower than this threshold in milliseconds. Set to -1 to disable, 0 to log everything."
}

variable "pg_deadlock_timeout_ms" {
  type        = number
  default     = 1000
  description = "Time in milliseconds a transaction waits for a lock before deadlock detection runs and the wait is logged."
}

variable "pg_log_temp_files_kb" {
  type        = number
  default     = 0
  description = "Log temp files larger than this size in kilobytes. 0 logs all temp files (any spill to disk)."
}

variable "pg_qs_retention_days" {
  type        = number
  default     = 7
  description = "Number of days to retain Azure Query Store performance history."
}

variable "iothub_data_contributor_principal_id" {
  type        = string
  default     = ""
  description = "Object ID of the user or group to assign IoT Hub Data Contributor role. Grants portal device listing and az iot CLI access."
}

variable "alert_email" {
  type        = string
  default     = ""
  description = "Email address to receive database alerts. Leave empty to disable all alerting."
}

variable "alert_webhook_url" {
  type        = string
  default     = ""
  description = "Optional webhook URL for alerts (Teams, Slack, PagerDuty). Leave empty to disable."
}

variable "pg_alert_max_connections" {
  type        = number
  default     = 40
  description = "Alert when active connections exceed this. B1ms hard limit is ~50, so default 40 gives early warning."
}

variable "pg_alert_cpu_threshold" {
  type        = number
  default     = 85
  description = "Alert when PostgreSQL CPU percent exceeds this threshold for 15 minutes."
}

variable "pg_alert_storage_threshold" {
  type        = number
  default     = 80
  description = "Alert when PostgreSQL storage percent exceeds this threshold."
}

variable "pg_alert_iops_threshold" {
  type        = number
  default     = 90
  description = "Alert when PostgreSQL IOPS percent exceeds this threshold."
}
