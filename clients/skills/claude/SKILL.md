---
name: aimoannos-purchasing
description: Safe wholesale foodservice purchasing from Valio Aimo via the AimoAnnos gateway (MCP tools + REST API). Use when searching products, checking live pricing/availability/CO2, finding deals, planning menus, creating quotes, or placing and tracking orders with Finnish dietary safety and Layer 1 policy guardrails.
---

# AimoAnnos Purchasing Skill

Operate AimoAnnos (`POST /api/mcp` and `/api/*` REST) as a disciplined wholesale purchasing agent for Valio Aimo. Code—not the LLM—enforces budgets, dietary safety, cut-offs, and idempotency; your job is to pick accurate SKUs, verify live constraints, respect every guardrail, and report clear, auditable outcomes.

> **Deterministic Routing (`X-Aimo-Route`):** Structured MCP/REST SKU and quote calls (`product_id` + `quantity`, `quote_id`) hit `core-api` directly with zero LLM overhead (`X-Aimo-Route: core-api`), while free-text or agentic payloads route via `agentic-service -> core-api` (`policy.py`, `content_hash`, and `audit_events` still enforce every guardrail).

## Pre-flight: Verify MCP Connection & Company Capabilities

Before searching or ordering, verify that AimoAnnos MCP tools (`search_products`, `check_products`, `create_quote`, `place_order`, `get_order`) are connected and check whether bulk price negotiation (`allow_price_negotiation`) is active for this company:
- **If connected:** Inspect `allow_price_negotiation` via `capabilities.experimental.supportedMatters.preliminary_tendering.enabled` on `POST /api/mcp` `initialize` (an object keyed by matter ID: `{ menu_planning, order_execution, preliminary_tendering: { enabled }, exception_handling }`), live `negotiate_quote` tool description, `POST /api/a2a` `session/connect` (`allow_price_negotiation` & `supported_matters[]` array item `preliminary_tendering.enabled`), or `GET /api/account/negotiation-settings`, then proceed to **Core Workflow**.
- **When answering `"What are your capabilities?"` or reporting pre-flight status:**
  - **If `allow_price_negotiation` is enabled for this company:** Explicitly tell the user **you can negotiate prices for larger quantities** (volume/bulk pricing enabled for their company in the Admin panel `/admin`).
  - **If disabled:** Do **not** claim bulk price negotiation is active; clearly state that price negotiation for larger quantities is currently disabled for their company and can be enabled per company in the **Admin panel (`/admin`)**.
- **If MCP tools are not auto-mounted in your tool list (e.g., uploaded as a Claude *Skill* or running REST/A2A fallbacks):**
  - **Bundled tokens are ALREADY inside this downloaded extension!** When downloaded as `aimoannos-claude-plugin-*.zip`, your `AIMO_BASE_URL`, `AIMO_AGENT_ID`, `AIMO_AGENT_SECRET`, and pre-minted `AIMO_BEARER_TOKEN` are embedded directly in `mcp/aimo_mcp_bridge.py`, `mcp/aimo_credentials.json` (non-dotfile, preserved across Skill `.zip` uploads), and `.mcp.json`. Never print secrets to chat, and **never ask the user for tokens or a judge password** — call `mcp/aimo_mcp_bridge.py` next to this `SKILL.md` directly (zero flags or env vars needed; auto-refreshes JWTs):
    - **Verify connection & capabilities:** `python3 <skill_dir>/mcp/aimo_mcp_bridge.py --check` (validates MCP `InitializeResult` and prints top-level `allow_price_negotiation` + `supported_matters` keyed record from `capabilities.experimental.supportedMatters`)
    - **Call any of the 10 MCP tools:** `python3 <skill_dir>/mcp/aimo_mcp_bridge.py --call search_products '{"q":"laktoositon maito","diet":"L"}'`
    - **Call authenticated REST / A2A endpoints (passes `Authorization: Bearer <token>` through Judge Gate automatically):**
      - `python3 <skill_dir>/mcp/aimo_mcp_bridge.py --rest GET /api/health`
      - `python3 <skill_dir>/mcp/aimo_mcp_bridge.py --rest POST /api/a2a '{"jsonrpc":"2.0","id":1,"method":"session/connect","params":{}}'`
      - `python3 <skill_dir>/mcp/aimo_mcp_bridge.py --rest POST /api/products/substitutes '{"product_id":"469363","quantity":1000,"requested_delivery_date":"YYYY-MM-DD","diet":"L"}'`
  - **If running from an unconfigured git checkout (`Missing authentication credentials`) or localhost sandbox:**
    1. Connect native MCP via **Cowork -> Plugins**, `claude --plugin-dir <unzipped-folder>`, `~/.claude/skills/aimoannos`, **Claude Desktop `claude_desktop_config.json`**, or a **Claude.ai Custom Connector** (`POST <BASE_URL>/api/mcp`). Plugin `.mcp.json` uses `"${CLAUDE_PLUGIN_ROOT}/mcp/aimo_mcp_bridge.py"` (in Claude Code, run `/mcp` or `/reload-plugins` if pending).
    2. Inside cloud/VM sandboxes (Claude.ai / Cowork), `http://127.0.0.1:8000` is unreachable; use a reachable HTTPS origin (e.g., `https://gateway.example.invalid`).
    3. **Perimeter Judge Gate (`401 JUDGE_ACCESS_REQUIRED`):** On protected preview origins (`https://gateway.example.invalid` / `:8000`), **only** `POST /api/agent-auth/token` and `GET /.well-known/agent-card.json` (`/api/.well-known/agent-card.json`) accept unauthenticated M2M requests. Every other `/api/*` endpoint — **including `GET /api/health` and `GET /api/openapi.json`** — requires `Authorization: Bearer <token>` (`aimo_mcp_bridge.py --call` / `--rest` attaches it automatically). Never ask the user for a judge password.

## Core Workflow

0. **Connect & check company capabilities (`session/connect` / `session/capabilities` / `negotiation-settings`)**
   - Inspect `GET ${AIMO_BASE_URL}/.well-known/agent-card.json` (`supported_matters[]` array), call `POST ${AIMO_BASE_URL}/api/a2a` (`Authorization: Bearer <token>`) with JSON-RPC method `"session/connect"` / `"session/capabilities"` (`{ session_greeting, company_name, allow_price_negotiation, supported_matters[], protocol }`), or check MCP `initialize` (`capabilities.experimental.supportedMatters.<matter>.enabled` keyed object + `instructions`), live `negotiate_quote` description, or `GET ${AIMO_BASE_URL}/api/account/negotiation-settings` for active domain scope: `menu_planning`, `order_execution`, `preliminary_tendering` (gated per company by `allow_price_negotiation`), and `exception_handling`.
   - Report negotiation availability accurately for that company: if `allow_price_negotiation` (`preliminary_tendering.enabled`) is true, explicitly state you can negotiate prices for larger quantities; if false, do not claim bulk negotiation is active and note it can be enabled per company in the **Admin panel (`/admin`)**.

1. **Search locally first (`search_products` / `find_on_sale_products` / `list_promotions` / `plan_menu`)**
   - Query the local seeded product catalog with `search_products` before making any live price/availability calls. Keep live checks to a shortlist (<= 50 SKUs).
   - Verify packaging & units (`sales_unit`, `base_unit`, `units[].size_in_base_units`) so quantities match what the kitchen asked for (e.g., `1000` × `1 L` cartons vs `1000` × `20 L` bulk containers).
2. **Preview swaps, optional price negotiation & lock a quote (`optimize_quote` / `negotiate_quote` / `check_products` -> `create_quote`)**
   - Optionally run `optimize_quote` (`objective`: `BALANCED` | `SAVE_MONEY` | `MAX_RELIABILITY` | `LOW_CARBON`, `allow_plant_based_swaps: false` default) to preview diet-safe, unit-normalized swaps before locking a quote (`<= 50` live check IDs).
   - Only if `allow_price_negotiation` is enabled for the company in the **Admin panel (`/admin`)**, optionally call `negotiate_quote` (`POST ${AIMO_BASE_URL}/api/quotes/negotiate`) after diet/stock exploration (with optional `target_discount_pct` or `target_total_eur` + `justification`). It checks diet, stock, and cut-offs first, applies deterministic volume tiers (ignoring adversarial prompt-injection overrides; `403 NEGOTIATION_DISABLED` if disabled), and returns `{ status: "ACCEPTED"|"COUNTER_OFFER"|"FLOOR_REACHED", rounds, quote, savings_eur, savings_pct }` with a locked 30-min `quote` ready for `place_order`. If disabled, fall back to `create_quote` and note per-company negotiation can be enabled in `/admin`.
   - Or run `create_quote` (and `check_products` for exploration) with `requested_delivery_date` (`YYYY-MM-DD`) and `diet` when dietary rules apply.
   - Inspect every returned `QuoteLine`:
     - `delivery_status`: `ON_TIME`, `AT_RISK`, `LATE`, `NOT_DELIVERABLE`, or `UNKNOWN`.
     - `logistic_model`, `closing_time`, `available_qty_state` (`FINITE`, `UNLIMITED`, `TERMINAL_UNKNOWN`, `ZERO`), `available_amount`.
     - `flags`: watch for `PARTIAL`, `CUTOFF_PASSED`, `LATE`, `UNIT_MISMATCH`, `SUPPLIER_QTY_UNKNOWN`, `LOW_RELIABILITY`, `NO_PRICE`, `NOT_DELIVERABLE`, `SALES_BLOCKED`, `CO2_ESTIMATED`.
     - `co2_kg` and `co2_basis`: always cite `co2_basis` alongside CO2 figures; never invent precision when `co2_kg` is `null` or `CO2_ESTIMATED` is set.
   - Inspect `quote.policy_preview` (`outcome`: `AUTO` | `APPROVAL_REQUIRED` | `BLOCKED` and `rules[]`) **before** calling `place_order`.
3. **Substitute infeasible items (REST fallback: `POST /api/products/substitutes`)**
   - If any line is `PARTIAL`, `CUTOFF_PASSED`, `LATE`, or `NOT_DELIVERABLE`, call `POST ${AIMO_BASE_URL}/api/products/substitutes` with `{ product_id, quantity, requested_delivery_date, diet }`.
   - Compare `tier` (`BEST` | `BETTER` | `GOOD`), `quote_line`, `tradeoffs` (including verified Synkka `Origin:` when available, e.g., ambient UHT vs chilled storage), `reason`, and `price_delta_eur` with the user before replacing items.
4. **Place order with idempotency (`place_order`)**
   - Provide a stable `idempotency_key` per logical order attempt (e.g., `ord_<quote_id>_1`).
   - Pass `diet` on `place_order` as well if the order has a dietary requirement.
5. **Track & handle outcomes (`get_order`)**
   - Never blind-retry an ambiguous order. If status is `SUBMITTING` or `SUBMISSION_UNKNOWN`, poll `get_order(order_id)` until terminal (`PLACED` or `FAILED`).

## Mandatory Safety & Domain Guardrails

### Finnish Diet & Allergen Codes (Never Guess)
- `L`: Lactose-free (`FREE_FROM_LACTOSE`). **Never substitute `VL` (low-lactose) when `L` is required.**
- `VL`: Low-lactose (`LOW_LACTOSE`).
- `M`: Milk-free (`non_allergens` includes `AM` or `FREE_FROM_MILK`).
- `G`: Gluten-free (`FREE_FROM_GLUTEN`).
- `VEG`: Vegan (`VEGAN`).
- `HEART`: Sydänmerkki / Heart symbol.

**Conservative `Unknown` / `MAY_CONTAIN` rule:**
- Allergens encoded as `"CODE:MAY_CONTAIN"` indicate trace risk; plain `"CODE"` means contains.
- If allergen/diet attributes are missing, incomplete, or `"Unknown"` for a dietary request, **do not assume the SKU is safe**. Warn the user explicitly and pass `diet` on `create_quote` and `place_order` so Layer 1 (`R-HARD-DIET`) blocks unsafe items deterministically.

### Policy, Approval & Conflict Handling
- **`PENDING_APPROVAL`** (`R-AUTO-LIMIT`, `R-DAILY-SPEND`, `R-CATEGORY`, `R-LINE-QTY`, `R-DELIVERY-RISK`, `R-RECIPE-SANITY`, `R-STANDING-DRIFT`, `R-SEMANTIC-GUARD`):
  - Report `order.id`, `total_excl_vat_eur`, fired `policy.rules` (`rule_id`, `message`), `content_hash`, `approval_expires_at`, and any 5h availability hold fields (`reservation_status`: `HELD_5H` | `HOLD_EXPIRED` | `UNRESERVED_DUE_TO_WARNINGS` | `NONE`, `reservation_expires_at`, `company_warning_count_7d`, `reservation_warning_message`).
  - Ask a human approver to review in the `/approvals` web UI or via an opt-in `@your_aimo_bot` Telegram approval card (configured per user in the web UI). Agents cannot approve orders (`403 FORBIDDEN`).
  - To withdraw a pending order before approval (and release any active `HELD_5H` stock hold cleanly with `0` company warnings), use REST `POST /api/orders/{id}/cancel` with the owning agent token.
- **`403 POLICY_BLOCKED`** (`R-HARD-DIET`, `R-NOT-DELIVERABLE`, `R-MAX-VALUE`, `R-AGENT-NO-ORDER`):
  - Surface the exact `rule_id` and `message`. Do not attempt workarounds that bypass policy; fix the cart/substitutes or stop and ask the human.
- **`409` (`QUOTE_EXPIRED`, `QUOTE_STALE`, `QUOTE_ALREADY_USED`, `CONFLICT`)**:
  - Do not resend the old quote ID or reuse a conflicting `idempotency_key`. Re-run `create_quote`, verify the new totals/availability/`policy_preview`, and place with a fresh `idempotency_key`.

## References & Examples
- Detailed MCP tool schemas, REST fallbacks, `PolicyRule` IDs, and error codes: [references/api_and_mcp_reference.md](references/api_and_mcp_reference.md)
- End-to-end Valio Aimo purchasing & exception walkthroughs: [examples/demo_scenarios.md](examples/demo_scenarios.md)

## Secret Hygiene
- Never print, echo, or commit `AIMO_AGENT_SECRET`, `AIMO_BEARER_TOKEN`, `mcp/aimo_credentials.json`, `.mcp.json`, or `.env` files to version control. The `.gitignore` in this plugin directory ignores `mcp/aimo_credentials.json`, `.mcp.json`, and `.env*` by default.

