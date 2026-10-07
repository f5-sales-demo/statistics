#!/usr/bin/env bash
set -euo pipefail

# Export shared setup/discovery first. This example never loads a file or probes traffic.
section='Setup'
fail() {
  printf '%s: %s\n' "$section" "$1" >&2
  exit 1
}
for name in XCSH_API_URL XCSH_API_TOKEN XCSH_NAMESPACE XCSH_LB_NAME \
  XCSH_VIRTUAL_HOST XCSH_START_TIME XCSH_END_TIME XCSH_START_24H; do
  [[ -n "${!name:-}" ]] || fail "export $name using Overview and Query reference."
done
section='API activity'
[[ "${XCSH_API_DISCOVERY_ENABLED:-}" == true ]] ||
  fail 'API discovery must be enabled; repeat identifier discovery in Query reference.'
[[ -n "${XCSH_APP_TYPE:-}" ]] ||
  fail 'export XCSH_APP_TYPE from the load balancer label in Query reference.'
section='Setup'
jq -en --arg start "$XCSH_START_TIME" --arg end "$XCSH_END_TIME" \
  --arg day "$XCSH_START_24H" '
  all([$start, $end, $day][]; test("^[0-9]+$")) and
  (($day | tonumber) < ($start | tonumber)) and
  (($start | tonumber) < ($end | tonumber))' >/dev/null 2>&1 ||
  fail 'refresh the query window in Query reference.'
trap 'printf "%s: invalid response or query.\n" "$section" >&2' ERR

# Only HTTP handling and graph summaries are shared between the labeled sections.
api() {
  local method="$1" path="$2" payload="${3:-}" response status
  local -a args=(--silent --connect-timeout 10 --max-time 60
    --request "$method" --header "Authorization: APIToken ${XCSH_API_TOKEN}"
    --header 'Accept: application/json' --write-out $'\n%{http_code}')
  if [[ -n "$payload" ]]; then
    args+=(--header 'Content-Type: application/json' --data-binary "$payload")
  fi
  response="$(curl "${args[@]}" "${XCSH_API_URL%/}${path}" 2>/dev/null)" ||
    fail 'HTTP transport failed.'
  status="${response##*$'\n'}"
  [[ "$status" =~ ^2[0-9][0-9]$ ]] || fail 'HTTP request failed; check access in Overview.'
  body="$(jq -cse 'if length == 1 and (.[0] | type == "object")
    then .[0] else error("Expected one JSON object") end' \
    <<<"${response%$'\n'*}" 2>/dev/null)" || fail 'expected one valid JSON response object.'
}
graph_summary() {
  jq -c --arg vh "$XCSH_VIRTUAL_HOST" --arg src "${source:-}" \
    --arg dst "${destination:-}" --argjson expected "$1" '
    def finite: if type == "number" or type == "string" then
      (try tonumber catch null) | select(type == "number")
      | select(isfinite and (isnan | not)) else empty end;
    def summary: {type, unit, points: (.value.raw | length),
      samples: (.value.raw // []), latest: (.value.raw[-1] // null),
      maximum: ([.value.raw[]?.value | finite] | max)};
    [.data.nodes[]? | select(.id.vhost == $vh and
      ($dst == "" or .id.service == $dst)) | .data.metric.upstream[]?] as $node
    | [.data.edges[]? | select(.dst_id.vhost == $vh and
      .src_id.service == $src and .dst_id.service == $dst)
      | .data.metric.data[]?] as $edge
    | {step, metrics: ($node | map(summary) | sort_by(.type)),
       unavailable: ($expected - [$node[].type])}
      + if $dst == "" then {} else
        {source: $src, destination: $dst,
         edge: ($edge | map(summary) | sort_by(.type)),
         edge_unavailable: ($expected - [$edge[].type])} end
  ' <<<"$body" 2>/dev/null
}
base="$(jq -nc --arg ns "$XCSH_NAMESPACE" --arg start "$XCSH_START_TIME" \
  --arg end "$XCSH_END_TIME" '{namespace: $ns, start_time: $start,
    end_time: $end, step: "5m", range: "5m"}')"
graph_base="$(jq -c --arg vh "$XCSH_VIRTUAL_HOST" '. + {
  group_by: ["VHOST"], label_filter: [{label: "LABEL_VHOST", op: "EQ", value: $vh}]
}' <<<"$base")"
graph_path="/api/data/namespaces/${XCSH_NAMESPACE}/graph/service"

# 1. HTTP access logs: complete fixed-window snapshot, outcome fields only.
section='HTTP access logs'
query="$(jq -nr --arg vh "$XCSH_VIRTUAL_HOST" '"{vh_name=" + ($vh | tojson) + "}"')"
request="$(jq -c --arg query "$query" 'del(.step, .range) + {
  limit: 100, sort: "DESCENDING", scroll: true, query: $query}' <<<"$base")"
api POST "/api/data/namespaces/${XCSH_NAMESPACE}/access_logs" "$request"
total="$(jq -er '.total_hits | tonumber | select(isfinite and . >= 0 and . == floor)' \
  <<<"$body" 2>/dev/null)"
rows='[]'
pages=0
while :; do
  pages="$((pages + 1))"
  page="$(jq -ec --arg ns "$XCSH_NAMESPACE" --arg vh "$XCSH_VIRTUAL_HOST" '
    if (.logs | type) != "array" then error("Expected log array") else
    [.logs[] | (if type == "string" then fromjson else . end)
      | if .namespace == $ns and .vh_name == $vh
        then {rsp_code, rsp_code_class, method} else error("Scope mismatch") end] end
    ' <<<"$body" 2>/dev/null)" || fail 'invalid logs or records outside the selected scope.'
  count="$(jq 'length' <<<"$page")"
  ((count <= 100)) || fail 'page exceeds the 100-record limit.'
  rows="$(jq -nc --argjson rows "$rows" --argjson page "$page" '$rows + $page')"
  counted="$(jq 'length' <<<"$rows")"
  ((counted <= total)) || fail 'record count exceeds total_hits.'
  ((count > 0)) || break
  # Equality also permits a complete snapshot at the page cap without a 101st request.
  ((pages < 100)) || {
    ((counted == total)) || fail '100-page cap reached before total_hits; use a narrower window.'
    break
  }
  cursor="$(jq -er '.scroll_id // "" | select(type == "string")' <<<"$body" 2>/dev/null)"
  [[ -n "$cursor" ]] || break
  request="$(jq -nc --arg ns "$XCSH_NAMESPACE" --arg cursor "$cursor" \
    '{namespace: $ns, scroll_id: $cursor}')"
  api POST "/api/data/namespaces/${XCSH_NAMESPACE}/access_logs/scroll" "$request"
done
((counted == total)) || fail 'incomplete pagination: counted records differ from total_hits.'
logs="$(jq -c --argjson total "$total" --argjson pages "$pages" '
  {scope: "complete_window", total_hits: $total, pages: $pages, counted: length,
   response_codes: (sort_by(.rsp_code) | group_by(.rsp_code) |
     map({code: .[0].rsp_code, count: length})),
   response_classes: (sort_by(.rsp_code_class) | group_by(.rsp_code_class) |
     map({class: .[0].rsp_code_class, count: length})),
   methods: (sort_by(.method) | group_by(.method) |
     map({method: .[0].method, count: length}))}' <<<"$rows")"

# 2. Load balancer metrics: VHOST grouping retains ingress/egress selectors.
section='Load balancer metrics'
metrics='["HTTP_REQUEST_RATE","HTTP_ERROR_RATE","HTTP_ERROR_RATE_4XX",
  "HTTP_ERROR_RATE_5XX","HTTP_RESPONSE_LATENCY","REQUEST_THROUGHPUT",
  "RESPONSE_THROUGHPUT","REQUEST_TO_ORIGIN_RATE","CLIENT_RTT","SERVER_RTT",
  "HTTP_SERVER_DATA_TRANSFER_TIME","HTTP_INGRESS_REQUEST_RATE","HTTP_EGRESS_REQUEST_RATE",
  "HTTP_RESPONSE_LATENCY_PERCENTILE_50","HTTP_RESPONSE_LATENCY_PERCENTILE_90",
  "HTTP_RESPONSE_LATENCY_PERCENTILE_99","ACTIVE_CONNECTIONS","NEW_CONNECTION_RATE"]'
request="$(jq -c --argjson metrics "$metrics" '. + {
  field_selector: {node: {metric: {upstream: $metrics}}}}' <<<"$graph_base")"
api POST "$graph_path" "$request"
traffic="$(graph_summary "$metrics")"

# 3. Origin performance: require one configured public-IP origin and one exact edge.
section='Origin performance'
api GET "/api/config/namespaces/${XCSH_NAMESPACE}/http_loadbalancers/${XCSH_LB_NAME}"
pool="$(jq -er --arg ns "$XCSH_NAMESPACE" '
  [.spec.default_route_pools[]?.pool] | unique
  | if length == 1 and ((.[0].namespace // $ns) == $ns) and
      (.[0].name | type == "string" and length > 0)
    then .[0].name else error("Expected one pool") end' <<<"$body" 2>/dev/null)" ||
  fail 'expected one default origin pool in the selected namespace.'
api GET "/api/config/namespaces/${XCSH_NAMESPACE}/origin_pools/${pool}"
origin="$(jq -er '.spec.origin_servers
  | if length == 1 and (.[0].public_ip.ip | type == "string" and length > 0)
    then .[0].public_ip.ip else error("Expected one public-IP origin") end' \
  <<<"$body" 2>/dev/null)" || fail 'expected one configured public-IP origin.'
request="$(jq -c '. + {group_by: ["VHOST", "SERVICE"], field_selector: {
  node: {metric: {upstream: ["HTTP_REQUEST_RATE"]}},
  edge: {metric: {types: ["HTTP_REQUEST_RATE"]}}}}' <<<"$graph_base")"
api POST "$graph_path" "$request"
match="$(jq -ec --arg vh "$XCSH_VIRTUAL_HOST" --arg ip "$origin" '
  [.data.edges[]? | select(.dst_id.vhost == $vh and .dst_id.service == ("S:" + $ip))
    | {source: .src_id.service, destination: .dst_id.service}] | unique
  | if length == 1 and (.[0].source | type == "string" and length > 0)
    then .[0] else error("No unique configured origin edge") end' \
  <<<"$body" 2>/dev/null)" || fail 'no unique graph edge matches the configured origin; repeat origin discovery.'
source="$(jq -r '.source' <<<"$match")"
destination="$(jq -r '.destination' <<<"$match")"
metrics='["HTTP_REQUEST_RATE","HTTP_ERROR_RATE","HTTP_ERROR_RATE_4XX",
  "HTTP_ERROR_RATE_5XX","HTTP_RESPONSE_LATENCY","REQUEST_THROUGHPUT",
  "RESPONSE_THROUGHPUT","REQUEST_TO_ORIGIN_RATE","CLIENT_RTT","SERVER_RTT",
  "HTTP_SERVER_DATA_TRANSFER_TIME"]'
request="$(jq -c --argjson metrics "$metrics" '. + {
  group_by: ["VHOST", "SERVICE"], field_selector: {
    node: {metric: {upstream: $metrics}}, edge: {metric: {types: $metrics}}}}
  ' <<<"$graph_base")"
api POST "$graph_path" "$request"
origin_metrics="$(graph_summary "$metrics")"

# 4. Application health: retain returned score samples without inventing a unit.
section='Application health'
request="$(jq -c '. + {field_selector: {node: {healthscore: {types: [
  "HEALTHSCORE_CONNECTIVITY","HEALTHSCORE_PERFORMANCE","HEALTHSCORE_SECURITY",
  "HEALTHSCORE_RELIABILITY","HEALTHSCORE_OVERALL"]}}}}' <<<"$graph_base")"
api POST "$graph_path" "$request"
health="$(jq -c --arg vh "$XCSH_VIRTUAL_HOST" '
  [.data.nodes[]? | select(.id.vhost == $vh) | .data.healthscore.data[]?] as $scores
  | {step, healthscores: ($scores | map({type, points: (.value | length),
      samples: (.value // []), latest: (.value[-1] // null)}) | sort_by(.type)),
     unavailable: (["HEALTHSCORE_CONNECTIVITY","HEALTHSCORE_PERFORMANCE",
       "HEALTHSCORE_SECURITY","HEALTHSCORE_RELIABILITY","HEALTHSCORE_OVERALL"] -
       [$scores[].type])}' <<<"$body" 2>/dev/null)"

# 5. API activity: two fixed-window summaries and two discovery snapshots.
section='API activity'
api_path="/api/ml/data/namespaces/${XCSH_NAMESPACE}/virtual_hosts/${XCSH_VIRTUAL_HOST}/api_endpoints"
request="$(jq -nc --arg ns "$XCSH_NAMESPACE" --arg vh "$XCSH_VIRTUAL_HOST" \
  --arg start "$XCSH_START_TIME" --arg end "$XCSH_END_TIME" '{
    namespace: $ns, name: $vh, apiep_summary_filter: {start_time: $start, end_time: $end}}')"
api POST "${api_path}/summary/calls_by_response_code" "$request"
calls="$(jq -c '{total_calls, request_count_per_rsp_code}' <<<"$body")"
request="$(jq -c '. + {topk: 5, top_by_metric: "ACTIVITY_METRIC_TYPE_REQ_PERCENTAGE"}' <<<"$request")"
api POST "${api_path}/summary/top_active" "$request"
top="$(jq -c '{top_apieps: [.top_apieps[]? | {apiep_url, method, top_by_metric_value}]}' <<<"$body")"
api GET "${api_path}/stats"
stats="$(jq -c '{total_endpoints, inventory, discovered, shadow, pii_detected}' <<<"$body")"
api GET "/api/ml/data/namespaces/${XCSH_NAMESPACE}/app_types/${XCSH_APP_TYPE}/api_endpoints"
inventory="$(jq -c '{last_update, count: (.apiep_list | length),
  categories: ([.apiep_list[]?.category] | group_by(.) | map({category: .[0], count: length}))}' \
  <<<"$body" 2>/dev/null)"

# 6. WAF metrics: retain matching counter buckets and distinguish unavailable totals.
section='WAF metrics'
request="$(jq -c '. + {field_selector: ["TOTAL_REQUESTS","ATTACKED_REQUESTS",
  "BLOCKED_REQUESTS","BOT_DETECTION"], group_by: ["VIRTUAL_HOST"]}' <<<"$base")"
api POST "/api/data/namespaces/${XCSH_NAMESPACE}/app_firewall/metrics" "$request"
waf="$(jq -c --arg vh "$XCSH_VIRTUAL_HOST" '
  def finite: if type == "number" or type == "string" then
    (try tonumber catch null) | select(type == "number")
    | select(isfinite and (isnan | not)) else empty end;
  {step, metrics: [.data[]? | {type} + (if has("unit") then {unit} else {} end) + {
    series: [.data[]? | select(.key.VIRTUAL_HOST == $vh)
      | {key, samples: (.value // []), points: (.value | length), latest: (.value[-1] // null)}],
    total: ([.data[]? | select(.key.VIRTUAL_HOST == $vh) | .value[]?.value] as $values
      | [$values[] | finite] as $finite
      | if ($values | length) > 0 and ($finite | length) == ($values | length)
        then ($finite | add) else null end)}],
   unavailable: (["TOTAL_REQUESTS","ATTACKED_REQUESTS","BLOCKED_REQUESTS","BOT_DETECTION"] -
     [.data[]? | select(any(.data[]?; .key.VIRTUAL_HOST == $vh)) | .type])}
  ' <<<"$body" 2>/dev/null)"

# 7. Security events: bounded block logs and a separate fixed-window metric query.
section='Security events'
query="$(jq -nr --arg vh "$XCSH_VIRTUAL_HOST" \
  '"{vh_name=" + ($vh | tojson) + ",action=\"block\"}"')"
request="$(jq -nc --arg ns "$XCSH_NAMESPACE" --arg start "$XCSH_START_24H" \
  --arg end "$XCSH_END_TIME" --arg query "$query" '{namespace: $ns,
    start_time: $start, end_time: $end, limit: 3, sort: "DESCENDING", query: $query}')"
api POST "/api/data/namespaces/${XCSH_NAMESPACE}/app_security/events" "$request"
events="$(jq -c --arg ns "$XCSH_NAMESPACE" --arg vh "$XCSH_VIRTUAL_HOST" '
  {total_hits, returned: (.events | length), events: [.events[]?
    | (if type == "string" then fromjson else . end)
    | if .namespace == $ns and .vh_name == $vh and .action == "block" then
      {time, namespace, vh_name, action, sec_event_type, sec_event_name, rsp_code, app_firewall_name}
      else error("Event scope mismatch") end]}' <<<"$body" 2>/dev/null)" ||
  fail 'invalid events or records outside the selected block-event scope.'
request="$(jq -c --arg vh "$XCSH_VIRTUAL_HOST" 'del(.range) + {
  group_by: ["VH_NAME","SEC_EVENT_TYPE"], label_filter: [{label: "VH_NAME", op: "EQ", value: $vh}]}
  ' <<<"$base")"
api POST "/api/data/namespaces/${XCSH_NAMESPACE}/app_security/metrics" "$request"
security_metrics="$(jq -c '{data, step}' <<<"$body")"

# Print only after every query and completeness check succeeds.
section='Report'
report="$(jq -n --argjson context "$base" --arg vh "$XCSH_VIRTUAL_HOST" \
  --arg lb "$XCSH_LB_NAME" --arg app "$XCSH_APP_TYPE" --arg day "$XCSH_START_24H" \
  --argjson logs "$logs" --argjson traffic "$traffic" --argjson origin "$origin_metrics" \
  --argjson health "$health" --argjson calls "$calls" --argjson top "$top" \
  --argjson stats "$stats" --argjson inventory "$inventory" --argjson waf "$waf" \
  --argjson events "$events" --argjson security "$security_metrics" '{
    context: ($context + {virtual_host: $vh, load_balancer: $lb, app_type: $app,
      security_events_start_time: $day}),
    http_access_logs: $logs, load_balancer_metrics: $traffic, origin_performance: $origin,
    application_health: $health, api_activity: {calls_by_response_code: $calls,
      top_active: $top, stats: $stats, inventory: $inventory}, waf_metrics: $waf,
    security_events: {logs: $events, metrics: $security}}' 2>/dev/null)"
printf '%s\n' "$report"
