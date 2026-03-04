variable "location"    { type = string }
variable "rg_name"     { type = string }
variable "env_name"    { type = string }

variable "acr_name"    { type = string }
variable "acr_rg_name" { type = string }

variable "plan_name"   { type = string }
variable "plan_sku"    { type = string }
variable "webapp_name" { type = string }

variable "image_name"  { type = string }
variable "image_tag"   { type = string }

variable "container_port" {
  type    = number
  default = 8000
}

variable "app_settings" {
  type    = map(string)
  default = {}
}