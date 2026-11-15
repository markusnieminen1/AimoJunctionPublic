# AimoAnnos External Agent Integrations (`clients/`)

This directory contains everything needed to connect external AI agents, IDEs, and custom automation workflows to the **AimoAnnos** B2B foodservice purchasing gateway (`POST /api/mcp`, `POST /api/a2a`, and `/api/*` REST).

Every external integration routes through deterministic **Layer 1 policy guardrails**—spend caps, Finnish dietary/allergen safety (`L`, `VL`, `M`, `G`, `VEG`, `HEART`), terminal cut-off checks, `SHA-256` quote-to-order integrity (`content_hash`), and human-in-the-loop approval escalation—enforced in backend code rather than by the LLM.

---

## Directory Layout

```text
clients/
├── mcp/
│   ├── aimo_mcp_bridge.py         # Zero-dependency Python 3.11+ stdio-to-HTTP MCP bridge & CLI
│   ├── live_mcp_showcase.py       # End-to-end 4-act external MCP buyer showcase runner
│   ├── configs/                   # Ready-to-use client templates
│   │   ├── claude_desktop_config.json
│   │   ├── cursor_mcp.json
│   │   ├── mcp.stdio.json
│   │   └── mcp.http.json
│   ├── test_aimo_mcp_bridge.py    # Offline unit tests for auth, MCP schemas & security guards
│   ├── test_live_mcp_showcase.py  # Offline end-to-end subprocess stdio showcase tests
│   ├── pyproject.toml
│   └── README.md                  # Detailed MCP bridge CLI & transport reference
└── skills/
    ├── UNIVERSAL_AGENT_PROMPT.md  # Framework-agnostic system prompt (LangGraph, OpenAI, REST, MCP)
    └── claude/                    # Self-contained Claude Plugin / Skill package source
        ├── .claude-plugin/plugin.json
        ├── .mcp.json.template
        ├── SKILL.md
        ├── examples/demo_scenarios.md
        └── references/api_and_mcp_reference.md
```

---

## Overview of External Agent Integrations

### 1. Universal MCP `2025-03-26` Bridge (`clients/mcp/aimo_mcp_bridge.py`)
- **Zero third-party dependencies:** Built solely on the Python `>=3.11` standard library (`urllib.request`, `json`, `ssl`, `argparse`), so any developer machine or sandboxed agent container with `python3` can run it without `pip install` or `uv`.
- **Stdio-to-Streamable-HTTP translation:** Reads newline-delimited JSON-RPC 2.0 MCP messages from `stdin` and forwards them to `POST ${AIMO_BASE_URL}/api/mcp` with `MCP-Protocol-Version: 2025-03-26` and `Authorization: Bearer <jwt>`.
- **Built-in CLI modes (`--check`, `--call`, `--rest`):** Doubles as a standalone CLI helper for health/capability checks, one-shot MCP tool calls, and authenticated same-origin REST/A2A requests when an agent runtime executes shell commands instead of mounting stdio MCP servers.

### 2. Claude Desktop / Cowork / Skills 1-Click `.zip` Plugin Architecture
In the AimoAnnos web UI (**Agents** page), operators can download a ready-to-run `aimoannos-claude-plugin-<agent_id>.zip` bundle right after creating or rotating an agent credential:
- **100% client-side in-memory bundling:** `frontend/src/lib/agentBundle.ts` imports `clients/mcp/aimo_mcp_bridge.py` and `clients/skills/...` via Vite `?raw` imports and substitutes credential placeholders strictly in browser memory—agent secrets are never stored in web storage or sent back to the server.
- **Triple-redundant credential discovery:** Inside the generated `.zip`, credentials and a pre-minted 60-minute JWT are written to:
  1. `.mcp.json` (`mcpServers.aimoannos.env`, used when Claude Code or Cowork spawns the MCP server automatically),
  2. `mcp/aimo_credentials.json` (a non-dotfile preserved when uploading `.zip` archives to Claude Skills sandboxes that strip dotfiles), and
  3. `BUNDLED_*` sentinel constants (`BUNDLED_BASE_URL`, `BUNDLED_AGENT_ID`, `BUNDLED_AGENT_SECRET`, `BUNDLED_BEARER_TOKEN`, `BUNDLED_TOKEN_EXPIRES_AT`) inside `mcp/aimo_mcp_bridge.py` (inert `__AIMO_BUNDLED_*__` strings in git).

### 3. Cursor, Claude Code & Generic MCP Client Configs (`clients/mcp/configs/`)
Pre-built JSON templates let you connect any MCP-compatible IDE or desktop client either via the local Python stdio bridge (recommended—handles automatic token exchange and refresh) or directly to `POST /api/mcp` over Streamable HTTP:
- [`mcp/configs/claude_desktop_config.json`](mcp/configs/claude_desktop_config.json) — Claude Desktop stdio setup
- [`mcp/configs/cursor_mcp.json`](mcp/configs/cursor_mcp.json) — Cursor `.cursor/mcp.json` (both stdio and direct HTTP examples)
- [`mcp/configs/mcp.stdio.json`](mcp/configs/mcp.stdio.json) — Claude Code / generic `.mcp.json` stdio config
- [`mcp/configs/mcp.http.json`](mcp/configs/mcp.http.json) — Direct Streamable HTTP config (`POST /api/mcp` with `Bearer` JWT)

### 4. Reusable Agent Skills & Universal Prompt (`clients/skills/`)
- **[`skills/UNIVERSAL_AGENT_PROMPT.md`](skills/UNIVERSAL_AGENT_PROMPT.md):** Drop-in capability prompt for LangGraph, CrewAI, OpenAI/Gemini tool-calling agents, or custom Python/`curl` workflows (`{{AIMO_BASE_URL}}`, `{{AIMO_AGENT_ID}}`, `{{AIMO_AGENT_SECRET}}` placeholders). Includes a minimal `langchain_core.tools` + `httpx` snippet.
- **[`skills/claude/`](skills/claude/):** Structured Claude plugin & skill (`SKILL.md`, `references/api_and_mcp_reference.md`, `examples/demo_scenarios.md`) teaching agents Finnish allergen/diet codes (`L` vs `VL` safety), unit/pack-size verification, quote optimization/negotiation rules, and human approval escalation.

---

## Authentication & Security Architecture

1. **Short-Lived Bearer JWT Exchange (`agent_id` + `aks_...` -> 60-Minute JWT)**
   - Agents authenticate with a scoped `agent_id` (`agt_...`) and one-time secret (`aks_...`) at `POST /api/agent-auth/token`, receiving a 60-minute JWT (`aud=aimoannos-api`).
   - `TokenManager` in `aimo_mcp_bridge.py` caches the JWT in memory, **proactively refreshes** it within `60` seconds of expiry (using `expires_at` or the JWT `exp` claim), and **reactively re-exchanges & retries once** if an upstream call returns HTTP `401` (including behind optional perimeter preview gates returning `401 JUDGE_ACCESS_REQUIRED`).

2. **Zero-Permission Default Mandates & Deterministic Policy Gate**
   - Newly provisioned agents start with **least-privilege / zero-order defaults** until an operator explicitly grants mandates (`can_place_orders`, `auto_approve_limit_eur`, `max_order_value_eur`, `daily_spend_limit_eur`, `allowed_categories`, hard `diet` constraints).
   - Every quote and order is hashed (`SHA-256 content_hash`) and evaluated by Layer 1 `policy.py`. Out-of-policy orders either block deterministically (`403 POLICY_BLOCKED`, e.g. `R-HARD-DIET`, `R-MAX-VALUE`, `R-AGENT-NO-ORDER`) or enter `PENDING_APPROVAL` (`R-AUTO-LIMIT`, `R-DELIVERY-RISK`, `R-RECIPE-SANITY`, etc.) for human review in `/approvals` or via opt-in Telegram approval cards (e.g. `@your_aimo_bot` linked by an approver such as `anna.korhonen@hotel.fi`). Agents cannot self-approve (`403 FORBIDDEN`), and anomalous order spam or policy-bypass probing triggers immediate `403 AGENT_QUARANTINED`.

3. **Same-Origin `--rest` Credential Exfiltration Protection**
   - When an agent invokes `aimo_mcp_bridge.py --rest <METHOD> <PATH>`, `call_rest_one_shot()` parses and validates the target URL against `AIMO_BASE_URL`.
   - Any cross-origin host, port mismatch, HTTPS-to-HTTP scheme downgrade, protocol-relative path (`//evil.invalid/...`, `/\evil.invalid/...`), control character, or embedded userinfo (`https://user@gateway.example.invalid/...`) raises `BridgeError` before any network call is made—preventing prompt-injected instructions from exfiltrating the `Authorization: Bearer` token to a third-party server.

4. **Strict MCP `InitializeResult` `supportedMatters` Normalization & `ping` Keepalive**
   - Strict MCP clients such as Claude Desktop validate `InitializeResult` schemas rigorously. `normalize_mcp_initialize_result()` guarantees `capabilities.experimental.supportedMatters` is always formatted as a keyed object record (`{ menu_planning, order_execution, preliminary_tendering: { enabled: bool }, exception_handling }`) and strips non-standard top-level keys.
   - Agents inspect `preliminary_tendering.enabled` (`allow_price_negotiation`, toggled per company by human admins in `/admin`) during pre-flight to know whether bulk price negotiation (`negotiate_quote`) is permitted for that company.
   - If a client sends JSON-RPC `ping` keepalives and an upstream endpoint returns `-32601 Method not found`, the bridge transparently replies `{"jsonrpc": "2.0", "id": <id>, "result": {}}` to keep desktop sessions healthy.

5. **Automatic Secret & JWT Redaction**
   - `SecretRedactor` in `live_mcp_showcase.py` scrubs configured secrets, `aks_...` tokens, `Bearer ...` headers, and 3-segment `eyJ...` JWTs from both `stdout` and `stderr` (even under `--verbose` or upstream error echoes).

---

## Governed MCP Tools (`10` Total)

All 10 tools are exposed on `POST /api/mcp` (`MCP-Protocol-Version: 2025-03-26`). Structured SKU/quote calls (`product_id` + `quantity`, `quote_id`) route directly to `core-api` with zero LLM overhead (`X-Aimo-Route: core-api`), while planning/agentic flows route through `agentic-service -> core-api` with identical Layer 1 policy enforcement:

| # | MCP Tool | Key Arguments | Governance & Behavior |
|---|---|---|---|
| 1 | `search_products` | `q`, `category`, `allergen_free`, `label`, `diet` (`L\|VL\|M\|G\|VEG\|HEART`), `limit`, `offset` | Searches the local seeded product catalog with Finnish diet/allergen filtering; zero upstream API latency. |
| 2 | `check_products` | `product_ids` (`1..50`), `requested_delivery_date` (`YYYY-MM-DD`), `quantities` | Checks live unit pricing, stock/terminal availability, cut-off risk (`ON_TIME`, `AT_RISK`, `LATE`), and `co2_basis`. |
| 3 | `create_quote` | `requested_delivery_date`, `lines` (`[{product_id, quantity}]`), `diet` | Locks pricing & availability for `min(30 min, closing_time)`, computes `SHA-256` line snapshot, and returns `policy_preview`. |
| 4 | `optimize_quote` | `requested_delivery_date`, `lines` (`1..25`), `diet`, `objective` (`BALANCED\|SAVE_MONEY\|MAX_RELIABILITY\|LOW_CARBON`), `allow_plant_based_swaps` | Previews diet-safe, unit-normalized alternative SKUs before locking a quote (`<= 50` live check IDs). |
| 5 | `negotiate_quote` | `requested_delivery_date`, `lines`, `diet`, `target_discount_pct`, `target_total_eur`, `justification` | Company-gated (`allow_price_negotiation` in `/admin`) deterministic volume/bundle tendering after diet/stock/cut-off checks; returns a locked 30-min quote (`403 NEGOTIATION_DISABLED` when off). |
| 6 | `place_order` | `quote_id`, `idempotency_key`, `diet`, `note` | Evaluates Layer 1 `policy.py` and `content_hash`: submits immediately if `AUTO`, holds in `PENDING_APPROVAL` (with optional 5h stock hold), or rejects with `403 POLICY_BLOCKED`. |
| 7 | `get_order` | `order_id` | Polls company- and agent-isolated order state (`PENDING_APPROVAL`, `SUBMITTING`, `PLACED` with `gateway_reference`, `FAILED`, `CANCELLED`). |
| 8 | `find_on_sale_products` | `q`, `category`, `diet`, `valio_only`, `on_sale_only`, `requested_delivery_date`, `limit` | Discovers discounted (`listPrice > catalogPrice`) and actively promoted catalog items. |
| 9 | `list_promotions` | `active_only` (default `true`) | Lists active company promotions and culinary campaign rules (read-only). |
| 10 | `plan_menu` | `pax` (`1..5000`), `meal_type`, `requested_delivery_date`, `diet`, `prefer_on_sale`, `include_valio_recipes`, `max_budget_eur` | Generates a diet-safe Nordic foodservice menu plan with recipe quantities, CO2 estimate/basis, quote draft lines, and `policy_preview`. |

---

## Seeded Catalog & `--sku` Configuration (No Proprietary Dataset Dumps)

No confidential wholesale price dumps or proprietary catalog datasets are committed to this repository:
- **Where catalog items come from:** Product SKUs, allergen metadata, mock/live pricing, and stock states come from **your own seeded database catalog** (populated locally via `backend/scripts/demo_seed.py` for the synthetic demo fixture catalog, or `backend/scripts/seed_catalog.py` when importing your own catalog dataset).
- **Dynamic SKU discovery:** Agents should always call `search_products` first to look up valid `product_id` values, pack units (`sales_unit`, `base_unit`), and dietary attributes from the active catalog rather than hardcoding SKU assumptions.
- **Customizing SKUs in `live_mcp_showcase.py`:** By default, [`mcp/live_mcp_showcase.py`](mcp/live_mcp_showcase.py) targets the synthetic demo fixture SKUs created by `demo_seed.py` (`464306` lactose-free milk drink, `469363` partial-stock item, `463038` passed-cutoff terminal item). When running against your own seeded catalog, pass `--sku`, `--search-query`, `--partial-sku`, and `--cutoff-sku`:
  ```bash
  python3 clients/mcp/live_mcp_showcase.py \
    --mcp-config ./.mcp.json \
    --sku YOUR_PRIMARY_SKU \
    --search-query "milk" \
    --partial-sku YOUR_PARTIAL_STOCK_SKU \
    --cutoff-sku YOUR_PAST_CUTOFF_SKU \
    --no-wait
  ```

---

## Quick Start & Configuration

### Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `AIMO_BASE_URL` | No | `http://127.0.0.1:8000` | Gateway origin (`http://127.0.0.1:8001` direct FastAPI backend, `http://127.0.0.1:8000` Vite proxy, or `https://gateway.example.invalid`) |
| `AIMO_AGENT_ID` | Yes (with secret) | — | Agent identifier (`agt_...`) issued in the **Agents** UI |
| `AIMO_AGENT_SECRET` | Yes (with ID) | — | One-time agent secret (`aks_...`) shown on creation/rotation |
| `AIMO_BEARER_TOKEN` | Optional fallback | — | Pre-minted 60-minute JWT if not exchanging `AIMO_AGENT_ID` + `AIMO_AGENT_SECRET` |
| `AIMO_TOKEN_EXPIRES_AT` | No | — | Optional ISO-8601 expiry timestamp paired with `AIMO_BEARER_TOKEN` |
| `AIMO_TIMEOUT_SECONDS` | No | `30` | Per-request HTTP timeout in seconds |
| `AIMO_INSECURE_SKIP_TLS_VERIFY` | No | `false` | Set `true` **only** for local self-signed HTTPS dev certificates |

> **Security:** Never commit `.mcp.json`, `aimo_credentials.json`, `.env`, or live `aks_...` secrets to version control. Keep [`skills/claude/.mcp.json.template`](skills/claude/.mcp.json.template) and [`mcp/configs/*.json`](mcp/configs/) restricted to placeholders.

### 1. Health & Capability Verification (`--check`)

```bash
# Using environment variables against local FastAPI backend (:8001):
AIMO_BASE_URL="http://127.0.0.1:8001" \
AIMO_AGENT_ID="agt_YOUR_AGENT_ID" \
AIMO_AGENT_SECRET="aks_YOUR_AGENT_SECRET" \
python3 clients/mcp/aimo_mcp_bridge.py --check

# Or using a local .mcp.json downloaded from the Agents UI:
python3 clients/mcp/aimo_mcp_bridge.py --check --mcp-config ./.mcp.json
```

`--check` exchanges credentials, validates the MCP `2025-03-26` `InitializeResult` schema, lists all 10 tools, and prints `allow_price_negotiation` plus `supported_matters`.

### 2. One-Shot MCP Tool & Same-Origin REST Calls (`--call`, `--rest`)

```bash
# Invoke an MCP tool directly:
python3 clients/mcp/aimo_mcp_bridge.py --mcp-config ./.mcp.json \
  --call search_products '{"q":"maito","diet":"L","limit":5}'

# Call an authenticated same-origin REST endpoint:
python3 clients/mcp/aimo_mcp_bridge.py --mcp-config ./.mcp.json \
  --rest GET /api/health
```

### 3. Run the 4-Act External MCP Showcase (`live_mcp_showcase.py`)

[`mcp/live_mcp_showcase.py`](mcp/live_mcp_showcase.py) spawns `aimo_mcp_bridge.py` over real subprocess stdio JSON-RPC 2.0, aligns delivery dates with `GET /api/health` (`Europe/Helsinki`), and exercises:
1. **Act 1:** Catalog search -> quote -> `PENDING_APPROVAL` (`R-AUTO-LIMIT`) + optional live approval polling (`get_order` -> `PLACED`)
2. **Act 2:** Partial stock detection (`PARTIAL` / `R-DELIVERY-RISK`) -> diet-safe substitute lookup (`POST /api/products/substitutes`)
3. **Act 3:** Missed terminal cut-off guard (`CUTOFF_PASSED` / `LATE`) — halts without placing an unapproved late order
4. **Act 4:** Deterministic hard dietary block (`diet="M"` on a dairy item -> `403 POLICY_BLOCKED` / `R-HARD-DIET`)

```bash
# Non-blocking CI / smoke run across all 4 acts:
python3 clients/mcp/live_mcp_showcase.py --mcp-config ./.mcp.json --act all --no-wait

# Interactive Act 1 run polling up to 90s for human approval in /approvals or @your_aimo_bot:
python3 clients/mcp/live_mcp_showcase.py --mcp-config ./.mcp.json --act 1 --wait-approval-seconds 90
```

---

## Running Automated Tests

All unit and subprocess integration tests in `clients/mcp/` run completely offline against deterministic in-memory / ephemeral `127.0.0.1` mock transports (zero external network calls):

```bash
# Run with pytest (20 tests):
pytest -q clients/mcp

# Or run with Python's built-in unittest runner (zero dependencies):
python3 -m unittest discover -s clients/mcp -p "test_*.py" -v
```
