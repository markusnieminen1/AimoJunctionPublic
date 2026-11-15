# Aimo Annos — Frontend

Governance-first B2B foodservice purchasing portal and human-in-the-loop control plane for **Aimo Annos**. Built for kitchen managers, F&B buyers, and autonomous AI purchasing agents operating under deterministic Layer 1 policy guardrails.

---

## Tech Stack

- **Framework & UI:** React 18, TypeScript (strict), React Router v6, Tailwind CSS, Lucide Icons
- **Server State & Reliability:** TanStack Query v5, scoped `Idempotency-Key` management, SHA-256 `content_hash` approval verification, Helsinki-timezone (`Europe/Helsinki`) server clock synchronization
- **Contract Type-Safety:** OpenAPI 3.1 TypeScript codegen (`openapi-typescript` -> `src/api/schema.gen.ts`) paired with a compile-time contract drift guard (`src/api/schema.check.ts`)
- **Offline Mock Engine:** Mock Service Worker v2 (`src/mocks/`) with a purely synthetic TypeScript foodservice catalog (`SYNTHETIC_PRODUCTS` in `src/mocks/data.ts`) and `sessionStorage` state persistence
- **Build & Test:** Vite 5, Vitest (jsdom), Testing Library (`@testing-library/react` + `@testing-library/user-event`)
- **Localization:** Full English (`EN`) and Finnish (`FI`) UI localization (`src/i18n/`)

---

## Quick Start

```bash
cd frontend
npm install

# Start Vite dev server on http://127.0.0.1:8000
npm run dev
```

### Build, Typecheck & Test Commands

| Command | Description |
| --- | --- |
| `npm install` | Install frontend dependencies |
| `npm run dev` | Start Vite dev server on `:8000` (proxies `/api` to `:8001` when `VITE_USE_MOCKS=false`) |
| `npm run typecheck` | Run `tsc --noEmit` across app, tests, and OpenAPI schema compatibility assertions |
| `npm test` | Run the full Vitest unit & MSW integration suite once (`vitest run`) |
| `npm run test:watch` | Run Vitest in interactive watch mode |
| `npm run build` | Typecheck and build production static assets into `dist/` |
| `npm run preview` | Preview the production build locally |
| `npm run gen:schema` | Regenerate `src/api/schema.gen.ts` from `../backend/openapi.json` |

---

## Live Backend vs. Offline MSW Mock Mode

The frontend uses relative `/api/*` requests everywhere (zero hardcoded hosts) and supports two execution modes controlled by `VITE_USE_MOCKS`:

### 1. Live FastAPI Backend Mode (`VITE_USE_MOCKS=false`)

Proxies `/api/*` from the Vite dev server (`http://127.0.0.1:8000`) to the FastAPI backend (`http://127.0.0.1:8001` by default, configurable via `API_PROXY_TARGET`).

```bash
# Terminal 1: run FastAPI backend on :8001 (see ../backend/README.md)
# Terminal 2: start frontend against live backend
VITE_USE_MOCKS=false API_PROXY_TARGET=http://127.0.0.1:8001 npm run dev
```

You can also spin up an isolated, self-contained SQLite + fixture backend + Vite session for local integration testing via:

```bash
./scripts/integration-backend.sh
```

### 2. Offline MSW Mock Mode (`VITE_USE_MOCKS=true`)

Starts an in-browser Mock Service Worker (`src/mocks/browser.ts`) that simulates the complete backend contract — quotes, 3-Gate Layer 1 policy evaluation, SHA-256 `content_hash` approvals, A2A task traces, Cart Optimizer, Chaos Flight Simulator, and scripted AI Chef Assistant — without requiring a running backend.

```bash
VITE_USE_MOCKS=true npm run dev
```

- **Synthetic Catalog Only:** Offline mode uses `SYNTHETIC_PRODUCTS` (`100001`–`100012` in `src/mocks/data.ts`), a compact, purely fictional TypeScript catalog covering chilled/frozen/ambient zones, finite stock, terminal cut-offs, allergen matrices, and missing-allergen edge cases. No proprietary catalog JSON files or supplier GTINs are bundled.
- **Reference Demo Personas (password `demo1234`):**
  - `anna.korhonen@hotel.fi` — *Hotel Aurora Oy* (Hotel F&B Buyer)
  - `mikko.laine@restaurant.fi` — *Bistro Satama Oy* (Head Chef)
  - `sari.virtanen@festival.fi` — *Nordic Campus Catering Oy* (Event Producer)
- **Browser Console Dev Helpers:** When `VITE_USE_MOCKS=true`, `window.__mock` exposes helpers (`simulateAgentOrder()`, `advanceMinutes(31)`, `setPrice("100001", 1.05)`, `setGateway("timeout")`, `setAssistantAvailable(false)`, `reset()`) for testing edge cases interactively.

---

## Environment Variables

Copy `.env.example` to `.env.local` (or `.env.development`) to override defaults. Never place secrets in `VITE_*` variables, as Vite embeds them into client-side JavaScript.

```dotenv
# Offline MSW mock mode ("true") vs live FastAPI backend via Vite /api proxy ("false")
VITE_USE_MOCKS=false

# Public Telegram bot username (without @) used as fallback for 1-tap approval & QR pairing deep links
VITE_TELEGRAM_BOT_USERNAME=your_aimo_bot

# Dev-server only (read by vite.config.ts, not exposed to browser bundle): upstream FastAPI URL
API_PROXY_TARGET=http://127.0.0.1:8001
```

---

## Key Views & Workflows

| Route | View & Core Capabilities |
| --- | --- |
| `/login`, `/register` | Company account registration and authentication, plus 1-click sign-in cards for the 3 generic reference kitchen personas in dev/mock environments. |
| `/` | **My Aimo Dashboard:** Live KPI tiles (pending approvals, today/7-day spend, 7-day estimated CO₂e, active agents), pending approval preview, recent order blocks, active agent mandates, and instant prompt handoff to `/assistant`. |
| `/catalog`, `/products/:id` | **Wholesale Catalog & Product Detail:** Server-side Finnish dietary code filters (`L`, `VL`, `M`, `G`, `VEG`, `HEART`), strict allergen safety matrix (products lacking supplier allergen arrays render an explicit *Unknown — verify supplier specification* warning, never "safe"), temperature zone filtering, category CO₂e & on-time delivery reliability indicators, live per-date price/stock checks, and an *On sale & chef's picks* deals strip. |
| `/cart` | **Cart, Pre-Flight Optimizer & Quote Engine:** 3-Gate Governance Funnel policy preview, multi-objective **Cart Optimizer** (`BALANCED`, `SAVE_MONEY`, `MAX_RELIABILITY`, `LOW_CARBON` with explicit opt-in for plant-based swaps), diet-safe substitute finder for `PARTIAL`/`LATE` lines, automatic `ON_TIME` delivery-date alignment to the last available item, optional 1-tap volume price negotiation, and idempotent order placement. |
| `/assistant` | **AI Chef Purchasing Assistant:** Conversational purchasing, menu planning (with step-by-step reasoning traces and partial CO₂e coverage indicators), live deal discovery, A2A recipe yield sanity checks (`R-RECIPE-SANITY`), and configurable **Order Assurance** auto-execution with 1-tap pre-cutoff Undo. |
| `/agents`, `/agents/:id` | **AI Agents & Mandates:** Zero-permission-by-default agent creation (`can_place_orders`, `auto_approve_limit_eur`, `max_order_value_eur`, `daily_spend_limit_eur`, `max_quantity_per_line`, `allowed_categories`, hard diet locks), instant kill switch (`AGENT_DISABLED`), quarantined agent anomaly review, Order Assurance threshold slider, and a one-time secret modal that generates a self-contained **1-click Claude Code / Desktop MCP `.zip` plugin bundle** (`src/lib/agentBundle.ts`) alongside LangGraph, REST, and A2A setup snippets. |
| `/approvals` | **Human Sign-Off Inbox:** Cryptographic approval cards bound to SHA-256 `content_hash` and scoped `Idempotency-Key` headers. Detects mid-review price or quantity drift (`409 QUOTE_STALE` / `APPROVAL_CONTENT_CHANGED`) with before/after line highlights, displays 5-hour stock reservation hold countdowns, maps fired rules into the **3-Gate Governance Funnel** with remediation links, and offers 1-tap safe rescue actions (right-size to recipe norm or swap to an `ON_TIME` diet-safe substitute). |
| `/orders`, `/orders/:id` | **Orders & Reasoning Trace:** Filterable order list and detailed order lifecycle view. Surfaces gateway reconciliation states (`SUBMITTING` / `SUBMISSION_UNKNOWN` -> `PLACED`) without unsafe retry buttons, full event timelines linked by `request_id` to `/audit`, and the **How we got here** trace combining policy rules, A2A turns, reasoning steps, and originating web/Telegram `chat_trace` context. |
| `/audit` | **Audit Trail, A2A Inspector & Flight Simulator:** Tamper-evident SHA-256 audit hash-chain verifier, filterable audit event log (`request_id`, entity, actor, action, outcome), **A2A Protocol Trace** state stepper, **Core API vs. Agentic Service Request Router Inspector**, and an interactive 5-scenario **Chaos Flight Simulator** (`unit_trap`, `diet_breach`, `gateway_timeout_recovery`, `partial_and_cutoff`, `tampered_approval_hash`). |
| `/account` | **Company Profile & Settings:** Organization details and delivery branches, direct agent permission links, category brand preferences, human-only management promotions (enforced `403` / read-only for AI agent tokens), and personal **Telegram 1-tap approval pairing** via scannable QR code, deep link, or `@username` with active branch switching. |
| `/admin` | **Platform Admin Console:** Standalone internal console for toggling per-company organization bot preliminary price negotiation (`allow_price_negotiation`) and managing invisible platform ranking campaigns. |

---

## OpenAPI Schema Sync & Localization

### OpenAPI Contract Sync (`src/api/`)

1. Whenever backend Pydantic schemas or routes change, export `backend/openapi.json` and run:
   ```bash
   npm run gen:schema
   ```
2. `src/api/schema.check.ts` is compiled during `npm run typecheck` and `npm run build`. Any breaking drift between `src/api/schema.gen.ts` and the ergonomic frontend domain types in `src/api/types.ts` fails compilation immediately.

### EN / FI Localization (`src/i18n/`)

- All user-facing strings live in `src/i18n/en.ts` (canonical shape `Strings`) and `src/i18n/fi.ts` (`satisfies Strings`).
- TypeScript enforces 100% key and signature parity between English and Finnish at compile time, and `src/i18n/i18n.test.tsx` verifies live language switching in the top bar.

---

## Directory Structure

```text
frontend/
├── public/                 # Static assets & MSW service worker script
├── scripts/
│   └── integration-backend.sh  # Isolated local FastAPI + Vite integration runner
├── src/
│   ├── api/                # Fetch client, endpoints, TanStack Query hooks, OpenAPI types & drift check
│   ├── assistant/          # Session-persisted assistant chat store
│   ├── auth/               # AuthContext, Bearer session storage, safe redirect validation
│   ├── cart/               # Principal-bound CartContext & quote staleness tracking
│   ├── components/         # Shared UI primitives, GovernanceFunnel, QuotePanel, Telegram QR, etc.
│   ├── domain/             # Allergen buckets, Finnish diet code inference, units, temperature zones
│   ├── i18n/               # Complete EN & FI string dictionaries and I18nProvider
│   ├── lib/                # Helsinki date math, EUR/FI formatting, PKZIP + Claude MCP bundle builder
│   ├── mocks/              # Offline MSW v2 handlers, synthetic catalog (data.ts), policy/simulator engine
│   ├── pages/              # Route views and Vitest integration test suites
│   └── test/               # Vitest setup and test rendering helpers
├── .env.example            # Documented public environment variables
├── package.json
├── tailwind.config.ts
├── tsconfig.json
└── vite.config.ts
```
