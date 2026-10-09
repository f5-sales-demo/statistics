#!/usr/bin/env python3
"""Check configurable resource identities without reading private deployment data."""

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TerraformInputs(unittest.TestCase):
    def test_names_are_inputs(self):
        source = (ROOT / "terraform/main.tf").read_text()
        for name in [
            "namespace",
            "domainname",
            "lb_name",
            "origin_pool_name",
            "healthcheck_name",
            "waf_name",
            "deployer",
            "environment",
            "owner",
            "purpose",
            "timer_name",
        ]:
            assert re.search(r'variable "' + name + r'"\s*{', source)
        assert 'split(".", local.domain)' not in source
        assert "statistics.f5-sales-demo.com" not in source

    def test_addresses_and_pins_unchanged(self):
        source = (ROOT / "terraform/main.tf").read_text()
        for address in [
            'xcsh_namespace" "statistics',
            'xcsh_http_loadbalancer" "statistics',
            'xcsh_app_firewall" "statistics',
            'xcsh_origin_pool" "origin',
            'xcsh_healthcheck" "origin',
        ]:
            assert 'resource "' + address + '"' in source
        assert 'version = "= 15.0.0"' in source
        assert "58402bf63383d59df07cbf4dbb7e78ec0ab46b0f" in source
        assert "b212c0d1db14c654ccddc260a7bc2efa486cfae3" in source

    def test_sanitized_tfvars(self):
        source = (ROOT / "terraform/terraform.tfvars.example").read_text()
        assert "<XCSH_AZURE_SUBSCRIPTION_ID>" in source
        assert "api_token" not in source.lower()
        assert "f5-sales-demo.com" not in source

    def test_recorded_response_measurements_unchanged(self):
        hashes = json.loads((ROOT / "tests/recorded-response-hashes.json").read_text())
        for file, expected in hashes.items():
            text = (ROOT / file).read_text()
            bodies = re.findall(
                r"^ *```(json|text)\n(.*?)^ *```", text, re.MULTILINE | re.DOTALL
            )
            actual = [
                hashlib.sha256(
                    re.sub(
                        r'<XCSH_[A-Z0-9_]+>',
                        "IDENTIFIER",
                        body,
                    ).encode()
                ).hexdigest()
                for _, body in bodies
            ]
            assert actual == expected, file

    def test_historical_blocks_bind_only_configurable_identifiers(self):
        for path in (ROOT / "docs/en").rglob("*.mdx"):
            text = path.read_text()
            for match in re.finditer(r"Captured [^\n]+", text):
                remainder = text[match.end() :]
                fence = remainder.find("```")
                if fence >= 0:
                    body_end = remainder.find("```", fence + 3)
                    body = remainder[fence:body_end]
                    identifiers = set(re.findall(r"<(XCSH_[A-Z0-9_]+)>", body))
                    attributes = remainder[:fence]
                    if identifiers:
                        assert 'data-personalize="off"' not in attributes, path.name
                        assert "data-xcsh-context=" in attributes, path.name
                        declared = re.search(r'data-xcsh-fields="([^"]+)"', attributes)
                        assert declared is not None, path.name
                        assert identifiers <= set(declared[1].split()), path.name
                    else:
                        assert 'data-personalize="off"' in attributes, path.name


if __name__ == "__main__":
    unittest.main()
