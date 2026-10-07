terraform {
  required_version = ">= 1.8.0"
  required_providers {
    xcsh = {
      source  = "f5-sales-demo/xcsh"
      version = "= 15.0.0"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.0"
    }
  }
  backend "local" {}
}

provider "xcsh" {}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

provider "azuread" {}

variable "subscription_id" {
  description = "The signed-in sales-demo Azure subscription ID."
  type        = string
  sensitive   = true
}

variable "location" {
  description = "Azure region for both dedicated demo VMs."
  type        = string
  default     = "eastus2"
}

variable "ssh_public_key_path" {
  description = "Operator public key installed on both VMs."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "namespace" {
  description = "Namespace for the owned deployment."
  type        = string
  default     = "demo-app"
}

variable "domainname" {
  description = "Domainname for the owned deployment."
  type        = string
  default     = "app.example.com"
}

variable "lb_name" {
  description = "Lb name for the owned deployment."
  type        = string
  default     = "demo-app"
}

variable "origin_pool_name" {
  description = "Origin pool name for the owned deployment."
  type        = string
  default     = "demo-app-origin"
}

variable "healthcheck_name" {
  description = "Healthcheck name for the owned deployment."
  type        = string
  default     = "demo-app-health"
}

variable "waf_name" {
  description = "Waf name for the owned deployment."
  type        = string
  default     = "demo-app-waf"
}

variable "deployer" {
  description = "Deployer for the owned deployment."
  type        = string
  default     = "demo-app"
}

variable "environment" {
  description = "Environment for the owned deployment."
  type        = string
  default     = "lab"
}

variable "owner" {
  description = "Owner for the owned deployment."
  type        = string
  default     = "demo-app"
}

variable "purpose" {
  description = "Purpose for the owned deployment."
  type        = string
  default     = "demo-app"
}

variable "timer_name" {
  description = "Timer name for the owned deployment."
  type        = string
  default     = "demo-app-timer"
}

locals {
  domain    = var.domainname
  namespace = var.namespace
  labels = {
    "f5-sales-demo/owner" = var.owner
  }
}

# Each module source resolves to an immutable commit.
module "origin" {
  source = "git::https://github.com/f5-sales-demo/origin-server.git//terraform?ref=58402bf63383d59df07cbf4dbb7e78ec0ab46b0f"

  subscription_id     = var.subscription_id
  deployer            = var.deployer
  environment         = var.environment
  location            = var.location
  ssh_public_key_path = var.ssh_public_key_path
  tags                = { purpose = var.purpose }
}

module "generator" {
  source = "git::https://github.com/f5-sales-demo/traffic-generator.git//terraform?ref=b212c0d1db14c654ccddc260a7bc2efa486cfae3"

  subscription_id     = var.subscription_id
  deployer            = var.deployer
  environment         = var.environment
  location            = var.location
  ssh_public_key_path = var.ssh_public_key_path
  target_fqdn         = local.domain
  tags                = { purpose = var.purpose }
}

resource "xcsh_namespace" "statistics" {
  name = local.namespace
}

resource "xcsh_healthcheck" "origin" {
  name      = var.healthcheck_name
  namespace = xcsh_namespace.statistics.name
  labels    = local.labels

  healthy_threshold   = 1
  unhealthy_threshold = 2
  timeout             = 3
  interval            = 10

  http_health_check {
    path                   = "/health"
    use_origin_server_name = {}
  }
}

resource "xcsh_origin_pool" "origin" {
  name      = var.origin_pool_name
  namespace = xcsh_namespace.statistics.name
  labels    = local.labels
  port      = 80

  origin_servers {
    public_ip {
      ip = module.origin.public_ip
    }
  }

  healthcheck {
    name      = xcsh_healthcheck.origin.name
    namespace = xcsh_healthcheck.origin.namespace
  }

  no_tls                = {}
  same_as_endpoint_port = {}
}

resource "xcsh_app_firewall" "statistics" {
  name      = var.waf_name
  namespace = xcsh_namespace.statistics.name
  labels    = local.labels

  default_detection_settings = {}
  blocking                   = {}
}

resource "xcsh_http_loadbalancer" "statistics" {
  name      = var.lb_name
  namespace = xcsh_namespace.statistics.name
  labels    = local.labels
  domains   = [local.domain]

  https_auto_cert {
    port          = 443
    http_redirect = false
    no_mtls       = {}
  }

  default_route_pools {
    pool {
      name      = xcsh_origin_pool.origin.name
      namespace = xcsh_origin_pool.origin.namespace
    }
    weight   = 1
    priority = 1
  }

  advertise_on_public_default_vip = {}
  enable_api_discovery {}

  app_firewall {
    name      = xcsh_app_firewall.statistics.name
    namespace = xcsh_app_firewall.statistics.namespace
  }
}

# The extension installs a persistent, bounded timer after the LB exists.
resource "azurerm_virtual_machine_extension" "generator_timer" {
  name                       = var.timer_name
  virtual_machine_id         = module.generator.vm_id
  publisher                  = "Microsoft.Azure.Extensions"
  type                       = "CustomScript"
  type_handler_version       = "2.1"
  auto_upgrade_minor_version = true

  protected_settings = jsonencode({
    script = base64encode(templatefile("${path.module}/install-generator-timer.sh.tftpl", {
      fqdn = local.domain
    }))
  })
  depends_on = [xcsh_http_loadbalancer.statistics]
}

output "demo_hostname" {
  description = "HTTPS demo hostname."
  value       = local.domain
}

output "origin_vm_name" {
  description = "Dedicated origin VM name."
  value       = module.origin.vm_name
}

output "generator_vm_name" {
  description = "Dedicated generator VM name."
  value       = module.generator.vm_name
}
