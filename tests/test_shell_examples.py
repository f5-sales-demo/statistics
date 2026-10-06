#!/usr/bin/env python3
"""Exercise the downloadable Bash examples at the cURL boundary."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOCK = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
url = args[-1]
scenario = os.environ["SCENARIO"]
request = json.loads(args[args.index("--data-binary") + 1]) if "--data-binary" in args else None
with open(os.environ["CALLS"], "a") as file:
    file.write(json.dumps({"url": url, "request": request, "args": args}) + "\n")
ns, vh = "example", "telemetry-example"
def point(value, timestamp=100):
    return {"timestamp": timestamp, "value": value, "trend_value": None}
def metric(kind):
    values = ["NaN", "Infinity", "-Infinity", "bad", None, "1e9999"] if scenario == "nonfinite" else ["0", "2.5", "NaN", "bad", "Infinity"]
    return {"type": kind, "unit": "UNIT_REQUESTS_PER_SECOND", "value": {"raw": [point(v, 100+i) for i, v in enumerate(values)]}}
body = {}
if "http_loadbalancers" in url:
    body = {"metadata": {"name": ns, "namespace": ns, "labels": {"ves.io/app_type": "example-app"}}, "spec": {"enable_api_discovery": {}, "default_route_pools": [{"pool": {"name": "example-pool", "namespace": ns}}]}}
elif "origin_pools" in url:
    body = {"spec": {"origin_servers": [{"public_ip": {"ip": "203.0.113.10"}}]}}
    if scenario == "ambiguous_pool":
        body["spec"]["origin_servers"].append({"public_ip": {"ip": "203.0.113.11"}})
elif url.endswith("graph/service"):
    selector = request["field_selector"]["node"]
    metrics = [metric(m) for m in selector.get("metric", {}).get("upstream", []) if m not in ["ACTIVE_CONNECTIONS", "NEW_CONNECTION_RATE"]]
    node = {"id": {"vhost": vh}, "data": {"metric": {"upstream": metrics}, "healthscore": {"data": [{"type": "HEALTHSCORE_OVERALL", "value": [point("97.125")]}]}}}
    edges = []
    if "SERVICE" in request["group_by"]:
        node["id"]["service"] = "S:203.0.113.10"
        edges = [{"src_id": {"service": "public", "vhost": vh}, "dst_id": node["id"], "data": {"metric": {"data": metrics}}}]
        if scenario == "ambiguous_origin":
            edges.append({**edges[0], "src_id": {"service": "other", "vhost": vh}})
    body = {"step": "5m", "data": {"nodes": [] if scenario == "empty" else [node], "edges": edges}}
elif "access_logs" in url:
    path = Path(os.environ["PAGE"])
    page = int(path.read_text()) if path.exists() else 0
    path.write_text(str(page+1))
    row = {"namespace": ns, "vh_name": vh, "rsp_code": "200", "rsp_code_class": "2xx", "method": "GET", "req_headers": "RAW_PRIVATE_SENTINEL"}
    if scenario == "scope":
        row["namespace"] = "other"
    if scenario == "cap":
        rows, total = [row], 101
    elif scenario == "cap_complete":
        rows, total = ([row] if page < 100 else []), 100
    elif scenario == "empty":
        rows, total = [], 0
    else:
        rows, total = ([row]*100 if page == 0 else [row]*2 if page == 1 else []), 102
    if scenario == "count":
        total = 103
    body = {"total_hits": str(total), "logs": [json.dumps(r) for r in rows], "scroll_id": "cursor"}
    if scenario == "missing_cursor":
        body.pop("scroll_id")
    if scenario == "bad_log":
        body["logs"] = ["not JSON RAW_PRIVATE_SENTINEL"]
elif "calls_by_response_code" in url:
    body = {"total_calls": "102", "request_count_per_rsp_code": [{"rsp_code_class": "HTTP_RESPONSE_CODE_CLASS_2XX", "count": 102}]}
elif "top_active" in url:
    body = {"top_apieps": [{"apiep_url": "/health", "method": "GET", "top_by_metric_value": 100}]}
elif url.endswith("api_endpoints/stats"):
    body = {"total_endpoints": 1, "inventory": 0, "discovered": 1, "shadow": 0, "pii_detected": 0}
elif url.endswith("api_endpoints"):
    body = {"last_update": None, "apiep_list": [{"category": []}]}
elif "app_firewall" in url:
    body = {"step": "5m", "data": [{"type": "BLOCKED_REQUESTS", "data": [{"key": {"VIRTUAL_HOST": vh}, "value": [point("NaN" if scenario == "nonfinite" else "0")]}]}]}
elif "app_security/events" in url:
    event = {"namespace": ns, "vh_name": vh, "action": "block", "time": "2000-01-01T00:00:00Z", "req_headers": "RAW_PRIVATE_SENTINEL"}
    if scenario == "event_scope":
        event["vh_name"] = "other"
    body = {"total_hits": "1", "events": [json.dumps(event)]}
elif "app_security/metrics" in url:
    body = {"step": "5m", "data": []}
else:
    raise AssertionError(url)
if scenario == "network":
    sys.stderr.write("RAW_PRIVATE_SENTINEL " + os.environ["XCSH_API_TOKEN"])
    sys.exit(28)
fail = scenario in ["http", "json", "stream"] or (scenario == "late_http" and "app_security/metrics" in url)
if fail:
    sys.stdout.write("not JSON RAW_PRIVATE_SENTINEL" if scenario == "json" else json.dumps({"message": "RAW_PRIVATE_SENTINEL " + os.environ["XCSH_API_TOKEN"]}))
    if scenario == "stream":
        sys.stdout.write("\n{}")
else:
    sys.stdout.write(json.dumps(body))
sys.stdout.write("\n503" if scenario in ["http", "late_http"] and fail else "\n200")
"""


class ShellExamples(unittest.TestCase):
    def run_script(self, script="get-stats.sh", scenario="success", omit=None):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            mock = work / "curl"
            mock.write_text(MOCK)
            mock.chmod(0o700)
            env = {
                "PATH": f"{work}:{os.environ['PATH']}",
                "SCENARIO": scenario,
                "CALLS": str(work / "calls"),
                "PAGE": str(work / "page"),
                "XCSH_API_URL": "https://console.example.com",
                "XCSH_API_TOKEN": "SECRET_SENTINEL",
                "XCSH_NAMESPACE": "example",
                "XCSH_LOAD_BALANCER": "example",
                "XCSH_VIRTUAL_HOST": "telemetry-example",
                "XCSH_API_DISCOVERY_ENABLED": "true",
                "XCSH_APP_TYPE": "example-app",
                "XCSH_START_TIME": "100",
                "XCSH_END_TIME": "7300",
                "XCSH_START_24H": "1",
            }
            if omit:
                env.pop(omit)
            # The executable and script are fixed test-owned paths; no shell is used.
            result = subprocess.run(  # noqa: S603
                [str(shutil.which("bash")), str(ROOT / "docs/assets/scripts" / script)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            calls = (
                [json.loads(line) for line in (work / "calls").read_text().splitlines()]
                if (work / "calls").exists()
                else []
            )
            return result, calls

    def assert_failure(self, result):
        assert result.returncode != 0
        assert result.stdout == ""
        assert result.stderr.strip()
        assert "SECRET_SENTINEL" not in result.stderr
        assert "RAW_PRIVATE_SENTINEL" not in result.stderr

    def test_missing_configuration(self):
        for script in ["simple-stats.sh", "get-stats.sh"]:
            for name in [
                "XCSH_API_URL",
                "XCSH_API_TOKEN",
                "XCSH_VIRTUAL_HOST",
                "XCSH_START_TIME",
            ]:
                with self.subTest(script=script, name=name):
                    result, calls = self.run_script(script, omit=name)
                    self.assert_failure(result)
                    assert calls == []

    def test_api_discovery_prerequisites(self):
        for name in ["XCSH_API_DISCOVERY_ENABLED", "XCSH_APP_TYPE"]:
            result, calls = self.run_script(omit=name)
            self.assert_failure(result)
            assert "API activity" in result.stderr
            assert calls == []

    def test_simple(self):
        result, calls = self.run_script("simple-stats.sh")
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["maximum"] == 2.5
        assert report["latest"]["value"] == "Infinity"
        assert report["points"] == 5
        assert report["metric"] == "HTTP_REQUEST_RATE"
        assert len(calls) == 1
        assert calls[0]["request"]["label_filter"][0]["value"] == "telemetry-example"

    def test_no_finite_maximum(self):
        for scenario in ["nonfinite", "empty"]:
            result, _ = self.run_script("simple-stats.sh", scenario)
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)["maximum"] is None

    def test_complete_report(self):
        result, calls = self.run_script()
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert set(report) - {"context"} == {
            "http_access_logs",
            "load_balancer_metrics",
            "origin_performance",
            "application_health",
            "api_activity",
            "waf_metrics",
            "security_events",
        }
        assert report["http_access_logs"]["counted"] == 102
        assert report["http_access_logs"]["pages"] == 3
        assert report["api_activity"]["calls_by_response_code"]["total_calls"] == "102"
        assert (
            report["application_health"]["healthscores"][0]["latest"]["value"]
            == "97.125"
        )
        assert "NEW_CONNECTION_RATE" in report["load_balancer_metrics"]["unavailable"]
        assert report["security_events"]["metrics"]["data"] == []
        assert "RAW_PRIVATE_SENTINEL" not in result.stdout
        assert "SECRET_SENTINEL" not in result.stdout
        assert sum("api_endpoints" in call["url"] for call in calls) == 4
        for call in calls:
            assert "--connect-timeout" in call["args"]
            assert "--max-time" in call["args"]
            assert "system" not in call["url"]
            if "access_logs" in call["url"] and not call["url"].endswith("scroll"):
                assert call["request"]["limit"] == 100
                assert call["request"]["scroll"]

    def test_nonfinite_waf(self):
        result, _ = self.run_script(scenario="nonfinite")
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["waf_metrics"]["metrics"][0]["total"] is None
        assert (
            report["waf_metrics"]["metrics"][0]["series"][0]["samples"][0]["value"]
            == "NaN"
        )

    def test_failures(self):
        for scenario in [
            "http",
            "json",
            "stream",
            "network",
            "late_http",
            "ambiguous_pool",
            "ambiguous_origin",
            "scope",
            "event_scope",
            "count",
            "missing_cursor",
            "bad_log",
            "cap",
        ]:
            with self.subTest(scenario=scenario):
                result, _ = self.run_script(scenario=scenario)
                self.assert_failure(result)

    def test_simple_failures(self):
        for scenario in ["http", "json", "stream", "network"]:
            self.assert_failure(self.run_script("simple-stats.sh", scenario)[0])

    def test_complete_at_page_cap(self):
        result, _ = self.run_script(scenario="cap_complete")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["http_access_logs"]["pages"] == 100


if __name__ == "__main__":
    unittest.main()
