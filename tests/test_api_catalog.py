"""Exercise coverage failures and catalog boundaries using reviewed source evidence."""

import copy
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "api_catalog", ROOT / "scripts/api_catalog.py"
)
assert SPEC is not None
assert SPEC.loader is not None
CATALOG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CATALOG)


class CatalogTests(unittest.TestCase):
    """Protect reporting queries, exclusions, source coverage, and rendering."""

    inventory: ClassVar[dict[str, Any]]
    entries: ClassVar[dict[tuple[str, str], dict[str, Any]]]

    @classmethod
    def setUpClass(cls):
        cls.inventory = CATALOG.load_inventory()
        cls.entries = {
            (entry["method"], entry["path"]): entry
            for entry in cls.inventory["endpoints"]
        }

    def test_coverage_and_generated_page(self):
        CATALOG.validate(self.inventory)
        CATALOG.check_pages(self.inventory, ROOT / "docs/en/api-catalog")

    def test_split_catalog_coverage_and_overview(self):
        directory = ROOT / "docs/en/api-catalog"
        CATALOG.check_pages(self.inventory, directory)
        assert len(self.inventory["endpoints"]) == 443
        assert len(list(directory.glob("*.mdx"))) == 16
        assert "| Method and exact path |" not in (directory / "index.mdx").read_text()

    def test_missing_stale_duplicate_and_extra_pages_fail(self):
        directory = ROOT / "docs/en/api-catalog"
        for mutation in ["missing", "stale", "duplicate", "extra", "marker"]:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                pages = Path(tmp) / "catalog"
                shutil.copytree(directory, pages)
                page = pages / "application-traffic.mdx"
                if mutation == "missing":
                    page.unlink()
                elif mutation == "stale":
                    page.write_text(
                        page.read_text().replace(
                            "Tenant service graph", "Changed service graph"
                        )
                    )
                elif mutation == "duplicate":
                    row = next(
                        line
                        for line in page.read_text().splitlines()
                        if line.startswith("| `POST")
                    )
                    page.write_text(page.read_text() + "\n" + row + "\n")
                elif mutation == "extra":
                    (pages / "unmapped.mdx").write_text("Unmapped page")
                else:
                    page.write_text(page.read_text().replace(CATALOG.START, ""))
                try:
                    CATALOG.check_pages(self.inventory, pages)
                except ValueError:
                    continue
                message = f"Catalog accepted {mutation}"
                raise AssertionError(message)

    def test_unmapped_primary_group_fails(self):
        with patch.dict(CATALOG.GROUP_SLUGS):
            del CATALOG.GROUP_SLUGS["bot"]
            try:
                CATALOG.validate(self.inventory)
            except ValueError:
                return
            message = "Catalog accepted an unmapped primary group"
            raise AssertionError(message)

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
