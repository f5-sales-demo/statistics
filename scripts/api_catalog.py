"""Validate reviewed specification coverage and render deterministic endpoint tables."""

import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, NoReturn

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "engineering/api-reference"
INVENTORY = EVIDENCE / "inventory.json"
PAGE = ROOT / "docs/en/api-catalog.mdx"
START = "{/*BEGIN GENERATED API CATALOG*/}"
END = "{/*END GENERATED API CATALOG*/}"
GROUPS = [
    ("traffic", "Application traffic, origins, and service health"),
    ("api", "API activity, discovery, and security analytics"),
    ("waf", "WAF and application security"),
    ("bot", "Bot defense"),
    ("csd", "Client-side defense"),
    ("intelligence", "Device and data intelligence"),
    ("ddos", "DDoS protection"),
    ("dns", "DNS"),
    ("cdn", "CDN"),
    ("network", "Sites, connectivity, cloud networking, and policy counters"),
    ("kubernetes", "Kubernetes, workloads, and storage"),
    ("logs", "Logs, audit activity, alerts, and platform events"),
    ("synthetic", "Synthetic monitoring"),
    ("billing", "Billing, consumption, and quota usage"),
]


def fail(message: str) -> NoReturn:
    """Reject invalid coverage with a reviewable explanation."""
    raise ValueError(message)


def load_inventory() -> dict[str, Any]:
    """Load reviewed inclusions and exclusions from their versioned files."""
    inventory = json.loads(INVENTORY.read_text())
    inventory["excluded"] = json.loads((EVIDENCE / inventory["exclusions"]).read_text())
    return inventory


def load_projection() -> list[dict[str, Any]]:
    """Read the sanitized extraction receipt without requiring network in CI."""
    with gzip.open(EVIDENCE / "source-operations.json.gz", "rt") as source:
        return json.load(source)


def validate(
    inventory: dict[str, Any], projection: list[dict[str, Any]] | None = None
) -> None:
    """Reject missing dispositions, stale source evidence, and duplicate routes."""
    projection = load_projection() if projection is None else projection
    expected = {(item["method"], item["path"]): item for item in projection}
    groups = dict(GROUPS)
    observed = set()
    for section in ["endpoints", "excluded"]:
        for entry in inventory[section]:
            key = (entry["method"], entry["path"])
            if key in observed or key not in expected:
                fail(f"Duplicate or unknown operation: {key}")
            observed.add(key)
            if not entry.get("reason", "").strip():
                fail(f"Missing disposition reason: {key}")
            if section == "excluded":
                continue
            if entry["group"] not in groups or entry["kind"] not in {
                "query",
                "discovery",
                "report",
            }:
                fail(f"Invalid primary group or kind: {key}")
            if entry["method"] not in {"GET", "POST"}:
                fail(f"Mutation included: {key}")
            if entry.get("sources") != [
                {name: source[name] for name in ["file", "operation", "sha256"]}
                for source in expected[key]["sources"]
            ]:
                fail(f"Missing or stale source/schema evidence: {key}")
            if (
                any(source["system_only"] for source in expected[key]["sources"])
                and "system" not in entry["scope"]
            ):
                fail(f"Missing system restriction: {key}")
            for field in ["information", "scope", "filters"]:
                if not entry.get(field):
                    fail(f"Missing {field}: {key}")
            if set(entry["references"]) != {"operation", "request", "response"}:
                fail(f"Missing specification references: {key}")
    if observed != set(expected):
        fail(f"Unexplained omissions: {sorted(set(expected) - observed)}")
    if set(groups) != {entry["group"] for entry in inventory["endpoints"]}:
        fail("Missing resource group")


def cell(value: str) -> str:
    """Escape table syntax without changing inline code identifiers."""
    return value.replace("|", "&#124;").replace("\n", " ")


def render(inventory: dict[str, Any]) -> str:
    """Produce every exact method/path once, grouped by query purpose."""
    lines = [START, ""]
    for group, title in GROUPS:
        lines += [f"## {title}", ""]
        for kind, label in [
            ("query", "Statistics and analytics queries"),
            ("discovery", "Related discovery and status"),
            ("report", "Existing reports and artifacts"),
        ]:
            entries = sorted(
                (
                    entry
                    for entry in inventory["endpoints"]
                    if entry["group"] == group and entry["kind"] == kind
                ),
                key=lambda entry: (entry["path"], entry["method"]),
            )
            if not entries:
                continue
            lines += [
                f"### {title}: {label[0].lower() + label[1:]}",
                "",
                "| Method and exact path | Information returned | Scope and principal filters | Specification reference |",
                "| --- | --- | --- | --- |",
            ]
            for entry in entries:
                refs = entry["references"]
                reference = (
                    f"[Operation]({refs['operation']}) · "
                    f"[Request schema]({refs['request']}) · "
                    f"[Response schema]({refs['response']})"
                )
                if entry["source_only"]:
                    reference += "; corrected source"
                information = entry["information"]
                if entry["deprecated"]:
                    information += "; source marks deprecated"
                values = [
                    f"`{entry['method']} {entry['path']}`",
                    information,
                    entry["scope"] + "; " + entry["filters"],
                    reference,
                ]
                lines.append("| " + " | ".join(cell(value) for value in values) + " |")
            lines += [""]
    lines.append(END)
    return "\n".join(lines)


def check_page(inventory: dict[str, Any], page: Path) -> None:
    """Reject handwritten drift in generated tables."""
    text = page.read_text()
    actual = text[text.index(START) : text.index(END) + len(END)]
    if actual != render(inventory):
        fail("Generated catalog is stale; run scripts/api_catalog.py --write")


def resolve_reference(source: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    """Resolve a local OpenAPI reference for schema evidence."""
    while "$ref" in value:
        reference = value["$ref"]
        value = source
        for key in reference.removeprefix("#/").split("/"):
            value = value[key.replace("~1", "/").replace("~0", "~")]
    return value


def operation_digest(source: dict[str, Any], operation: dict[str, Any]) -> str:
    """Fingerprint the operation and all schemas reachable from its references."""
    schemas = {}
    pending = [operation]
    while pending:
        value = pending.pop()
        if isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            if "$ref" in value and value["$ref"] not in schemas:
                reference = value["$ref"]
                schemas[reference] = resolve_reference(source, value)
                pending.append(schemas[reference])
            pending.extend(value.values())
    encoded = json.dumps(
        {"operation": operation, "schemas": schemas},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def verify_sources(inventory: dict[str, Any], root: Path) -> None:
    """Replay extraction from original bytes and verify every operation/schema closure."""
    receipt = json.loads((EVIDENCE / "sources.json").read_text())
    observed = {}
    for source_file in receipt["files"]:
        filename, digest = source_file["path"], source_file["sha256"]
        path = root / filename
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            fail(f"Source file changed: {filename}")
        source = json.loads(path.read_text())
        paths = source.get("paths", {})
        if not isinstance(paths, dict):
            continue
        for route, item in paths.items():
            for method, operation in item.items():
                if method in {
                    "get",
                    "post",
                    "put",
                    "delete",
                    "patch",
                    "head",
                    "options",
                }:
                    observed[(filename, method.upper(), route)] = {
                        "operation": operation.get("operationId", ""),
                        "sha256": operation_digest(source, operation),
                    }
    expected = {
        (source["file"], item["method"], item["path"]): {
            "operation": source["operation"],
            "sha256": source["sha256"],
        }
        for item in load_projection()
        for source in item["sources"]
    }
    if observed != expected:
        fail("Source operation or schema evidence differs from extraction receipt")
    validate(inventory)


def main() -> None:
    """Generate the page or validate it with optional source re-verification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--sources", type=Path)
    args = parser.parse_args()
    inventory = load_inventory()
    validate(inventory)
    if args.sources:
        verify_sources(inventory, args.sources)
    if args.write:
        text = PAGE.read_text()
        PAGE.write_text(
            text[: text.index(START)]
            + render(inventory)
            + text[text.index(END) + len(END) :]
        )
    check_page(inventory, PAGE)
    print(
        json.dumps(
            {
                "status": "pass",
                "included": len(inventory["endpoints"]),
                "excluded": len(inventory["excluded"]),
                "groups": dict(
                    Counter(entry["group"] for entry in inventory["endpoints"])
                ),
                "source_only": sum(
                    entry["source_only"] for entry in inventory["endpoints"]
                ),
                "page_sha256": hashlib.sha256(PAGE.read_bytes()).hexdigest(),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
