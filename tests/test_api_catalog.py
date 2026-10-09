"""Exercise coverage failures and catalog boundaries using reviewed source evidence."""

import copy
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "api_catalog", ROOT / "scripts/api_catalog.py"
)
CATALOG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CATALOG)


class CatalogTests(unittest.TestCase):
    """Protect reporting queries, exclusions, source coverage, and rendering."""

    @classmethod
    def setUpClass(cls):
        cls.inventory = CATALOG.load_inventory()
        cls.entries = {
            (entry["method"], entry["path"]): entry
            for entry in cls.inventory["endpoints"]
        }

    def test_coverage_and_generated_page(self):
        CATALOG.validate(self.inventory)
        CATALOG.check_page(self.inventory, ROOT / "docs/en/api-catalog.mdx")

    def test_read_only_post_and_neighboring_mutations(self):
        prefix = "/api/shape/csd/namespaces/{namespace}"
        assert self.entries[("POST", prefix + "/scripts")]["kind"] == "query"
        assert self.entries[("GET", prefix + "/reports-history")]["kind"] == "report"
        excluded = {
            (item["method"], item["path"]): item for item in self.inventory["excluded"]
        }
        for path in ["/reports", "/scripts/{id}/approval-status", "/testjs"]:
            assert excluded[("POST", prefix + path)]["reason"]
        assert (
            "POST",
            "/api/shape/bot/namespaces/{namespace}/reporting/atb",
        ) in excluded

    def test_repeated_domains_and_versions(self):
        base = "/api/shape/bot/namespaces/{namespace}"
        for version in ["", "/v2", "/v3"]:
            entry = self.entries[
                ("POST", base + version + "/reporting/traffic/overview")
            ]
            assert len(entry["sources"]) > 1
        prefix = "/api/shape/csd/namespaces/{namespace}/scripts"
        assert ("GET", prefix) in self.entries
        assert ("POST", prefix) in self.entries

    def test_system_restrictions_and_source_only_cloud_queries(self):
        site = self.entries[
            ("POST", "/api/data/namespaces/{namespace}/site/{site}/status/metrics")
        ]
        assert "system" in site["scope"]
        for path in ["metrics", "segment_metrics", "{name}/metrics"]:
            entry = self.entries[
                ("POST", "/api/data/namespaces/system/cloud_connects/" + path)
            ]
            assert entry["source_only"]
            assert entry["references"]["operation"].startswith("https://github.com/")

    def test_probability_density_is_not_a_download(self):
        entry = self.entries[
            (
                "GET",
                "/api/ml/data/namespaces/{namespace}/virtual_hosts/{name}/api_endpoint/pdf",
            )
        ]
        assert entry["kind"] == "query"
        assert "Probability density" in entry["information"]

    def test_missing_duplicate_unknown_group_and_unexplained_exclusion_fail(self):
        for mutation in ["missing", "duplicate", "group", "reason", "evidence"]:
            inventory = copy.deepcopy(self.inventory)
            if mutation == "missing":
                inventory["endpoints"].pop()
            elif mutation == "duplicate":
                inventory["endpoints"].append(inventory["endpoints"][0])
            elif mutation == "group":
                inventory["endpoints"][0]["group"] = "unknown"
            elif mutation == "reason":
                inventory["excluded"][0]["reason"] = ""
            else:
                inventory["endpoints"][0]["sources"] = []
            with self.subTest(mutation=mutation):
                try:
                    CATALOG.validate(inventory)
                except ValueError:
                    continue
                message = f"Coverage accepted invalid {mutation}"
                raise AssertionError(message)


if __name__ == "__main__":
    unittest.main()
