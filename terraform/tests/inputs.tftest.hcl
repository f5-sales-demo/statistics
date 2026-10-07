mock_provider "xcsh" {}
mock_provider "azurerm" {}
mock_provider "azuread" {}

run "alternate_identity" {
  command = plan
  variables {
    subscription_id     = "<XCSH_AZURE_SUBSCRIPTION_ID>"
    namespace           = "example-app"
    domainname          = "alternate.example.com"
    lb_name             = "alternate-lb"
    origin_pool_name    = "alternate-origin"
    healthcheck_name    = "alternate-health"
    waf_name            = "alternate-waf"
    deployer            = "alternate"
    environment         = "test"
    owner               = "alternate-owner"
    purpose             = "alternate-purpose"
    timer_name          = "alternate-timer"
    ssh_public_key_path = "tests/ssh-key.pub"
  }
  override_module {
    target = module.origin
    outputs = {
      public_ip = "198.51.100.10"
      vm_name   = "vm-origin-alternate"
    }
  }
  override_module {
    target = module.generator
    outputs = {
      vm_id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/example/providers/Microsoft.Compute/virtualMachines/example"
      vm_name = "vm-generator-alternate"
    }
  }
  assert {
    condition     = xcsh_namespace.statistics.name == "example-app" && xcsh_http_loadbalancer.statistics.name == "alternate-lb" && xcsh_http_loadbalancer.statistics.domains == tolist(["alternate.example.com"])
    error_message = "Namespace, load balancer and hostname must be independent inputs."
  }
  assert {
    condition     = xcsh_origin_pool.origin.name == "alternate-origin" && xcsh_healthcheck.origin.name == "alternate-health" && xcsh_app_firewall.statistics.name == "alternate-waf"
    error_message = "Named XC objects must follow alternate inputs."
  }
  assert {
    condition     = xcsh_http_loadbalancer.statistics.labels["f5-sales-demo/owner"] == "alternate-owner" && azurerm_virtual_machine_extension.generator_timer.name == "alternate-timer"
    error_message = "Owner and timer identities must follow alternate inputs."
  }
}
