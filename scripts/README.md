# Aimo Annos — Telegram Customer & Approval Bridge (`scripts/`)

`scripts/telegram_bridge.py` is a single unified `getUpdates` long-polling daemon that connects private Telegram chats to the Aimo Annos Core API without requiring public inbound webhooks or risking Telegram `409 Conflict` polling collisions.

---

## Capabilities

- **1-Scan ISO 18004 QR Code Pairing (`/start link_<token>`):** Buyers and kitchen managers scan a single-use QR code generated in the Web Purchasing Portal (`POST /api/auth/telegram/link-token`) to bind their private Telegram account (`chat_id > 0`) to their organization in one tap.
- **Multi-Branch Kitchen Switching (`/branches`):** Multi-location hospitality groups (hotels, restaurant groups, catering operations) can inspect and switch their active delivery kitchen at any time via `/branches` (or `/branch`) and 1-tap inline buttons (`brn:<branch_id>`).
- **Conversational Purchasing Assistant Parity:** Full parity with the web assistant — natural-language catalog lookup, diet-safe quote drafting, Order Assurance scoring (`50–100`), interactive clarification options (`opt:<nonce>:<i>`), 5-hour platform stock reservation (`HELD_5H`) visibility, and 1-tap quote confirmation (`ord:<nonce>`).
- **1-Tap `content_hash`-Verified Human Approvals (`Approve` / `Reject`):** Pushes real-time approval cards for `PENDING_APPROVAL` orders (plus immediate security review cards if an agent is quarantined). Compact `<=16`-byte callback tokens (`apr:<nonce>`, `rej:<nonce>`, `fix:<nonce>`) resolve to the full 64-character SHA-256 `content_hash` and an `Idempotency-Key`. If an order's lines, prices, or status change before the button is tapped, the Core API refuses with `409 Conflict` and the bridge pushes a refreshed card.
- **1-Tap Pre-Cutoff Order Undo (`cnl:<nonce>`):** Newly placed or pending orders include an inline **Undo / Cancel Order** action enforced deterministically against delivery `closing_time` cut-offs.
- **Strict Private-Chat Recipient Isolation (`100%` Silent Drop):** Group/supergroup/channel updates and messages or button callbacks from unlinked Telegram accounts are silently dropped (`0` outbound messages, `0` state mutations). Unlinking from Telegram (`/unlink`) or disconnecting in the Web Portal immediately revokes session state.
- **Optional Env-Locked Operator Demo Switcher:** Disabled by default (`is_verified_operator` returns `False` whenever operator variables are unset). When an operator explicitly configures both `TELEGRAM_OPERATOR_USERNAME` and `TELEGRAM_OPERATOR_USER_ID`, `/login` exposes a 1-tap switcher for the three seeded demo buyer personas (`anna`, `mikko`, `sari`) alongside `/lead` / `/customer` mode switching.

---

## Environment Variables

Configure variables in `.env` (at the repository root or `backend/.env`) or export them in your shell:

| Variable | Required | Default | Description |
| :--- | :---: | :--- | :--- |
| `TELEGRAM_BOT_TOKEN` | **Yes** | — | Bot API token from `@BotFather` (shared with `backend/.env` so QR pairing and HMAC verification match). Also set `TELEGRAM_BOT_USERNAME` in `backend/.env` to match your bot's handle. |
| `TELEGRAM_BRIDGE_SECRET` | **Yes** *(or auto-derived)* | `HMAC-SHA256(TELEGRAM_BOT_TOKEN, "aimo-tg-bridge-v1")` | Authenticates local bridge requests (`X-Telegram-Bridge-Secret`) to `/api/telegram/*`. Automatically derived when `backend` and `scripts/telegram_bridge.py` share `TELEGRAM_BOT_TOKEN`, or set explicitly. |
| `AIMO_CORE_API_URL` | **Yes** | `http://127.0.0.1:8001` | Base URL of the local Aimo Annos FastAPI backend. |
| `TELEGRAM_OPERATOR_USERNAME` | Optional | `""` *(disabled)* | Telegram username (without `@`) of a local operator allowed to use `/login` demo persona switching. |
| `TELEGRAM_OPERATOR_USER_ID` | Optional | `""` *(disabled)* | Positive numeric Telegram user ID of the operator. Both `TELEGRAM_OPERATOR_USERNAME` and `TELEGRAM_OPERATOR_USER_ID` must be set to enable operator commands. |
| `TELEGRAM_APPROVER_EMAIL` / `TELEGRAM_APPROVER_PASSWORD` | Optional | Seeded demo user | Optional fallback credentials for direct `/api/orders?status=PENDING_APPROVAL` polling. |

Example `.env` snippet:

```bash
TELEGRAM_BOT_TOKEN=<YOUR_BOTFATHER_TOKEN>
AIMO_CORE_API_URL=http://127.0.0.1:8001
# Optional: leave unset to keep operator /login mode disabled
# TELEGRAM_OPERATOR_USERNAME=your_telegram_handle
# TELEGRAM_OPERATOR_USER_ID=10000001
```

---

## Running the Bridge

From the repository root (with the FastAPI backend running on `http://127.0.0.1:8001`):

```bash
# 1. Verify bot token connectivity and recipient lock status
uv run --project backend python scripts/telegram_bridge.py --check

# 2. Start the continuous long-polling listener & approval pusher
uv run --project backend python scripts/telegram_bridge.py listen
```

Runtime state (polling offset, active customer bindings, and short-lived callback nonces) is persisted locally in `.telegram_state.json` (gitignored), and any bot tokens or JWTs are scrubbed from error logs via `redact_secrets(...)`.

---

## Running Tests

The bridge test suite (`scripts/test_telegram_bridge.py`) runs **100% offline** using `httpx.MockTransport` with zero external network requests:

```bash
# Using uv from repository root
uv run --project backend pytest scripts/test_telegram_bridge.py

# Or directly with pytest
pytest scripts/test_telegram_bridge.py
```
