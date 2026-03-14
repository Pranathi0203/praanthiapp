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
  type = string
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
