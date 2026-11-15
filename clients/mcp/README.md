# AimoAnnos MCP Bridge, CLI & Showcase (`clients/mcp/`)

This package provides the zero-dependency Python 3.11+ **stdio-to-Streamable-HTTP MCP bridge** ([`aimo_mcp_bridge.py`](aimo_mcp_bridge.py)), ready-to-use MCP client configurations ([`configs/`](configs/)), and the 4-act live external buyer showcase ([`live_mcp_showcase.py`](live_mcp_showcase.py)) for AimoAnnos (`POST /api/mcp`, protocol `2025-03-26`).

For an overview of the full `clients/` ecosystem—including the 1-click Claude `.zip` plugin bundler and reusable skills in `../skills/`—see [`../README.md`](../README.md).

---

## Architecture & Security Highlights

1. **Universal Stdio-to-Streamable-HTTP Bridge (`2025-03-26`)**
   - Uses Python `>=3.11` stdlib only (`dependencies = []` in [`pyproject.toml`](pyproject.toml)).
   - Reads newline-delimited JSON-RPC 2.0 requests from `stdin` and forwards them to `POST ${AIMO_BASE_URL}/api/mcp` with `Authorization: Bearer <token>` and `MCP-Protocol-Version: 2025-03-26`.

2. **Proactive & Reactive 60-Minute Agent JWT Management**
   - Exchanges `AIMO_AGENT_ID` (`agt_...`) + `AIMO_AGENT_SECRET` (`aks_...`) at `POST ${AIMO_BASE_URL}/api/agent-auth/token` for a 60-minute Bearer JWT (`aud=aimoannos-api`).
   - **Proactive refresh:** Refreshes automatically within `60` seconds of `expires_at` (or the JWT `exp` claim on pre-minted tokens).
   - **Reactive refresh:** Re-exchanges credentials and retries once if `/api/mcp` or a `--rest` call returns HTTP `401` (including behind protected preview origins that return `401 JUDGE_ACCESS_REQUIRED` when `Authorization: Bearer <token>` is missing or expired).

3. **Credential Discovery Precedence & `?raw` Bundle Contract**
   `BridgeConfig.from_env()` resolves configuration strictly in memory in the following order:
   1. Explicit `--mcp-config <path>` (`mcpServers.aimoannos.env`, expanding `${VAR}` against `os.environ`)
   2. Process environment variables (`AIMO_BASE_URL`, `AIMO_AGENT_ID`, `AIMO_AGENT_SECRET`, `AIMO_BEARER_TOKEN`, `AIMO_TOKEN_EXPIRES_AT`)
   3. Optional sibling `aimo_credentials.json` or `.mcp.json` next to `aimo_mcp_bridge.py` (or parent plugin root)
   4. `BUNDLED_*` sentinel constants (`BUNDLED_BASE_URL`, `BUNDLED_AGENT_ID`, `BUNDLED_AGENT_SECRET`, `BUNDLED_BEARER_TOKEN`, `BUNDLED_TOKEN_EXPIRES_AT`), which remain inert `__AIMO_BUNDLED_*__` placeholders in git and are populated purely in browser memory when generating a 1-click Claude `.zip` bundle in the UI.

4. **Zero-Permission Defaults & Same-Origin `--rest` Exfiltration Guard**
   - Agents start with zero-order default mandates until granted permissions by an administrator, and cannot bypass Layer 1 `policy.py` or `SHA-256 content_hash` checks.
   - `call_rest_one_shot()` (`--rest`) enforces an exact `(scheme, netloc)` match against `AIMO_BASE_URL` and rejects cross-origin URLs, HTTP downgrades, protocol-relative paths (`//...`, `/\...`), and userinfo (`user@host`) so Bearer JWTs are never sent off-origin.

5. **Claude Desktop `InitializeResult` Normalization & `ping` Keepalive**
   - Normalizes `capabilities.experimental.supportedMatters` into a strict keyed record (`{ menu_planning, order_execution, preliminary_tendering: { enabled: bool }, exception_handling }`) and validates `InitializeResult` against MCP `2025-03-26`.
   - Exposes `allow_price_negotiation` (`preliminary_tendering.enabled`, managed per company in `/admin`) in `--check` output, and answers `ping` requests with `{}` if upstream returns `-32601`.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `AIMO_BASE_URL` | No | `http://127.0.0.1:8000` | AimoAnnos base URL (`http://127.0.0.1:8001` backend, `http://127.0.0.1:8000` frontend proxy, or `https://gateway.example.invalid`) |
| `AIMO_AGENT_ID` | Yes (with secret) | — | Agent ID (`agt_...`) created in the AimoAnnos **Agents** UI |
| `AIMO_AGENT_SECRET` | Yes (with ID) | — | One-time agent secret (`aks_...`) shown at creation or rotation |
| `AIMO_BEARER_TOKEN` | Optional fallback | — | Pre-exchanged 60-minute agent JWT |
| `AIMO_TOKEN_EXPIRES_AT` | No | — | Optional ISO-8601 expiry timestamp for `AIMO_BEARER_TOKEN` |
| `AIMO_TIMEOUT_SECONDS` | No | `30` | HTTP request timeout in seconds |
| `AIMO_INSECURE_SKIP_TLS_VERIFY` | No | `false` | Set `true` only for local self-signed HTTPS dev certificates |

> **Security:** Never commit real `AIMO_AGENT_SECRET`, `AIMO_BEARER_TOKEN`, `.mcp.json`, or `aimo_credentials.json` files to git.

---

## Exposed Governed MCP Tools (`10` Total)

- `search_products` — Search the local seeded catalog (`q`, `category`, `allergen_free`, `label`, `diet`: `L|VL|M|G|VEG|HEART`, `limit`, `offset`)
- `check_products` — Live pricing, stock/terminal availability, delivery risk, and `co2_basis` for `1..50` SKUs
- `create_quote` — Lock prices and availability (`min(30 min, closing_time)`) and compute `policy_preview`
- `optimize_quote` — Preview diet-safe, unit-normalized cart swaps (`BALANCED`, `SAVE_MONEY`, `MAX_RELIABILITY`, `LOW_CARBON`) before locking a quote
- `negotiate_quote` — Company-gated (`allow_price_negotiation` in `/admin`) deterministic volume/bundle price negotiation after diet, stock, and cut-off checks, returning a locked 30-minute quote
- `place_order` — Submit an `OPEN` quote with `idempotency_key` through Layer 1 policy and `SHA-256 content_hash` verification
- `get_order` — Poll or inspect a single company- and agent-isolated order by ID
- `find_on_sale_products` — Discover live discounted (`listPrice > catalogPrice`) and promoted SKUs
- `list_promotions` — Read active company promotions and culinary campaign recommendations
- `plan_menu` — Plan a diet-safe Nordic foodservice menu for `pax` (`1..5000`) with recipes, CO2 estimate/basis, quote draft lines, and `policy_preview`

---

## CLI Usage & Client Configuration Templates

### Bridge CLI Modes

```bash
# 1. Verify auth, MCP InitializeResult schema, company capabilities, and tools/list:
AIMO_BASE_URL="http://127.0.0.1:8001" \
AIMO_AGENT_ID="agt_YOUR_AGENT_ID" \
AIMO_AGENT_SECRET="aks_YOUR_AGENT_SECRET" \
python3 clients/mcp/aimo_mcp_bridge.py --check

# 2. Or read credentials strictly in-memory from a local .mcp.json:
python3 clients/mcp/aimo_mcp_bridge.py --check --mcp-config ./.mcp.json

# 3. Call any MCP tool one-shot:
python3 clients/mcp/aimo_mcp_bridge.py --mcp-config ./.mcp.json \
  --call search_products '{"q":"laktoositon maito","diet":"L"}'

# 4. Call an authenticated same-origin REST or A2A endpoint:
python3 clients/mcp/aimo_mcp_bridge.py --mcp-config ./.mcp.json \
  --rest GET /api/health
python3 clients/mcp/aimo_mcp_bridge.py --mcp-config ./.mcp.json \
  --rest POST /api/a2a '{"jsonrpc":"2.0","id":1,"method":"session/connect","params":{}}'
```

### Ready-to-Use Client Config Templates (`configs/`)

- **Claude Code / `.mcp.json` (stdio, recommended — auto-refreshes JWTs):** [`configs/mcp.stdio.json`](configs/mcp.stdio.json)
- **Claude Code / `.mcp.json` (direct Streamable HTTP with Bearer JWT):** [`configs/mcp.http.json`](configs/mcp.http.json)
- **Claude Desktop (`claude_desktop_config.json`):** [`configs/claude_desktop_config.json`](configs/claude_desktop_config.json)
- **Cursor (`.cursor/mcp.json`):** [`configs/cursor_mcp.json`](configs/cursor_mcp.json)

---

## Live 4-Act External MCP Showcase (`live_mcp_showcase.py`)

[`live_mcp_showcase.py`](live_mcp_showcase.py) drives `aimo_mcp_bridge.py` over real subprocess stdio JSON-RPC 2.0 (`2025-03-26`), aligns its default next-Monday delivery date with `GET /api/health` (`server_time` / `X-Server-Time`), redacts all secrets/JWTs from output, and executes four governance acts:

1. **Act 1 (`--act 1`):** Search -> quote -> `place_order` (`PENDING_APPROVAL` via `R-AUTO-LIMIT`) -> optional live human approval polling (`get_order` -> `PLACED`, e.g. approved in `/approvals` or via `@your_aimo_bot` by `anna.korhonen@hotel.fi`).
2. **Act 2 (`--act 2`):** Partial stock detection (`PARTIAL`, `R-DELIVERY-RISK`) -> REST `POST /api/products/substitutes` self-healing candidates.
3. **Act 3 (`--act 3`):** Passed terminal cut-off guard (`CUTOFF_PASSED`, `LATE`) — halts without placing an unapproved late order.
4. **Act 4 (`--act 4`):** Hard dietary safety block (`diet="M"` on a dairy SKU -> `403 POLICY_BLOCKED` / `R-HARD-DIET`).

All catalog items come from your locally seeded database catalog (`demo_seed.py` or `seed_catalog.py`). Override the default demo fixture SKU IDs (`464306`, `469363`, `463038`) with `--sku`, `--search-query`, `--partial-sku`, and `--cutoff-sku`:

```bash
# Run all 4 acts non-interactively (CI / pre-flight mode):
python3 clients/mcp/live_mcp_showcase.py --mcp-config ./.mcp.json --act all --no-wait

# Interactive Act 1 waiting up to 90s for human approval:
python3 clients/mcp/live_mcp_showcase.py --mcp-config ./.mcp.json --act 1 --wait-approval-seconds 90

# Target custom SKU IDs from your own seeded catalog:
python3 clients/mcp/live_mcp_showcase.py --mcp-config ./.mcp.json \
  --sku SKU_MILK_L --search-query "milk" \
  --partial-sku SKU_PARTIAL --cutoff-sku SKU_CUTOFF --no-wait
```

---

## Running Automated Tests (`pytest`)

```bash
# Run the full offline test suite (20 tests):
pytest -q clients/mcp

# Or with stdlib unittest:
python3 -m unittest discover -s clients/mcp -p "test_*.py" -v
```
