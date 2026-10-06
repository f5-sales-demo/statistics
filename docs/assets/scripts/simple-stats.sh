#!/usr/bin/env bash
set -euo pipefail

# Run Overview setup and Query reference discovery in the parent Bash session.
for name in XCSH_API_URL XCSH_API_TOKEN XCSH_NAMESPACE XCSH_VIRTUAL_HOST \
  XCSH_START_TIME XCSH_END_TIME; do
  if [[ -z "${!name:-}" ]]; then
    printf 'Request rate: export %s using Overview and Query reference.\n' "$name" >&2
    exit 1
  fi
done
if ! jq -en --arg start "$XCSH_START_TIME" --arg end "$XCSH_END_TIME" \
  '$start | test("^[0-9]+$")' >/dev/null 2>&1 ||
  ! jq -en --arg start "$XCSH_START_TIME" --arg end "$XCSH_END_TIME" \
    '($end | test("^[0-9]+$")) and (($start | tonumber) < ($end | tonumber))' >/dev/null 2>&1; then
  printf 'Request rate: refresh the query window in Query reference.\n' >&2
  exit 1
fi
trap 'printf "Request rate: invalid response or query.\n" >&2' ERR

request="$(jq -nc --arg ns "$XCSH_NAMESPACE" --arg vh "$XCSH_VIRTUAL_HOST" \
  --arg start "$XCSH_START_TIME" --arg end "$XCSH_END_TIME" '{
    namespace: $ns, start_time: $start, end_time: $end,
    step: "5m", range: "5m", group_by: ["VHOST"],
    label_filter: [{label: "LABEL_VHOST", op: "EQ", value: $vh}],
    field_selector: {node: {metric: {upstream: ["HTTP_REQUEST_RATE"]}}}
  }')"
if ! response="$(curl --silent --connect-timeout 10 --max-time 60 \
  --request POST --header "Authorization: APIToken ${XCSH_API_TOKEN}" \
  --header 'Content-Type: application/json' --data-binary "$request" \
  --write-out $'\n%{http_code}' \
  "${XCSH_API_URL%/}/api/data/namespaces/${XCSH_NAMESPACE}/graph/service" 2>/dev/null)"; then
  printf 'Request rate: HTTP transport failed.\n' >&2
  exit 1
fi
status="${response##*$'\n'}"
if [[ ! "$status" =~ ^2[0-9][0-9]$ ]]; then
  printf 'Request rate: HTTP request failed. Check access in Overview.\n' >&2
  exit 1
fi
body="$(jq -cse 'if length == 1 and (.[0] | type == "object")
  then .[0] else error("Expected one JSON object") end' \
  <<<"${response%$'\n'*}" 2>/dev/null)"
report="$(jq -c --arg vh "$XCSH_VIRTUAL_HOST" '
  def finite: if type == "number" or type == "string" then
    (try tonumber catch null) | select(type == "number")
    | select(isfinite and (isnan | not)) else empty end;
  [.data.nodes[]? | select(.id.vhost == $vh)
    | .data.metric.upstream[]? | select(.type == "HTTP_REQUEST_RATE")]
  | if length > 1 then error("Ambiguous request-rate series") else .[0] end
  | {metric: (.type // "HTTP_REQUEST_RATE"), unit, points: (.value.raw | length),
     latest: (.value.raw[-1] // null),
     maximum: ([.value.raw[]?.value | finite] | max)}
  ' <<<"$body" 2>/dev/null)"
printf '%s\n' "$report"
