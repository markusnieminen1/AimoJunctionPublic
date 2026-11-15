#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8001}"
BASE_URL="${BASE_URL%/}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CREDS_FILE="${BACKEND_DIR}/.demo_credentials"

if [[ ! -f "${CREDS_FILE}" ]]; then
  echo "ERROR: Demo credentials file not found at ${CREDS_FILE}" >&2
  echo "Run: cd ${BACKEND_DIR} && uv run python scripts/demo_seed.py" >&2
  exit 1
fi

HDR_FILE="$(mktemp)"
cleanup() {
  rm -f "${HDR_FILE}"
}
trap cleanup EXIT

RUN_ID="demo-$(date +%s)-$$"

read_cred() {
  local agent_key="$1"
  local field_name="$2"
  python3 -c '
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
section = sys.argv[2]
key = sys.argv[3]
if section == "user":
    print(d["user"][key])
else:
    print(d["agents"][section][key])
' "${CREDS_FILE}" "${agent_key}" "${field_name}"
}

KITCHEN_AGENT_ID="$(read_cred 'kitchen_agent' 'id')"
KITCHEN_AGENT_SECRET="$(read_cred 'kitchen_agent' 'agent_secret')"
SCOUT_AGENT_ID="$(read_cred 'read_only_scout' 'id')"
SCOUT_AGENT_SECRET="$(read_cred 'read_only_scout' 'agent_secret')"
DEMO_EMAIL="$(read_cred 'user' 'email')"
DEMO_PASSWORD="$(read_cred 'user' 'password')"

step_header() {
  echo ""
  echo "========================================================================"
  echo "$1"
  echo "========================================================================"
}

http_call() {
  local method="$1"
  local path="$2"
  local token="${3:-}"
  local idem_key="${4:-}"
  local body="${5:-}"

  local -a args=(-sS -D "${HDR_FILE}" -w $'\n%{http_code}' -X "${method}" "${BASE_URL}${path}")
  args+=(-H "Accept: application/json")
  if [[ -n "${token}" ]]; then
    args+=(-H "Authorization: Bearer ${token}")
  fi
  if [[ -n "${idem_key}" ]]; then
    args+=(-H "Idempotency-Key: ${idem_key}")
  fi
  if [[ -n "${body}" ]]; then
    args+=(-H "Content-Type: application/json" --data "${body}")
  fi

  local raw
  raw="$(curl "${args[@]}")"
  HTTP_CODE="${raw##*$'\n'}"
  HTTP_BODY="${raw%$'\n'*}"
}

assert_code() {
  local expected="$1"
  local label="$2"
  if [[ "${HTTP_CODE}" != "${expected}" ]]; then
    echo "FAILED (${label}): expected HTTP ${expected}, got HTTP ${HTTP_CODE}" >&2
    echo "${HTTP_BODY}" >&2
    exit 1
  fi
}

step_header "1. Health check (GET /api/health)"
http_call "GET" "/api/health"
assert_code "200" "GET /api/health"
SERVER_TIME_HEADER="$(grep -i '^x-server-time:' "${HDR_FILE}" | head -n 1 | cut -d':' -f2- | tr -d '\r' | xargs || true)"
NEXT_MONDAY="$(python3 -c '
import json, sys
from datetime import datetime, timedelta
body = json.loads(sys.stdin.read())
srv_time = (sys.argv[1] or "").strip() or body["server_time"]
dt = datetime.fromisoformat(srv_time)
days_ahead = (0 - dt.weekday()) % 7
if days_ahead == 0:
    days_ahead = 7
next_mon = (dt.date() + timedelta(days=days_ahead)).isoformat()
st, db_st, aimo_st, mode = body["status"], body["db"], body["aimo"], body["aimo_mode"]
print(f"Health: status={st} db={db_st} aimo={aimo_st} mode={mode}", file=sys.stderr)
print(f"Server time: {srv_time} -> Next Monday: {next_mon}", file=sys.stderr)
print(next_mon)
' "${SERVER_TIME_HEADER}" <<< "${HTTP_BODY}")"

step_header "2. Exchange Hotel Aurora Kitchen Agent credentials (POST /api/agent-auth/token)"
AUTH_PAYLOAD="$(python3 -c 'import json, sys; print(json.dumps({"agent_id": sys.argv[1], "agent_secret": sys.argv[2]}))' "${KITCHEN_AGENT_ID}" "${KITCHEN_AGENT_SECRET}")"
http_call "POST" "/api/agent-auth/token" "" "" "${AUTH_PAYLOAD}"
assert_code "200" "POST /api/agent-auth/token (kitchen)"
KITCHEN_TOKEN="$(python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
ag = d["agent"]
ag_name, ag_id = ag["name"], ag["id"]
delegator = ag["delegated_by"]["name"]
auto_lim = ag["auto_approve_limit_eur"]
max_lim = ag["max_order_value_eur"]
daily_lim = ag["daily_spend_limit_eur"]
exp = d["expires_at"]
print(f"Authenticated: {ag_name} ({ag_id}), delegated_by={delegator}", file=sys.stderr)
print(f"Limits: auto=EUR {auto_lim:.2f}, max=EUR {max_lim:.2f}, daily=EUR {daily_lim:.2f}", file=sys.stderr)
print(f"Token expires_at: {exp}", file=sys.stderr)
print(d["token"])
' <<< "${HTTP_BODY}")"

step_header "3. Search lactose-free milk (GET /api/products?q=laktoositon+maito&diet=L)"
http_call "GET" "/api/products?q=laktoositon+maito&diet=L" "${KITCHEN_TOKEN}"
assert_code "200" "GET /api/products"
python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
ids = [item["id"] for item in d["items"]]
assert "464306" in ids, f"Expected 464306 in search results, got {ids}"
total = d["total"]
target = next(item for item in d["items"] if item["id"] == "464306")
print(f"Found {total} matching products; top results (and target 464306):")
for item in d["items"][:5]:
    pid, pname, unit = item["id"], item["name"], item["sales_unit"]
    co2, rel = item.get("co2_kg_per_kg"), item.get("delivery_reliability")
    print(f"  - {pid}: {pname} (unit={unit}, co2={co2}, rel={rel})")
t_id, t_name, t_unit = target["id"], target["name"], target["sales_unit"]
t_co2, t_rel = target.get("co2_kg_per_kg"), target.get("delivery_reliability")
print(f"  -> Selected {t_id}: {t_name} (unit={t_unit}, co2={t_co2}, rel={t_rel})")
' <<< "${HTTP_BODY}"

step_header "4. Quote 1,000 x 464306 for next Monday ${NEXT_MONDAY} (POST /api/quotes)"
QUOTE_PAYLOAD="$(python3 -c 'import json, sys; print(json.dumps({"requested_delivery_date": sys.argv[1], "lines": [{"product_id": "464306", "quantity": 1000}], "diet": "L"}))' "${NEXT_MONDAY}")"
http_call "POST" "/api/quotes" "${KITCHEN_TOKEN}" "" "${QUOTE_PAYLOAD}"
assert_code "201" "POST /api/quotes (1000 x 464306)"
QUOTE_ID="$(python3 -c '
import json, sys
q = json.loads(sys.stdin.read())
line = q["lines"][0]
rules = [(r["rule_id"], r["outcome"]) for r in q["policy_preview"]["rules"]]
qid, qexp = q["id"], q["expires_at"]
qty, pid = line["quantity"], line["product_id"]
unit_p = line["unit_price_eur"]
tot_ex, tot_in = q["total_excl_vat_eur"], q["total_incl_vat_eur"]
deliv, avail_d, closing_t = q["overall_delivery_status"], line["available_date"], line["closing_time"]
co2_kg, co2_basis = q["total_co2_kg"], line["co2_basis"]
outcome = q["policy_preview"]["outcome"]
print(f"Quote ID: {qid} (expires_at={qexp})", file=sys.stderr)
print(f"Line: {qty:g} x {pid} @ EUR {unit_p:.2f} = EUR {tot_ex:.2f} excl. VAT (EUR {tot_in:.2f} incl. VAT)", file=sys.stderr)
print(f"Delivery: {deliv} (available_date={avail_d}, closing_time={closing_t})", file=sys.stderr)
print(f"CO2: {co2_kg} kg ({co2_basis})", file=sys.stderr)
print(f"Policy preview: {outcome} rules={rules}", file=sys.stderr)
assert outcome == "APPROVAL_REQUIRED", q["policy_preview"]
print(qid)
' <<< "${HTTP_BODY}")"

step_header "5. Place order for quote ${QUOTE_ID} -> PENDING_APPROVAL (POST /api/orders)"
ORDER_PAYLOAD="$(python3 -c 'import json, sys; print(json.dumps({"quote_id": sys.argv[1], "note": "Hotel Aurora Monday breakfast milk (1,000 L)"}))' "${QUOTE_ID}")"
http_call "POST" "/api/orders" "${KITCHEN_TOKEN}" "${RUN_ID}-step5" "${ORDER_PAYLOAD}"
assert_code "201" "POST /api/orders (1000 x 464306)"
ORDER_META="$(python3 -c '
import json, sys
o = json.loads(sys.stdin.read())
rule_ids = [r["rule_id"] for r in o["policy"]["rules"]]
oid, status_val = o["id"], o["status"]
app_exp, chash = o["approval_expires_at"], o["content_hash"]
assert status_val == "PENDING_APPROVAL", f"Expected PENDING_APPROVAL, got {status_val}"
assert "R-AUTO-LIMIT" in rule_ids, f"Expected R-AUTO-LIMIT, got {rule_ids}"
print(f"Order ID: {oid}", file=sys.stderr)
print(f"Status: {status_val} (approval_expires_at={app_exp})", file=sys.stderr)
print(f"Rules fired: {rule_ids}", file=sys.stderr)
print(f"Content hash: {chash}", file=sys.stderr)
print(f"{oid}|{chash}")
' <<< "${HTTP_BODY}")"
PENDING_ORDER_ID="${ORDER_META%%|*}"
PENDING_CONTENT_HASH="${ORDER_META#*|}"

step_header "6a. Exception demo: Partial stock on 469363 (384 of 1,000) + Substitutes"
PARTIAL_QUOTE_PAYLOAD="$(python3 -c 'import json, sys; print(json.dumps({"requested_delivery_date": sys.argv[1], "lines": [{"product_id": "469363", "quantity": 1000}], "diet": "L"}))' "${NEXT_MONDAY}")"
http_call "POST" "/api/quotes" "${KITCHEN_TOKEN}" "" "${PARTIAL_QUOTE_PAYLOAD}"
assert_code "201" "POST /api/quotes (1000 x 469363)"
python3 -c '
import json, sys
q = json.loads(sys.stdin.read())
line = q["lines"][0]
qid = q["id"]
dstat, qstate, avail, flags = line["delivery_status"], line["available_qty_state"], line["available_amount"], line["flags"]
print(f"Partial stock quote {qid}: status={dstat}, state={qstate}, available={avail}, flags={flags}")
assert "PARTIAL" in flags, flags
' <<< "${HTTP_BODY}"

SUB_PAYLOAD="$(python3 -c 'import json, sys; print(json.dumps({"product_id": "469363", "quantity": 1000, "requested_delivery_date": sys.argv[1], "diet": "L"}))' "${NEXT_MONDAY}")"
http_call "POST" "/api/products/substitutes" "${KITCHEN_TOKEN}" "" "${SUB_PAYLOAD}"
assert_code "200" "POST /api/products/substitutes"
python3 -c '
import json, sys
s = json.loads(sys.stdin.read())
items = s["items"]
sub_ids = [item["product"]["id"] for item in items]
assert "464306" in sub_ids or "464398" in sub_ids, f"Expected 464306/464398 in substitutes, got {sub_ids}"
print(f"Diet-safe substitutes for 469363 ({len(items)} found):")
for item in items:
    p = item["product"]
    ql = item["quote_line"]
    pid, pname, unit = p["id"], p["name"], p["sales_unit"]
    up, dstat, toffs = ql["unit_price_eur"], ql["delivery_status"], item["tradeoffs"]
    print(f"  - {pid}: {pname} | EUR {up:.2f}/{unit} | {dstat} | tradeoffs={toffs}")
' <<< "${HTTP_BODY}"

step_header "6b. Exception demo: Over max order value (10,000 x 464306) -> 403 POLICY_BLOCKED (R-MAX-VALUE)"
MAX_QUOTE_PAYLOAD="$(python3 -c 'import json, sys; print(json.dumps({"requested_delivery_date": sys.argv[1], "lines": [{"product_id": "464306", "quantity": 10000}], "diet": "L"}))' "${NEXT_MONDAY}")"
http_call "POST" "/api/quotes" "${KITCHEN_TOKEN}" "" "${MAX_QUOTE_PAYLOAD}"
assert_code "201" "POST /api/quotes (10000 x 464306)"
MAX_QUOTE_ID="$(python3 -c 'import json, sys; print(json.loads(sys.stdin.read())["id"])' <<< "${HTTP_BODY}")"

MAX_ORDER_PAYLOAD="$(python3 -c 'import json, sys; print(json.dumps({"quote_id": sys.argv[1]}))' "${MAX_QUOTE_ID}")"
http_call "POST" "/api/orders" "${KITCHEN_TOKEN}" "${RUN_ID}-step6b" "${MAX_ORDER_PAYLOAD}"
assert_code "403" "POST /api/orders (over max value)"
python3 -c '
import json, sys
err = json.loads(sys.stdin.read())
code = err["error"]["code"]
req_id = err["request_id"]
rule_ids = [r["rule_id"] for r in err["error"]["details"]["rules"]]
assert code == "POLICY_BLOCKED", err
assert "R-MAX-VALUE" in rule_ids, rule_ids
print(f"Blocked as expected: code={code} rules={rule_ids} request_id={req_id}")
' <<< "${HTTP_BODY}"

step_header "6c. Exception demo: Milk-free diet violation (diet=M on 464306) -> 403 POLICY_BLOCKED (R-HARD-DIET)"
DIET_QUOTE_PAYLOAD="$(python3 -c 'import json, sys; print(json.dumps({"requested_delivery_date": sys.argv[1], "lines": [{"product_id": "464306", "quantity": 10}], "diet": "M"}))' "${NEXT_MONDAY}")"
http_call "POST" "/api/quotes" "${KITCHEN_TOKEN}" "" "${DIET_QUOTE_PAYLOAD}"
assert_code "201" "POST /api/quotes (diet=M on 464306)"
DIET_QUOTE_ID="$(python3 -c 'import json, sys; print(json.loads(sys.stdin.read())["id"])' <<< "${HTTP_BODY}")"

DIET_ORDER_PAYLOAD="$(python3 -c 'import json, sys; print(json.dumps({"quote_id": sys.argv[1], "diet": "M"}))' "${DIET_QUOTE_ID}")"
http_call "POST" "/api/orders" "${KITCHEN_TOKEN}" "${RUN_ID}-step6c" "${DIET_ORDER_PAYLOAD}"
assert_code "403" "POST /api/orders (diet=M on 464306)"
python3 -c '
import json, sys
err = json.loads(sys.stdin.read())
code = err["error"]["code"]
req_id = err["request_id"]
rule_ids = [r["rule_id"] for r in err["error"]["details"]["rules"]]
assert code == "POLICY_BLOCKED", err
assert "R-HARD-DIET" in rule_ids, rule_ids
print(f"Blocked as expected: code={code} rules={rule_ids} request_id={req_id}")
' <<< "${HTTP_BODY}"

step_header "7. Read-only Scout order attempt -> 403 POLICY_BLOCKED (R-AGENT-NO-ORDER)"
SCOUT_AUTH_PAYLOAD="$(python3 -c 'import json, sys; print(json.dumps({"agent_id": sys.argv[1], "agent_secret": sys.argv[2]}))' "${SCOUT_AGENT_ID}" "${SCOUT_AGENT_SECRET}")"
http_call "POST" "/api/agent-auth/token" "" "" "${SCOUT_AUTH_PAYLOAD}"
assert_code "200" "POST /api/agent-auth/token (scout)"
SCOUT_TOKEN="$(python3 -c 'import json, sys; print(json.loads(sys.stdin.read())["token"])' <<< "${HTTP_BODY}")"

SCOUT_QUOTE_PAYLOAD="$(python3 -c 'import json, sys; print(json.dumps({"requested_delivery_date": sys.argv[1], "lines": [{"product_id": "464306", "quantity": 10}]}))' "${NEXT_MONDAY}")"
http_call "POST" "/api/quotes" "${SCOUT_TOKEN}" "" "${SCOUT_QUOTE_PAYLOAD}"
assert_code "201" "POST /api/quotes (scout)"
SCOUT_QUOTE_ID="$(python3 -c 'import json, sys; print(json.loads(sys.stdin.read())["id"])' <<< "${HTTP_BODY}")"

SCOUT_ORDER_PAYLOAD="$(python3 -c 'import json, sys; print(json.dumps({"quote_id": sys.argv[1]}))' "${SCOUT_QUOTE_ID}")"
http_call "POST" "/api/orders" "${SCOUT_TOKEN}" "${RUN_ID}-step7" "${SCOUT_ORDER_PAYLOAD}"
assert_code "403" "POST /api/orders (scout)"
python3 -c '
import json, sys
err = json.loads(sys.stdin.read())
code = err["error"]["code"]
req_id = err["request_id"]
rule_ids = [r["rule_id"] for r in err["error"]["details"]["rules"]]
assert code == "POLICY_BLOCKED", err
assert "R-AGENT-NO-ORDER" in rule_ids, rule_ids
print(f"Blocked as expected: code={code} rules={rule_ids} request_id={req_id}")
' <<< "${HTTP_BODY}"

step_header "8. Demo Summary & Next Step (Human Approval)"
cat <<EOF
External agent flow completed successfully against ${BASE_URL}:
  - Pending Approval Order ID : ${PENDING_ORDER_ID}
  - Content Hash              : ${PENDING_CONTENT_HASH}
  - Requested Delivery Date   : ${NEXT_MONDAY}
  - Exceptions Verified       : 469363 partial stock + substitutes (464306/464398),
                                R-MAX-VALUE block, R-HARD-DIET block, R-AGENT-NO-ORDER block

To approve the pending order on phone / Web UI:
  1. Log in as Anna Korhonen (${DEMO_EMAIL} / ${DEMO_PASSWORD})
  2. Open the Approval Inbox (GET /api/orders?status=PENDING_APPROVAL)
  3. Tap Approve on order ${PENDING_ORDER_ID} (or call):
     POST ${BASE_URL}/api/orders/${PENDING_ORDER_ID}/approve
     Headers: Authorization: Bearer <USER_JWT>, Idempotency-Key: approve-${RUN_ID}
     Body: {"content_hash": "${PENDING_CONTENT_HASH}", "note": "Approved by Anna"}
EOF
