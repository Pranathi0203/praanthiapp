variable "location" {
  type = string
}

variable "rg_name" {
  type = string
}

variable "acr_name" {
  type = string
}

variable "acr_rg_name" {
  type = string
}

variable "plan_name" {
  type = string
}

variable "plan_sku" {
  type = string
}

variable "webapp_name" {
  type = string
}

variable "image_name" {
  type = string
}

variable "image_tag" {
  type = string
}

variable "container_port" {
  type = number
}

variable "allowed_client_cidr" {
  type    = string
  default = "174.165.209.210/32"
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
  sensitive = true
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

variable "alert_email" {
  type    = string
  default = "pranymunnangi@gmail.com"
}

variable "iothub_data_contributor_principal_id" {
  type    = string
  default = ""
}
