# AimoAnnos MCP & REST Reference

## Authentication (Agents)

Exchange `agent_id` + `agent_secret` for a 60-minute JWT (`aud=aimoannos-api`):

```http
POST /api/agent-auth/token
Content-Type: application/json

{"agent_id": "agt_...", "agent_secret": "aks_..."}
```

Response: `{ "token": "<jwt>", "expires_at": "...", "agent": { ... } }`.
Pass `Authorization: Bearer <token>` on `/api/mcp` and **all** REST `/api/*` requests — including `GET /api/health` and `GET /api/openapi.json`. On protected preview origins (`https://gateway.example.invalid` / `:8000`), only `POST /api/agent-auth/token` and `GET /.well-known/agent-card.json` (`/api/.well-known/agent-card.json`) are open without a browser cookie; unauthenticated `/api/*` calls return `401 JUDGE_ACCESS_REQUIRED` (no judge password is needed once `Authorization: Bearer <token>` is attached). Downloaded `aimoannos-claude-plugin-*.zip` bundles embed your `AIMO_BASE_URL`, `AIMO_AGENT_ID`, `AIMO_AGENT_SECRET`, and pre-minted `AIMO_BEARER_TOKEN` directly inside `mcp/aimo_mcp_bridge.py`, `mcp/aimo_credentials.json`, and `.mcp.json` (`python3 mcp/aimo_mcp_bridge.py --check | --call <tool> '<json>' | --rest <METHOD> <path> ['<json>']` authenticates and refreshes tokens automatically).

## MCP Tools (`POST /api/mcp`, protocol `2025-03-26`)

| Tool | Required args | Optional args | Notes |
|---|---|---|---|
| `search_products` | — | `q`, `category`, `allergen_free`, `label`, `diet`, `limit` (1..100), `offset` | Local DB search only; no upstream API call |
| `check_products` | `product_ids` (1..50), `requested_delivery_date` (`YYYY-MM-DD`) | `quantities` (`{product_id: number}`) | Live price, availability, delivery risk, CO2 basis |
| `create_quote` | `requested_delivery_date`, `lines` (`[{product_id, quantity}]`, 1..100) | `diet` (`L,VL,M,G,VEG,HEART`) | Locks quote for min(30 min, cut-off); includes `policy_preview` |
| `place_order` | `quote_id` | `note`, `diet`, `idempotency_key` | Evaluates Layer 1 policy and submits if `AUTO` |
| `get_order` | `order_id` | — | Poll `SUBMITTING` / `SUBMISSION_UNKNOWN` or inspect status |
| `find_on_sale_products` | — | `q`, `category`, `diet`, `valio_only`, `on_sale_only`, `requested_delivery_date`, `limit` (1..50) | Discovers `listPrice > catalogPrice` + promoted items |
| `list_promotions` | — | `active_only` (default `true`) | Read-only active company promotions |
| `plan_menu` | `pax` (1..5000) | `meal_type` (`breakfast`\|`lunch`\|`dinner`), `requested_delivery_date`, `diet`, `prefer_on_sale`, `include_valio_recipes`, `max_budget_eur` | Nordic menu plan + quote draft lines + `policy_preview` |
| `optimize_quote` | `requested_delivery_date` (`YYYY-MM-DD`), `lines` (`[{product_id, quantity}]`, 1..25) | `diet`, `objective` (`BALANCED`\|`SAVE_MONEY`\|`MAX_RELIABILITY`\|`LOW_CARBON`, default `BALANCED`), `allow_plant_based_swaps` (default `false`) | Previews diet-safe, unit-normalized swaps before locking a quote (`<=50` live check IDs) |
| `negotiate_quote` | `requested_delivery_date` (`YYYY-MM-DD`), `lines` (`[{product_id, quantity}]`, 1..25) | `diet`, `target_discount_pct` (`0..99`), `target_total_eur` (`>0`), `justification`, `recipe_id`, `target_pax` (`1..50000`) | Company-gated (`allow_price_negotiation` in `/admin`) preliminary A2A volume/bundle negotiation after diet/stock/cut-off checks; returns `QuoteNegotiateResponse` + locked 30-min `quote` |

Note: `initialize` on `POST /api/mcp` returns `instructions` and `capabilities.experimental.supportedMatters` as an MCP-compliant object keyed by matter ID (`{ menu_planning: { enabled: true, ... }, order_execution: { enabled: true, ... }, preliminary_tendering: { enabled: <allow_price_negotiation>, ... }, exception_handling: { enabled: true, ... } }`), and `tools/list` reflects the authenticated company's live `allow_price_negotiation` status in `negotiate_quote`. (By contrast, A2A `/.well-known/agent-card.json` and `POST /api/a2a` `session/connect` keep `supported_matters[]` as an array.) During **pre-flight** or when asked **"What are your capabilities?"**, explicitly tell the user you can negotiate prices for larger quantities if enabled for their company (or note it is disabled and managed per company in `/admin`).

## A2A Session Handshake & Capabilities (`/.well-known/agent-card.json`, `POST /api/a2a`)

- **Public Agent Card (`GET /.well-known/agent-card.json`, alias `GET /api/.well-known/agent-card.json`):** Returns the agent card including `supported_matters[]` (`{ id, title, summary, enabled, tools_or_skills[] }`) for `menu_planning`, `order_execution`, `preliminary_tendering` (gated per company by `allow_price_negotiation`), and `exception_handling`.
- **Authenticated Session Connect (`POST /api/a2a`, `Authorization: Bearer <token>`, JSON-RPC 2.0):** Call method `"session/connect"` or `"session/capabilities"` to receive `{ session_greeting, company_name, supported_matters, protocol: "A2A/JSON-RPC-2.0" }`, where `preliminary_tendering.enabled` dynamically reflects that company's `allow_price_negotiation` setting (configured per company in `/admin`; also readable via `GET /api/account/negotiation-settings`). Capability questions on `"message/send"` and task `metadata.supported_matters` expose the same supported matters.

## Useful REST Fallbacks (Bearer Agent JWT)

| Method & Path | Purpose | Request / Notes |
|---|---|---|
| `GET /api/account/negotiation-settings` | Check whether bulk price negotiation is enabled for this company | Returns `{ company_id, allow_price_negotiation, max_auto_discount_pct, updated_at }` (admins toggle per company via `GET/PUT /api/admin/company-negotiations/{company_id}` in `/admin`; `403` for agents) |
| `GET /api/products/{id}` | Full product unit conversions, labels, allergens, `non_allergens` | Verify `units[]` and `allergens` before ordering |
| `POST /api/products/substitutes` | Diet-safe `ON_TIME` alternatives (`tier`: `BEST`\|`BETTER`\|`GOOD`) | `{ "product_id": "469363", "quantity": 1000, "requested_delivery_date": "YYYY-MM-DD", "diet": "L" }` |
| `POST /api/quotes/optimize` | Pre-flight cart swap preview (`BALANCED`, `SAVE_MONEY`, `MAX_RELIABILITY`, `LOW_CARBON`) | `{ "requested_delivery_date": "YYYY-MM-DD", "lines": [...], "objective": "BALANCED" }` |
| `POST /api/quotes/negotiate` | Company-gated preliminary price negotiation (`allow_price_negotiation`) | `{ "requested_delivery_date": "YYYY-MM-DD", "lines": [...], "target_discount_pct": 5, "justification": "..." }` -> `{ status, rounds, quote, savings_eur, savings_pct }` |
| `POST /api/orders/{id}/cancel` | Cancel an agent's own `PENDING_APPROVAL` order | `{ "note": "..." }` |
| `GET /api/orders/{id}/trace` | Full reasoning & audit chain trace for an order | Company- and agent-isolated |
| `GET /api/health` | Check `server_time`, `aimo_mode` (`live`\|`fixture`), and `aimo` status | Pass `Authorization: Bearer <token>` behind Judge Gate (`401 JUDGE_ACCESS_REQUIRED` if omitted); use `server_time` / `X-Server-Time` for cut-offs |

## Layer 1 Policy Rule IDs

| `rule_id` | Outcome | Trigger |
|---|---|---|
| `R-HARD-DIET` | `BLOCKED` | Product violates requested `diet` or agent hard diet constraint |
| `R-NOT-DELIVERABLE` | `BLOCKED` | `NOT_DELIVERABLE`, `NO_PRICE`, or `SALES_BLOCKED` |
| `R-AGENT-NO-ORDER` | `BLOCKED` | Agent `can_place_orders` is `false` |
| `R-MAX-VALUE` | `BLOCKED` | `total_excl_vat_eur > max_order_value_eur` |
| `R-AUTO-LIMIT` | `APPROVAL_REQUIRED` | `total_excl_vat_eur > auto_approve_limit_eur` |
| `R-DAILY-SPEND` | `APPROVAL_REQUIRED` | `spent_today_eur + total_excl_vat_eur > daily_spend_limit_eur` |
| `R-CATEGORY` | `APPROVAL_REQUIRED` | Product category outside `allowed_categories` prefixes |
| `R-LINE-QTY` | `APPROVAL_REQUIRED` | Line `quantity > max_quantity_per_line` |
| `R-DELIVERY-RISK` | `APPROVAL_REQUIRED` | `delivery_status` in `("LATE", "AT_RISK")` or risk flag (`CUTOFF_PASSED`, `LATE`, `PARTIAL`, `UNIT_MISMATCH`) |
| `R-RECIPE-SANITY` | `APPROVAL_REQUIRED` | Recipe yield over-ordering sanity check (`> 1.5x` target pax yield) |
| `R-STANDING-DRIFT` | `APPROVAL_REQUIRED` | Standing-order drift (`> 25%` portion/cost creep vs `>= 2` prior-day `PLACED` orders) |
| `R-SEMANTIC-GUARD` | `APPROVAL_REQUIRED` | Semantic governance firewall flagged untrusted/coercive instructions |
| `R-AGENT-WITHIN-LIMITS` | `AUTO` | All checks pass; submitted immediately to order gateway |

## `PENDING_APPROVAL` 5h Soft Availability Hold (`OrderOut`)

- `reservation_status`: `"HELD_5H"` (finite stock reserved up to `min(5h, approval_expires_at)`), `"HOLD_EXPIRED"`, `"UNRESERVED_DUE_TO_WARNINGS"`, `"NONE"`, or `null`.
- `reservation_expires_at`, `company_warning_count_7d`, `reservation_warning_message`: clean `cancel` or reject releases holds with `0` warnings; undecided expiries increment warnings (`>= 3` in 7 days suspends holds).

## Common Error Codes (`error.code`)

- `401 JUDGE_ACCESS_REQUIRED` — Unauthenticated `/api/*` call (such as `GET /api/health` or `GET /api/openapi.json` without `Authorization: Bearer <token>`) hit the perimeter Judge Gate. **Never ask the human for a judge password**; exchange `agent_id` + `agent_secret` via `POST /api/agent-auth/token` (open to M2M without a cookie) and pass `Authorization: Bearer <token>`.
- `401 UNAUTHENTICATED` — Expired/invalid token or agent credentials (stdio bridge refreshes automatically).
- `403 AGENT_DISABLED` — Agent is disabled or past `expires_at`.
- `403 NEGOTIATION_DISABLED` — Company-level `allow_price_negotiation` is `false` (human admin only via the **Admin panel (`/admin`)**, backed by `GET/PUT /api/admin/company-negotiations/{company_id}`; never claim bulk price negotiation is active when disabled, and fall back to `create_quote`).
- `403 POLICY_BLOCKED` — Hard policy rule (`R-HARD-DIET`, `R-MAX-VALUE`, `R-NOT-DELIVERABLE`, `R-AGENT-NO-ORDER`); inspect `error.details`.
- `409 QUOTE_EXPIRED` / `QUOTE_STALE` / `QUOTE_ALREADY_USED` — Re-create quote with `create_quote`.
- `409 CONFLICT` — Same `idempotency_key` reused with a different request body.
- `503 UPSTREAM_UNAVAILABLE` — Valio Aimo API unreachable after retries.
