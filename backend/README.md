# AimoAnnos Backend (`core-api` & `agentic-service`)

Governed B2B foodservice procurement engine built with **Python 3.12**, **FastAPI**, **SQLAlchemy 2.0**, and **Pydantic v2**.

AimoAnnos separates **probabilistic agent reasoning** from **deterministic financial, operational, and food-safety enforcement**: AI agents can search catalogs, plan menus, negotiate substitutions, and assemble quotes, while every quote and order is gated in pure Python code before a single item reaches a supplier gateway.

---

## 1. Architecture Overview

```text
External Clients (Web UI · MCP Clients · A2A Agents · Telegram Bot Bridge)
                                   │
                                   ▼
                 ┌───────────────────────────────────┐
                 │     AgenticRoutingMiddleware       │
                 │   Deterministic Request Inspector  │
                 └─────────┬───────────────┬─────────┘
        Structured payloads│               │Unstructured / /api/agentic/*
                           ▼               ▼
            ┌────────────────────┐   ┌──────────────────────────┐
            │     core-api       │◄──┤     agentic-service      │
            │ Deterministic REST │   │ Intent parsing, menu     │
            │ Quotes · 3-Gate    │   │ planning, substitution   │
            │ Policy · Orders ·  │   │ ranking & tool calling   │
            │ Hash-Chained Audit │   │ (loop-guarded internal   │
            └─────────┬──────────┘   │  HMAC hop -> core-api)   │
                      │              └──────────────────────────┘
                      ▼
     ┌────────────────────────────────────────┐
     │ SQLite (WAL) or MariaDB (26 tables)    │
     │ + Zero-ALTER JSON metadata envelopes   │
     └────────────────────────────────────────┘
```

### Key Architectural Pillars
- **`core-api` vs. `agentic-service` Separation:** `AgenticRoutingMiddleware` (`app/main.py`, `app/request_router.py`) inspects incoming requests deterministically and stamps `X-Aimo-Route: core-api | agentic-service`. Unstructured natural-language order intents are handled by `agentic-service`, which translates them into structured quote/order calls back through `core-api` using a loop-guarded internal HMAC header (`X-Aimo-Forwarded-By`) so **no agentic code path can ever bypass `core-api` policy gates**.
- **26-Table Relational Schema (`app/models.py`):** Runs out-of-the-box on **SQLite** (`WAL` journal mode, `foreign_keys=ON`, `busy_timeout=5000`) and scales seamlessly to **MariaDB / MySQL** (`mysql+pymysql://...`). Covers multi-tenant companies, kitchen branches, human users, delegated agents, catalog products/categories, quotes, orders, inventory holds, hold warnings, promotions, platform campaigns, brand/negotiation settings, A2A tasks, Telegram pairing tokens/sessions, idempotency keys, gateway submissions, and hash-chained audit logs.
- **Zero-ALTER Extensibility:** Structured JSON columns store rich Synkka allergen state maps, verified nutritional claims, policy evaluation traces, remediation hints, and A2A artifacts without brittle schema migrations.
- **100% Offline-First Defaults:** Ships with `AIMO_MODE=fixture`, `LLM_PROVIDER=fake`, and `FAST_LLM_PROVIDER=fake` so local development and automated tests run offline with zero external API keys.

---

## 2. 3-Gate Deterministic Policy Engine (14 Rules in Code, Never LLM)

Every quote (`POST /api/quotes`) and order (`POST /api/orders`) is evaluated by `app/services/policy.py` against the canonical 14-rule registry in `app/governance_registry.py`. **LLMs never decide policy outcomes.**

### Gate 1 — `SAFETY_LOCK` (Hard Red Lock — Never Overridable)
Applies equally to human buyers and autonomous agents. Cannot be overridden via approval.
- **`R-HARD-DIET` — Finnish Dietary & EU Annex II Allergen Enforcement (`app/domain/valio_rules.py`):**
  - Strictly distinguishes **`L` (Lactose-Free, `< 0.01 g / 100 g`) ≠ `VL` (Low-Lactose, `< 1.0 g / 100 g`) ≠ `M` (Milk-Protein-Free)**. A lactose-free cow's milk product passes `L` and `VL` but is hard-blocked (`403 POLICY_BLOCKED`) under `M` or `VEG`.
  - **Missing allergen data is never treated as safe:** unstated allergens evaluate to `UNKNOWN`, triggering conservative `REVIEW` / `BLOCKED` handling rather than silent `PASS`.
  - **Strict code namespace isolation:** GS1/SAP unit codes and allergen codes are looked up in isolated namespaces (e.g., `"ST"` means *piece* in `UNIT` and *pistachio* in `ALLERGEN` — never conflated).
- **`R-NOT-DELIVERABLE`:** Hard-blocks lines flagged `NOT_DELIVERABLE`, `NO_PRICE`, or `SALES_BLOCKED`.

### Gate 2 — `OPERATIONAL_SANITY` (Amber Shield — Human-Reviewable with 1-Tap Rescue)
Catches supply-chain and kitchen quantity anomalies before supplier submission, routing orders to `PENDING_APPROVAL` with actionable remediation hints:
- **`R-DELIVERY-RISK`:** Flags `CUTOFF_PASSED`, `LATE`, `PARTIAL` stock, or `UNIT_MISMATCH`, paired with 1-tap `GOOD / BETTER / BEST` diet-safe substitutes (`POST /api/products/substitutes`).
- **`R-RECIPE-SANITY`:** Holds orders whose ingredient quantities exceed `1.5×` the expected culinary yield for `target_pax`.
- **`R-STANDING-DRIFT`:** Holds recurring restock orders when quantity or EUR spend drifts `> 25%` above the company's historical `PLACED` baseline.
- **`R-SEMANTIC-GUARD`:** Detects split-order smurfing and coercive prompt-injection notes, forcing human verification.
- **Inventory Reservation Holds:** Pending-approval orders place time-bounded stock holds (`AIMO_HOLD_HOURS`, default `5.0h`) and track repeated hold expirations over a rolling 7-day window.

### Gate 3 — `AUTONOMY_ENVELOPE` (Financial Mandates & Delegated Authority)
Enforces per-agent commercial mandates configured by human managers:
- **`R-USER-DIRECT`:** Human buyers bypass agent spend caps once Gate 1 & Gate 2 pass.
- **`R-AGENT-NO-ORDER`:** Hard-blocks scout/read-only agents (`can_place_orders=False`).
- **`R-MAX-VALUE`:** Hard-blocks single agent orders exceeding `max_order_value_eur`.
- **`R-AUTO-LIMIT`:** Routes agent orders above `auto_approve_limit_eur` to `PENDING_APPROVAL`.
- **`R-DAILY-SPEND`:** Routes agent orders that push cumulative daily spend past `daily_spend_limit_eur` to `PENDING_APPROVAL`.
- **`R-CATEGORY` & `R-LINE-QTY`:** Routes out-of-category SKUs (`allowed_categories`) or oversized lines (`max_quantity_per_line`) to `PENDING_APPROVAL`.
- **`R-AGENT-WITHIN-LIMITS`:** Auto-places (`AUTO` -> `PLACED`) clean orders within every delegated limit.

### Cryptographic Locks, Order Assurance & Pre-Cutoff Undo
- **SHA-256 `content_hash` Quote-to-Order Lock:** Computed deterministically over canonical `(product_id, quantity, unit_price_eur, line_total_eur)`, `total_excl_vat_eur`, and `requested_delivery_date`. Human approvals (`POST /api/orders/{id}/approve`) must echo the exact `content_hash`, preventing bait-and-switch mutations.
- **Order Assurance Score (`50–100`):** Quantifies live price certainty, supplier cut-off margin, delivery reliability, diet verification, and budget compliance on every order.
- **Pre-Cutoff Undo:** Placed orders can be safely cancelled/undone (`POST /api/orders/{id}/cancel`) while supplier `closingTime` has not yet passed.
- **Tamper-Evident Hash-Chained Audit Trail:** Every state transition appends an `audit_events` record linking `prev_hash -> entry_hash` (SHA-256), verifiable anytime via `GET /api/audit/verify`.

---

## 3. Open Interfaces

| Protocol | Endpoint(s) | Authentication | Highlights |
| :--- | :--- | :--- | :--- |
| **REST / OpenAPI 3.1** | `/api/*`, Swagger UI at `/api/docs`, schema at `/api/openapi.json` | `Bearer <JWT>` (human user or 60-min agent token from `POST /api/agent-auth/token`) | Full procurement lifecycle, `Idempotency-Key` support, `X-Request-ID` & `X-Server-Time` headers on every response. |
| **MCP (JSON-RPC 2.0)** | `POST /api/mcp` | `Bearer <JWT>` | Streamable HTTP Model Context Protocol server exposing **10 governed tools**: `search_products`, `check_products`, `create_quote`, `place_order`, `get_order`, `find_on_sale_products`, `list_promotions`, `plan_menu`, `optimize_quote`, `negotiate_quote`. |
| **A2A Protocol (v0.2.5)** | `GET /.well-known/agent-card.json`, `POST /api/a2a`, `GET /api/a2a/tasks` | Public discovery card; `Bearer <JWT>` for RPC | Multi-turn Agent-to-Agent `message/send`, `tasks/get`, and `tasks/cancel` with structured option negotiation (`input-required` -> `completed`). |
| **Telegram Bridge** | `/api/telegram/*` & `/api/auth/telegram/*` | `X-Telegram-Bridge-Secret` (HMAC-SHA256 derived from `TELEGRAM_BOT_TOKEN`) | 15-minute one-time SHA-256 hashed QR pairing links (`/start link_<token>`), multi-branch kitchen switching, chat quoting, and 1-tap mobile order approvals. |

---

## 4. Supplying Your Own Product Catalog & Optional Fixtures

Proprietary supplier datasets and recorded pricing dumps are **not bundled** in this public repository (only generic category codes/labels in `app/domain/category_mapping.json` are included). You can seed your own catalog JSON in seconds, and `AIMO_MODE=fixture` automatically synthesizes deterministic pricing and stock availability when recorded fixture files are absent.

### 4.1 Product Catalog JSON (`catalog_data/products.json`)
By default, `scripts/seed_catalog.py` reads `../catalog_data/products.json` (or any path passed via `--products`). Supply a JSON array of product objects following this structure:

```json
[
  {
    "id": "900101",
    "category": "10400",
    "salesUnit": "ST",
    "baseUnit": "ST",
    "allowedLotSize": 1,
    "units": [
      { "unitId": "ST", "sizeInBaseUnits": 1 },
      { "unitId": "KI", "sizeInBaseUnits": 10 }
    ],
    "vendorName": "Nordic Dairy Oy",
    "countryOfOrigin": "FI",
    "temperatureCondition": 3,
    "substitutions": [
      { "substituteProduct": "900102" }
    ],
    "synkkaData": {
      "brand": "Nordic",
      "names": [
        { "language": "fi", "value": "Laktoositon kevytmaitojuoma 1 l" },
        { "language": "en", "value": "Lactose-free semi-skimmed milk drink 1 L" }
      ],
      "classifications": [
        {
          "name": "allergen",
          "values": [
            { "id": "AM", "unit": "CONTAINS" }
          ]
        },
        {
          "name": "nonAllergen",
          "values": [
            { "id": "AW", "unit": "FREE_FROM" }
          ]
        },
        {
          "name": "nutritionalClaim",
          "values": [
            { "id": "FREE_FROM_LACTOSE" }
          ]
        }
      ],
      "nutritionalContent": [
        {
          "id": "LACS",
          "value": 0.0,
          "unit": "GRM",
          "basisQuantity": 100,
          "basisQuantityUnit": "MLT",
          "unitPrecision": "APPROXIMATELY"
        }
      ],
      "keyIngredients": [
        { "language": "fi", "value": "Pastöroitu maito, D-vitamiini" }
      ]
    }
  }
]
```

### 4.2 Optional Enrichment CSVs (`carbon_footprint.csv` & `delivery_reliability.csv`)
Both CSV files are **completely optional** — if omitted, `seed_catalog.py` seeds all products cleanly with `co2_kg_per_kg = None` and `delivery_reliability = None`.

- **`catalog_data/carbon_footprint.csv`** (`--co2`):
  ```csv
  productId,carbonFootprintKgPerUnit
  900101,1.15
  ```
- **`catalog_data/delivery_reliability.csv`** (`--reliability`, accepts `0–100` percentages or `0.0–1.0` probabilities):
  ```csv
  productId,reliabilityPct
  900101,98.5
  ```

### 4.3 Automatic Synthetic Fixture Mode vs. Optional Recorded Fixtures
When `AIMO_MODE=fixture` (the default):
- **Zero-config fallback (no files required):** If `tests/fixtures/aimo/price.json` or `availability.json` is not present on disk, `AimoClient` (`app/aimo/client.py`) automatically synthesizes deterministic responses for every requested `product_id`:
  - **Pricing:** `catalogPrice.value = 2.50` EUR excl. VAT (`valueWithTax = 2.84`, `taxRate = 13.5%`, `netPriceFound = true`, `error = false`).
  - **Availability:** `logisticModel = "Stock"`, `availableAmount = 500.0` on `requested_delivery_date`, and `closingTime` set 2 days ahead of `max(requested_date, clock.now())`.
- **Custom recorded fixtures (optional):** If you place `price.json` and `availability.json` inside `backend/tests/fixtures/aimo/`, `AimoClient` loads your recorded payloads directly:
  ```json
  // backend/tests/fixtures/aimo/price.json
  {
    "customerId": "123",
    "currency": "EUR",
    "products": [
      {
        "productId": "900101",
        "quantity": 1.0,
        "netPriceFound": true,
        "error": false,
        "catalogPrice": { "value": 1.95, "valueWithTax": 2.21 },
        "listPrice": { "value": 2.30, "valueWithTax": 2.61 },
        "tax": { "taxRate": 13.5 }
      }
    ]
  }
  ```
  ```json
  // backend/tests/fixtures/aimo/availability.json
  {
    "customerId": "123",
    "products": [
      {
        "productId": "900101",
        "logisticModel": "Stock",
        "closingTime": "2026-11-18T16:00:00+02:00",
        "salesBlock": false,
        "scheduleLines": [
          { "availableDate": "2026-11-19", "availableAmount": 500.0, "salesBlock": false }
        ]
      }
    ]
  }
  ```

---

## 5. Environment Variables (`.env.example`)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DATABASE_URL` | `sqlite:///./aimoannos.db` | SQLAlchemy database URL (`sqlite:///...` or `mysql+pymysql://user:pass@127.0.0.1:3306/aimoannos`). |
| `JWT_SECRET` | `dev-insecure-secret-change-me` | HMAC-SHA256 signing secret for user & agent JWTs (replace with `>= 32` random bytes in production). |
| `AIMO_MODE` | `fixture` | `fixture` (100% offline local/synthetic fixtures) or `live` (upstream HTTP calls). |
| `AIMO_BASE_URL` | `https://api.example.invalid` | Upstream supplier API base URL when `AIMO_MODE=live`. |
| `AIMO_API_KEY` | *(empty)* | Upstream supplier `X-API-Key` credential when `AIMO_MODE=live`. |
| `APP_NOW` | *(empty)* | Optional ISO 8601 clock override (e.g. `2026-11-15T14:00:00+02:00`) for deterministic cut-off demos/tests. |
| `LLM_PROVIDER` | `fake` | Assistant LLM backend: `fake` (offline deterministic), `anthropic`, or `featherless`. |
| `LLM_MODEL` | `claude-sonnet-4-5` | Primary model identifier when using `anthropic` or `featherless`. |
| `ANTHROPIC_API_KEY` | *(empty)* | API key when `LLM_PROVIDER=anthropic`. |
| `FEATHERLESS_API_KEY` | *(empty)* | API key when `LLM_PROVIDER=featherless` or `FAST_LLM_PROVIDER=featherless`. |
| `FAST_LLM_PROVIDER` | `fake` | Fast semantic verifier backend: `fake` (offline deterministic) or `featherless`. |
| `FAST_LLM_MODEL` | `google/gemma-4-31B-it` | Fast semantic verifier model identifier. |
| `FAST_LLM_FALLBACK_MODEL` | `google/gemma-4-26B-A4B-it` | Fallback model for fast semantic decisions. |
| `LLM_MAX_CALLS_PER_HOUR` | `200` | Hourly safety cap on outbound LLM calls. |
| `TELEGRAM_BOT_USERNAME` | `your_aimo_bot` | Bot username (without `@`) used when generating `https://t.me/...` QR pairing deep links. |
| `TELEGRAM_BOT_TOKEN` | *(empty)* | Telegram Bot token used by [`../scripts/telegram_bridge.py`](../scripts/telegram_bridge.py) and HMAC bridge authentication. |
| `AIMO_HOLD_HOURS` | `5.0` | Hours to reserve finite stock for `PENDING_APPROVAL` orders. |
| `ORDER_GATEWAY_SIMULATE` | `none` | Gateway fault-injection mode: `none`, `timeout_after_accept`, or `error`. |
| `APP_ENV` | `dev` | Runtime environment (`dev` enables `/api/auth/demo-personas` and `/api/dev/*` helpers). |
| `PORT` | `8001` | HTTP listen port. |

---

## 6. Quick Start & Running Tests

### Prerequisites
- Python `>= 3.12` and [`uv`](https://docs.astral.sh/uv/)

### 1. Install Dependencies & Configure Environment
```bash
cd backend
uv sync
cp .env.example .env
```

### 2. (Optional) Seed Your Product Catalog
Place `products.json` (and optional `carbon_footprint.csv` / `delivery_reliability.csv`) in `../catalog_data/` or pass `--products`:
```bash
uv run python scripts/seed_catalog.py --products ../catalog_data/products.json
```

### 3. Seed Demo Personas & Delegated Agents
```bash
uv run python scripts/demo_seed.py --with-sample-orders
```
This idempotently creates **3 generic F&B demo personas** (password `demo1234`, zero pre-linked Telegram IDs) and writes rotated agent credentials for Hotel Aurora Oy to `backend/.demo_credentials`:
1. **`anna.korhonen@hotel.fi`** — *Anna Korhonen* · *Hotel Aurora Oy* (`Hotel F&B Buyer`) + 3 delegated agents (`kitchen_agent`, `reorder_bot`, `read_only_scout`)
2. **`mikko.laine@restaurant.fi`** — *Mikko Laine* · *Bistro Satama Oy* (`Head Chef`)
3. **`sari.virtanen@festival.fi`** — *Sari Virtanen* · *Nordic Campus Catering Oy* (`Event Producer`)

*(If you run `demo_seed.py --with-sample-orders` before seeding a product catalog, it still succeeds cleanly and simply skips sample order creation.)*

### 4. Start the API Server
```bash
uv run uvicorn app.main:app --reload --port 8001
```
- Interactive OpenAPI 3.1 docs: `http://127.0.0.1:8001/api/docs`
- Health check: `http://127.0.0.1:8001/api/health`
- Re-export static `openapi.json`: `uv run python scripts/export_openapi.py`

### 5. Lint & Run Tests
```bash
uv run ruff check app tests scripts
uv run pytest -q
```
*(Tests that specifically target omitted recorded catalog fixture files are automatically skipped with a clear pointer to Section 4 above, while offline auth, health, agent delegation, audit hash-chain, demo persona, optional CSV seeding, and synthetic fixture quote-to-order tests execute out-of-the-box.)*
