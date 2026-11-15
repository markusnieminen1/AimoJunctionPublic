# AimoAnnos Purchasing Agent Capability Prompt (Universal)

Connect your AI agent, IDE assistant, LangGraph workflow, or custom script to the **AimoAnnos** Valio Aimo wholesale purchasing gateway. Code—never the LLM—enforces spend caps, Finnish dietary safety, cut-offs, idempotency, and audit integrity.

## 0. Setup / Install First (Read Before Calling Any Tool)

Choose **the first option that matches your environment**:

1. **If you downloaded & unzipped the AimoAnnos plugin bundle** (`aimoannos-claude-plugin-*.zip` — contains `./.mcp.json`, `./mcp/aimo_credentials.json`, `./.claude-plugin/plugin.json`, `SKILL.md`, and `./mcp/aimo_mcp_bridge.py` with your agent tokens **already pre-embedded**):
   - **Plugin / Skill mode (Claude Code, Cowork, or Claude Skills `.zip` upload):** Pass `claude --plugin-dir /path/to/unzipped-plugin`, install at `~/.claude/skills/aimoannos`, upload under **Claude Cowork -> Plugins**, or upload directly to **Claude Skills** (`mcp/aimo_mcp_bridge.py` and `mcp/aimo_credentials.json` carry your tokens in non-dotfiles so `python3 ./mcp/aimo_mcp_bridge.py --check`, `--call <tool> '<json>'`, and `--rest GET /api/health` work with zero env vars even when `.mcp.json` is not auto-spawned).
   - **CLI registration (`claude mcp add`, from inside the unzipped folder or with an absolute bridge path):**
     ```bash
     claude mcp add aimoannos -e AIMO_BASE_URL="{{AIMO_BASE_URL}}" -e AIMO_AGENT_ID="{{AIMO_AGENT_ID}}" -e AIMO_AGENT_SECRET="{{AIMO_AGENT_SECRET}}" -- python3 ./mcp/aimo_mcp_bridge.py
     ```
   - *(Inside a source repository checkout, the bridge path is `clients/mcp/aimo_mcp_bridge.py` instead of `./mcp/aimo_mcp_bridge.py`.)*
2. **If no local `aimo_mcp_bridge.py` script is on disk (No Download Required!):**
   - **Do NOT install packages or search for missing local files.** Connect immediately over plain HTTPS using **Section 1** (`POST {{AIMO_BASE_URL}}/api/agent-auth/token`) and either **Streamable HTTP MCP** (`POST {{AIMO_BASE_URL}}/api/mcp`) or **pure `curl`/Python REST** (**Section 2 Option B**).
3. **Pre-flight & capability check before ordering:**
   - **Authenticate before calling `/api/*` (`401 JUDGE_ACCESS_REQUIRED` guard):** Protected preview origins (e.g., `https://gateway.example.invalid`) sit behind a browser Judge Access Gate that allows password-free M2M pass-through **only** on `POST {{AIMO_BASE_URL}}/api/agent-auth/token`, `GET {{AIMO_BASE_URL}}/.well-known/agent-card.json` (or `{{AIMO_BASE_URL}}/api/.well-known/agent-card.json`), and `/api/*` requests carrying `Authorization: Bearer <token>`. **Every `/api/*` route — including `GET /api/health` and `GET /api/openapi.json` — requires `Authorization: Bearer <token>`.** If you see `401 JUDGE_ACCESS_REQUIRED`, **do NOT ask the user for a judge password** — exchange `TOKEN` via **Section 1** (`POST {{AIMO_BASE_URL}}/api/agent-auth/token`) first and attach `-H "Authorization: Bearer ${TOKEN}"`!
   - Run `python3 ./mcp/aimo_mcp_bridge.py --check --mcp-config ./.mcp.json` (if using the stdio bridge; prints `allow_price_negotiation` and `supported_matters`) or exchange `TOKEN` (**Section 1**) and run `curl -sS -H "Authorization: Bearer ${TOKEN}" "{{AIMO_BASE_URL}}/api/health"` (plus optional public `curl -sS "{{AIMO_BASE_URL}}/.well-known/agent-card.json"`), then inspect whether `allow_price_negotiation` is enabled for this specific company via MCP `initialize` (`capabilities.experimental.supportedMatters.preliminary_tendering.enabled`, an object keyed by matter ID) / live `negotiate_quote` description, `POST {{AIMO_BASE_URL}}/api/a2a` (`"session/connect"` -> `allow_price_negotiation` & `supported_matters[]` array item `preliminary_tendering.enabled`), or `GET {{AIMO_BASE_URL}}/api/account/negotiation-settings`:
     - **If enabled:** Explicitly tell the user **you can negotiate prices for larger quantities** (volume/bulk pricing enabled for their company in the Admin panel `/admin`).
     - **If disabled:** Do **not** claim bulk price negotiation is active; note that price negotiation for larger quantities is currently disabled for their company and can be enabled per company in the **Admin panel (`/admin`)**.

---

## 1. Credentials & Environment

Configure these values in your agent runtime or `.env` (never commit `AIMO_AGENT_SECRET` or live JWTs):

```bash
export AIMO_BASE_URL="{{AIMO_BASE_URL}}"
export AIMO_AGENT_ID="{{AIMO_AGENT_ID}}"
export AIMO_AGENT_SECRET="{{AIMO_AGENT_SECRET}}"
```

Exchange credentials for a short-lived 60-minute agent JWT (`token` — `POST /api/agent-auth/token` passes through the perimeter Judge Gate without any password or cookie, and the returned `TOKEN` unlocks `GET /api/health`, `GET /api/openapi.json`, `POST /api/mcp`, `POST /api/a2a`, and all `/api/*` routes):

```bash
TOKEN=$(curl -sS -X POST "${AIMO_BASE_URL}/api/agent-auth/token" \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"'"${AIMO_AGENT_ID}"'","agent_secret":"'"${AIMO_AGENT_SECRET}"'"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")

curl -sS -H "Authorization: Bearer ${TOKEN}" "${AIMO_BASE_URL}/api/health"
```

---

## 2. Choose Your Integration Path

### Option A — MCP (Claude Code, Claude Desktop, Cursor, Windsurf)

- **Remote Streamable HTTP (`2025-03-26`, Zero Local Files):** `POST {{AIMO_BASE_URL}}/api/mcp` with `Authorization: Bearer <token>`.
- **Local Stdio Bridge (`mcp/aimo_mcp_bridge.py` from unzipped plugin via `${CLAUDE_PLUGIN_ROOT}/mcp/aimo_mcp_bridge.py`, or `clients/mcp/aimo_mcp_bridge.py` in repo):** Zero-dependency Python 3 stdlib bridge that handles `POST /api/agent-auth/token` exchange, 60-second proactive refresh, and 401 retry:

```json
{
  "mcpServers": {
    "aimoannos": {
      "command": "python3",
      "args": ["${CLAUDE_PLUGIN_ROOT}/mcp/aimo_mcp_bridge.py"],
      "env": {
        "AIMO_BASE_URL": "{{AIMO_BASE_URL}}",
        "AIMO_AGENT_ID": "{{AIMO_AGENT_ID}}",
        "AIMO_AGENT_SECRET": "{{AIMO_AGENT_SECRET}}"
      }
    }
  }
}
```

Available MCP tools (`tools/list` / `tools/call`, 10 total):
`search_products`, `check_products`, `optimize_quote`, `negotiate_quote`, `create_quote`, `place_order`, `get_order`, `find_on_sale_products`, `list_promotions`, `plan_menu`.

### Option B — Pure REST / OpenAPI Fallback (`{{AIMO_BASE_URL}}/api/openapi.json`)

Every capability (including `GET {{AIMO_BASE_URL}}/api/health` and `GET {{AIMO_BASE_URL}}/api/openapi.json`) works over plain HTTPS JSON with `Authorization: Bearer <token>` (unauthenticated `/api/*` requests except `POST /api/agent-auth/token` and `GET /api/.well-known/agent-card.json` return `401 JUDGE_ACCESS_REQUIRED`):

1. **Authenticate:** `POST {{AIMO_BASE_URL}}/api/agent-auth/token` with `{"agent_id": "{{AIMO_AGENT_ID}}", "agent_secret": "{{AIMO_AGENT_SECRET}}"}` -> `token`.
2. **Search / Discover / Plan:**
   - `GET {{AIMO_BASE_URL}}/api/products?q=...&diet=L&limit=20` (seeded foodservice catalog)
   - `GET {{AIMO_BASE_URL}}/api/products/deals` (live on-sale items & company promotions)
   - `POST {{AIMO_BASE_URL}}/api/menu/plan` (`{ "pax": 60, "diet": "L", "requested_delivery_date": "YYYY-MM-DD" }`)
3. **Price, Availability, CO2 & Policy Preview:**
   - `POST {{AIMO_BASE_URL}}/api/quotes` with `{"requested_delivery_date": "YYYY-MM-DD", "lines": [{"product_id": "464306", "quantity": 10}], "diet": "L"}`
   - Optional substitute lookup on `PARTIAL` / `LATE` / `CUTOFF_PASSED`: `POST {{AIMO_BASE_URL}}/api/products/substitutes`
   - Optional company-gated price negotiation (`allow_price_negotiation`, enabled per company in `/admin`; readback `GET {{AIMO_BASE_URL}}/api/account/negotiation-settings`): `POST {{AIMO_BASE_URL}}/api/quotes/negotiate` with `lines` + optional `target_discount_pct` / `target_total_eur` / `justification` (runs diet/stock/cut-off checks first; returns `403 NEGOTIATION_DISABLED` if disabled or `{ status: "ACCEPTED"|"COUNTER_OFFER"|"FLOOR_REACHED", rounds, quote, savings_eur, savings_pct }` with a locked 30-min `quote` ready for `POST /api/orders`).
4. **Place & Track Order:**
   - `POST {{AIMO_BASE_URL}}/api/orders` with header `Idempotency-Key: ord_<quote_id>_1` and body `{"quote_id": "<quote.id>", "diet": "L"}`
   - Poll `GET {{AIMO_BASE_URL}}/api/orders/{order_id}` on `SUBMITTING`, `SUBMISSION_UNKNOWN`, or `PENDING_APPROVAL` until terminal (`PLACED` with `gateway_reference`, `FAILED`, or `CANCELLED`). Withdraw unapproved orders with `POST {{AIMO_BASE_URL}}/api/orders/{order_id}/cancel`.

### Option C — A2A Session Handshake (`POST {{AIMO_BASE_URL}}/api/a2a`, JSON-RPC 2.0)

Discover which matters this gateway can handle before sending procurement tasks or answering *"What are your capabilities?"*:

- **Agent Card Discovery (Public):** `GET {{AIMO_BASE_URL}}/.well-known/agent-card.json` (or `GET {{AIMO_BASE_URL}}/api/.well-known/agent-card.json`) exposes `supported_matters[]` (`menu_planning`, `order_execution`, `preliminary_tendering` [gated per company by `allow_price_negotiation`], `exception_handling`).
- **Authenticated Session Connect:** Call `POST {{AIMO_BASE_URL}}/api/a2a` (`Authorization: Bearer <token>`) with JSON-RPC method `"session/connect"` (or `"session/capabilities"`), returning `{ "session_greeting", "company_name", "allow_price_negotiation", "supported_matters", "protocol": "A2A/JSON-RPC-2.0" }` where `supported_matters[]` is an array on A2A whose `preliminary_tendering.enabled` reflects that specific company's live `allow_price_negotiation` setting (on MCP `initialize`, `capabilities.experimental.supportedMatters` is a record keyed by matter ID — `capabilities.experimental.supportedMatters.preliminary_tendering.enabled` — alongside `instructions`, live `negotiate_quote` description, `GET {{AIMO_BASE_URL}}/api/account/negotiation-settings`, and `"message/send"` capability replies). Report clearly: if enabled, state that you can negotiate prices for larger quantities; if disabled, do not claim it and note per-company activation in the **Admin panel (`/admin`)**.

---

## 3. Minimal LangGraph / Python `@tool` Drop-In (`httpx`)

```python
import os, httpx
from langchain_core.tools import tool

BASE = os.getenv("AIMO_BASE_URL", "{{AIMO_BASE_URL}}").rstrip("/")
AGENT_ID = os.getenv("AIMO_AGENT_ID", "{{AIMO_AGENT_ID}}")
AGENT_SECRET = os.getenv("AIMO_AGENT_SECRET", "{{AIMO_AGENT_SECRET}}")

def _headers() -> dict[str, str]:
    r = httpx.post(f"{BASE}/api/agent-auth/token", json={"agent_id": AGENT_ID, "agent_secret": AGENT_SECRET}, timeout=15)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['token']}"}

@tool
def aimo_search_products(query: str, diet: str = "") -> dict:
    """Search Valio Aimo catalog by keyword/SKU and optional Finnish diet code (L, VL, M, G, VEG, HEART)."""
    params = {"q": query, "limit": 10, **({"diet": diet} if diet else {})}
    return httpx.get(f"{BASE}/api/products", params=params, headers=_headers(), timeout=15).json()

@tool
def aimo_create_quote(lines: list[dict], requested_delivery_date: str, diet: str = "") -> dict:
    """Lock prices/stock for [{'product_id': str, 'quantity': float}] and get policy_preview + CO2."""
    body = {"requested_delivery_date": requested_delivery_date, "lines": lines, **({"diet": diet} if diet else {})}
    return httpx.post(f"{BASE}/api/quotes", json=body, headers=_headers(), timeout=20).json()

@tool
def aimo_place_order(quote_id: str, idempotency_key: str, diet: str = "") -> dict:
    """Submit an OPEN quote through Layer 1 policy and SHA-256 content_hash verification."""
    hdrs = {**_headers(), "Idempotency-Key": idempotency_key}
    body = {"quote_id": quote_id, **({"diet": diet} if diet else {})}
    return httpx.post(f"{BASE}/api/orders", json=body, headers=hdrs, timeout=20).json()
```

---

## 4. Mandatory Operating, Routing & Safety Guardrails

1. **Deterministic Routing (`X-Aimo-Route`):** Structured SKU and quote payloads (`product_id` + `quantity`, `quote_id`) go straight to **`core-api`** with zero LLM overhead (`X-Aimo-Route: core-api`). Free-text or agentic payloads (`prompt`, `prep_list`, `auto_substitute`, `/api/menu/plan`, `/api/assistant/*`) route via **`agentic-service -> core-api`** (inspectable at `POST /api/routing/inspect`). Every quote and order still funnels through `policy.py`, `content_hash`, and `audit_events`.
2. **Idempotency & Integrity (`content_hash`, `Idempotency-Key`):** Always send a stable `Idempotency-Key` per logical order attempt, inspect `SHA-256 content_hash` from the quote/order response, and never blind-retry `409` (`QUOTE_EXPIRED`, `QUOTE_STALE`, `QUOTE_ALREADY_USED`) — always re-run `create_quote` with a fresh `idempotency_key`.
3. **Finnish Diet & Allergen Safety:** Valid codes are `L` (lactose-free), `VL` (low-lactose), `M` (milk-free), `G` (gluten-free), `VEG` (vegan), `HEART` (Sydänmerkki). **Never substitute `VL` when `L` is required.** Treat `"CODE:MAY_CONTAIN"` and `"Unknown"` conservatively and always forward `diet` on `create_quote` and `place_order` (`403 POLICY_BLOCKED` / `R-HARD-DIET` cannot be bypassed).
4. **Recipe Quantity Sanity Check (`R-RECIPE-SANITY`):** Check pack sizes (`sales_unit`, `base_unit`, `units[].size_in_base_units`) and compare quantities against recipe yield × `target_pax`. Stop and surface any `R-RECIPE-SANITY` over-order warning rather than ordering unverified excess quantities.
5. **Human Approval Escalation (`PENDING_APPROVAL`):** When an order triggers `PENDING_APPROVAL` (`R-AUTO-LIMIT`, `R-DELIVERY-RISK`, `R-RECIPE-SANITY`, `R-STANDING-DRIFT`, `R-SEMANTIC-GUARD`, etc.), present `order.id`, `total_excl_vat_eur`, fired `policy.rules` (`rule_id` + `message`), `content_hash`, and any 5h availability reservation info (`reservation_status`, `reservation_expires_at`, `reservation_warning_message`) so a human can approve via **Web `/approvals`** or in 1 tap via **opt-in `@your_aimo_bot` Telegram cards** (cancelling via `POST /api/orders/{id}/cancel` releases holds cleanly with zero warnings). Agents cannot self-approve (`403 FORBIDDEN`). Poll `GET /api/orders/{order_id}` for `PLACED` (`gateway_reference`).
6. **Zero Order Spam / Policy Probing (`403` Auto-Quarantine):** Never spam duplicate orders (same items/`content_hash` across fresh `Idempotency-Key`s) or attempt prompt-injection policy bypasses. Anomalous bursts or probes immediately quarantine the agent (`403 AGENT_QUARANTINED` / `AGENT_DISABLED` on active JWTs) pending mandatory human review.
