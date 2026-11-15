#!/usr/bin/env python3
"""Telegram Customer & Approval Bridge for Aimo Annos.

Single unified `getUpdates` long-polling daemon (avoiding Telegram 409 Conflict)
with strict fail-closed recipient controls:
1. Optional Team Lead <-> Human Operator channel (`INFORMATIONSHARING.md` + `send`):
   - Disabled by default unless `TELEGRAM_OPERATOR_USERNAME` and `TELEGRAM_OPERATOR_USER_ID`
     are explicitly configured in the environment / `.env`.
   - Strictly locked to the configured operator (`TELEGRAM_OPERATOR_USERNAME` /
     `TELEGRAM_OPERATOR_USER_ID`), verified on inbound updates and via `getChat` before
     every outbound operator `send`.
2. 1-Tap Order Approvals (`PENDING_APPROVAL` push + `✅ Approve` / `❌ Reject`):
   - Sent ONLY to users who have paired via 1-scan QR (`/start link_<token>`) or
     explicitly enabled their Telegram ID / username in the Aimo Annos web portal
     (`GET /api/auth/telegram/recipients`), verified via `getChat` in private chat.
   - Uses a <=12-char server nonce (`apr:<nonce>` = 16 bytes <= Telegram's 64-byte
     `callback_data` cap) bound to the full 64-char SHA-256 `content_hash` +
     `Idempotency-Key` + `note` into Core API `POST /api/orders/{id}/approve` & `/reject`.
3. Silent Drop for Unconsented Users:
   - Never sends any message (not even an error reply) to anyone who is neither
     the configured operator nor explicitly linked/opted-in via the web portal.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import hmac
import json
import math
import mimetypes
import os
import re
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_TELEGRAM_MSG_LEN = 3900
OPERATOR_USERNAME = os.environ.get("TELEGRAM_OPERATOR_USERNAME", "").strip().lstrip("@")
OPERATOR_USER_ID = os.environ.get("TELEGRAM_OPERATOR_USER_ID", "").strip()
BRIDGE_SECRET_MESSAGE = b"aimo-tg-bridge-v1"
BRIDGE_POST_TIMEOUT_SECONDS = 65.0
CUSTOMER_TIMEOUT_FALLBACK_MESSAGE = (
    "Kitchen assistant is taking a moment — please resend your message in a few seconds."
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = REPO_ROOT / ".env"
DEFAULT_DEMO_CREDS_PATH = REPO_ROOT / "backend" / ".demo_credentials"
DEFAULT_INBOX_PATH = REPO_ROOT / "INFORMATIONSHARING.md"
DEFAULT_STATE_PATH = REPO_ROOT / ".telegram_state.json"


def normalize_username(raw: str | None) -> str:
    return (raw or "").strip().lstrip("@").lower()


def load_kv_file(path: Path) -> dict[str, str]:
    """Parse simple KEY=VALUE lines from `.env` or `.demo_credentials`."""
    loaded: dict[str, str] = {}
    if not path.is_file():
        return loaded
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            loaded[key] = val
    return loaded


def load_demo_user_creds(path: Path) -> tuple[str, str]:
    """Load `(email, password)` from JSON or KEY=VALUE `backend/.demo_credentials`."""
    if not path.is_file():
        return ("", "")
    raw = path.read_text(encoding="utf-8").strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            user_obj = data.get("user") if isinstance(data, dict) else None
            if isinstance(user_obj, dict):
                return (
                    str(user_obj.get("email") or "").strip(),
                    str(user_obj.get("password") or "").strip(),
                )
        except json.JSONDecodeError:
            pass
    kv = load_kv_file(path)
    return (kv.get("DEMO_USER_EMAIL", ""), kv.get("DEMO_USER_PASSWORD", ""))


DEMO_LOGIN_PERSONAS: dict[str, tuple[str, str]] = {
    "anna": ("anna.korhonen@hotel.fi", "🏨 Anna · Hotel Aurora (Hilton)"),
    "mikko": ("mikko.laine@restaurant.fi", "🍽 Mikko · Bistro Satama"),
    "sari": ("sari.virtanen@festival.fi", "🎪 Sari · Nordic Campus Catering"),
}


def resolve_default_demo_password(path: Path | None = None) -> str:
    """Resolve default demo password from `backend/.demo_credentials` if present, else `'demo1234'`."""
    target = DEFAULT_DEMO_CREDS_PATH if path is None else path
    _, pwd = load_demo_user_creds(target)
    return pwd or "demo1234"


def build_operator_login_menu_markup() -> dict[str, Any]:
    """Build 1-tap demo buyer persona inline keyboard (`lgn:<key>`, <=64B callback_data)."""
    items = [
        {"text": label, "callback_data": f"lgn:{key}"}
        for key, (_, label) in DEMO_LOGIN_PERSONAS.items()
    ]
    return {
        "inline_keyboard": [items[idx : idx + 2] for idx in range(0, len(items), 2)],
    }


def resolve_setting(name: str, env_vars: dict[str, str], default: str = "") -> str:
    return (os.environ.get(name) or env_vars.get(name) or default).strip()


def resolve_bot_token(env_vars: dict[str, str]) -> str:
    """Support `TELEGRAM_BOT_TOKEN`, `telegram_api_key`, and `TELEGRAM_API_KEY`."""
    for key in ("TELEGRAM_BOT_TOKEN", "telegram_api_key", "TELEGRAM_API_KEY"):
        val = resolve_setting(key, env_vars)
        if val:
            return val
    return ""


def compute_bridge_secret(bot_token: str) -> str:
    """Compute `X-Telegram-Bridge-Secret` HMAC-SHA256 (or explicit `TELEGRAM_BRIDGE_SECRET`)."""
    explicit = os.environ.get("TELEGRAM_BRIDGE_SECRET", "").strip()
    if explicit:
        return explicit
    return hmac.new(
        bot_token.encode("utf-8"),
        BRIDGE_SECRET_MESSAGE,
        hashlib.sha256,
    ).hexdigest()


def clean_telegram_plain_text(raw: str) -> str:
    """Convert Markdown formatting (`**bold**`, `` `code` ``, headings, bullets) into safe plain text."""
    s = raw.strip()
    if not s:
        return ""
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"__(.+?)__", r"\1", s)
    s = re.sub(r"`([^`\n]+)`", r"\1", s)
    s = re.sub(r"(?m)^#{1,6}\s+", "", s)
    s = re.sub(r"(?m)^\s*[-*]\s+", "• ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def is_private_chat(chat_type: str, chat_id: str) -> bool:
    """Ensure Telegram chat is strictly private with positive numeric `chat_id > 0`."""
    if chat_type != "private":
        return False
    try:
        return int(chat_id) > 0
    except (TypeError, ValueError):
        return False


def redact_secrets(text: str, *secret_values: str) -> str:
    """Scrub Telegram bot tokens (including `/bot<token>` URLs), bridge HMACs, JWTs, and explicit secrets."""
    out = text
    for sec in secret_values:
        if sec and len(sec) >= 8:
            out = out.replace(sec, "[REDACTED_TELEGRAM_SECRET]")
            out = out.replace(compute_bridge_secret(sec), "[REDACTED_BRIDGE_SECRET]")
    out = re.sub(
        r"(?:(?<=bot)|(?<![A-Za-z0-9]))\d{6,15}:[A-Za-z0-9_-]{25,}",
        "[REDACTED_TELEGRAM_TOKEN]",
        out,
    )
    out = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED_JWT]", out)
    return out


def chunk_message(text: str, max_len: int = MAX_TELEGRAM_MSG_LEN) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    if len(stripped) <= max_len:
        return [stripped]
    chunks: list[str] = []
    rem = stripped
    while len(rem) > max_len:
        split_at = rem.rfind("\n", 0, max_len)
        if split_at <= max_len // 3:
            split_at = max_len
        chunks.append(rem[:split_at].rstrip())
        rem = rem[split_at:].lstrip("\n")
    if rem:
        chunks.append(rem)
    return chunks


class TelegramStateStore:
    """Persist `getUpdates` offset, verified operator chat, customer chat bindings, and nonces."""

    def __init__(self, path: Path = DEFAULT_STATE_PATH) -> None:
        self.path = path
        self.update_offset: int = 0
        self.operator_chat_id: str | None = None
        self.operator_user_id: str | None = None
        self.operator_mode: str = "lead"
        self.seen_update_ids: list[int] = []
        # Map normalized username or numeric user_id -> private chat_id (recorded ONLY after auth/opt-in check)
        self.verified_chats: dict[str, str] = {}
        # Map private str(chat_id) -> {telegram_user_id, company_name, branches, active_branch, ...}
        self.customer_chats: dict[str, dict[str, Any]] = {}
        # Map 12-char nonce -> {order_id, content_hash, total_excl_vat_eur, created_at}
        self.approval_nonces: dict[str, dict[str, Any]] = {}
        # Map 12-char nonce -> {chat_id, user_id, quote_id, total_eur, created_at}
        self.customer_order_nonces: dict[str, dict[str, Any]] = {}
        # Map 12-char nonce -> {chat_id, user_id, options, created_at}
        self.customer_option_nonces: dict[str, dict[str, Any]] = {}
        # Map 12-char nonce -> {order_id, chat_id, user_id, created_at}
        self.customer_cancel_nonces: dict[str, dict[str, Any]] = {}
        # Map f"{order_id}:{chat_id}" -> content_hash last pushed to that opted-in chat
        self.notified_pending_orders: dict[str, str] = {}
        # Watermark & dedup map for multi-company `/api/telegram/order-notifications`
        self.order_notifications_since: str | None = None
        self.seen_order_notifications: dict[str, str] = {}
        self._load()

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and hasattr(self, key)

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        self.update_offset = int(data.get("update_offset") or 0)
        op_chat = data.get("operator_chat_id")
        self.operator_chat_id = str(op_chat) if op_chat else None
        op_uid = data.get("operator_user_id")
        self.operator_user_id = str(op_uid) if op_uid else None
        raw_mode = str(data.get("operator_mode") or "lead").strip().lower()
        self.operator_mode = raw_mode if raw_mode in ("lead", "customer") else "lead"
        seen = data.get("seen_update_ids")
        if isinstance(seen, list):
            self.seen_update_ids = [int(x) for x in seen[-500:] if str(x).isdigit()]
        vchats = data.get("verified_chats")
        if isinstance(vchats, dict):
            self.verified_chats = {str(k): str(v) for k, v in list(vchats.items())[-300:]}
        cchats = data.get("customer_chats")
        if isinstance(cchats, dict):
            self.customer_chats = {
                str(k): v for k, v in list(cchats.items())[-300:] if isinstance(v, dict)
            }
        nonces = data.get("approval_nonces")
        if isinstance(nonces, dict):
            self.approval_nonces = {
                str(k): v for k, v in list(nonces.items())[-300:] if isinstance(v, dict)
            }
        ord_nonces = data.get("customer_order_nonces")
        if isinstance(ord_nonces, dict):
            self.customer_order_nonces = {
                str(k): v for k, v in list(ord_nonces.items())[-300:] if isinstance(v, dict)
            }
        opt_nonces = data.get("customer_option_nonces")
        if isinstance(opt_nonces, dict):
            self.customer_option_nonces = {
                str(k): v for k, v in list(opt_nonces.items())[-300:] if isinstance(v, dict)
            }
        cnl_nonces = data.get("customer_cancel_nonces")
        if isinstance(cnl_nonces, dict):
            self.customer_cancel_nonces = {
                str(k): v for k, v in list(cnl_nonces.items())[-300:] if isinstance(v, dict)
            }
        notified = data.get("notified_pending_orders")
        if isinstance(notified, dict):
            self.notified_pending_orders = {
                str(k): str(v) for k, v in list(notified.items())[-500:]
            }
        raw_since = data.get("order_notifications_since")
        self.order_notifications_since = (
            str(raw_since).strip() if isinstance(raw_since, str) and raw_since.strip() else None
        )
        seen_notifs = data.get("seen_order_notifications")
        if isinstance(seen_notifs, dict):
            self.seen_order_notifications = {
                str(k): str(v) for k, v in list(seen_notifs.items())[-500:]
            }

    def save(self) -> None:
        payload = {
            "update_offset": self.update_offset,
            "operator_chat_id": self.operator_chat_id,
            "operator_user_id": self.operator_user_id,
            "operator_mode": self.operator_mode,
            "seen_update_ids": self.seen_update_ids[-500:],
            "verified_chats": dict(list(self.verified_chats.items())[-300:]),
            "customer_chats": dict(list(self.customer_chats.items())[-300:]),
            "approval_nonces": dict(list(self.approval_nonces.items())[-300:]),
            "customer_order_nonces": dict(list(self.customer_order_nonces.items())[-300:]),
            "customer_option_nonces": dict(list(self.customer_option_nonces.items())[-300:]),
            "customer_cancel_nonces": dict(list(self.customer_cancel_nonces.items())[-300:]),
            "notified_pending_orders": dict(list(self.notified_pending_orders.items())[-500:]),
            "order_notifications_since": self.order_notifications_since,
            "seen_order_notifications": dict(list(self.seen_order_notifications.items())[-500:]),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def mark_update(self, update_id: int) -> bool:
        """Record `update_id` for deduplication ONLY (never mutates any recipient `chat_id`)."""
        if update_id in self.seen_update_ids:
            if update_id >= self.update_offset:
                self.update_offset = update_id + 1
                self.save()
            return False
        self.seen_update_ids.append(update_id)
        if len(self.seen_update_ids) > 500:
            self.seen_update_ids = self.seen_update_ids[-500:]
        if update_id >= self.update_offset:
            self.update_offset = update_id + 1
        self.save()
        return True

    def record_operator_chat(self, *, chat_id: str, user_id: str) -> None:
        """Store configured operator's private chat_id ONLY after operator verification passes."""
        op_u = normalize_username(OPERATOR_USERNAME)
        if chat_id and op_u:
            self.operator_chat_id = str(chat_id)
            self.operator_user_id = str(user_id)
            self.verified_chats[f"@{op_u}"] = str(chat_id)
            if user_id:
                self.verified_chats[str(user_id)] = str(chat_id)
            self.save()

    def record_opted_in_chat(self, *, chat_id: str, user_id: str, username: str) -> None:
        """Store an opted-in website user's private chat_id ONLY after opt-in match passes."""
        if not chat_id:
            return
        norm_u = normalize_username(username)
        if norm_u:
            self.verified_chats[f"@{norm_u}"] = str(chat_id)
        if user_id:
            self.verified_chats[str(user_id)] = str(chat_id)
        self.save()

    def register_approval_nonce(
        self,
        *,
        order_id: str,
        content_hash: str,
        total_excl_vat_eur: float | str | None,
        via_bridge: bool = False,
    ) -> str:
        """Create a <=12-char nonce so `apr:<nonce>` = 16 bytes (well under Telegram's 64-byte cap)."""
        for existing_nonce, meta in self.approval_nonces.items():
            if meta.get("order_id") == order_id and meta.get("content_hash") == content_hash:
                if via_bridge and not meta.get("via_bridge"):
                    meta["via_bridge"] = True
                    self.save()
                return existing_nonce
        nonce = secrets.token_hex(6)
        entry: dict[str, Any] = {
            "order_id": order_id,
            "content_hash": content_hash,
            "total_excl_vat_eur": total_excl_vat_eur,
            "created_at": datetime.now(UTC).isoformat(),
        }
        if via_bridge:
            entry["via_bridge"] = True
        self.approval_nonces[nonce] = entry
        self.save()
        return nonce

    def register_customer_order_nonce(
        self,
        *,
        chat_id: str,
        user_id: str,
        quote_id: str,
        total_eur: float | None = None,
    ) -> str:
        """Create a <=12-char nonce so `ord:<nonce>` = 16 bytes (bound to `chat_id` & `quote_id`)."""
        for existing_nonce, meta in self.customer_order_nonces.items():
            if meta.get("chat_id") == str(chat_id) and meta.get("quote_id") == str(quote_id):
                return existing_nonce
        nonce = secrets.token_hex(6)
        self.customer_order_nonces[nonce] = {
            "chat_id": str(chat_id),
            "user_id": str(user_id),
            "quote_id": str(quote_id),
            "total_eur": total_eur,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.save()
        return nonce

    def register_customer_options_nonce(
        self,
        *,
        chat_id: str,
        user_id: str,
        options: list[str],
    ) -> str:
        """Create a <=12-char nonce so `opt:<nonce>:<i>` = 18 bytes (bound to `chat_id`)."""
        nonce = secrets.token_hex(6)
        self.customer_option_nonces[nonce] = {
            "chat_id": str(chat_id),
            "user_id": str(user_id),
            "options": [str(o) for o in options[:4]],
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.save()
        return nonce

    def register_customer_cancel_nonce(
        self,
        *,
        order_id: str,
        chat_id: str,
        user_id: str,
    ) -> str:
        """Create a <=12-char nonce so `cnl:<nonce>` = 16 bytes (bound to `order_id` & `chat_id`)."""
        for existing_nonce, meta in self.customer_cancel_nonces.items():
            if meta.get("chat_id") == str(chat_id) and meta.get("order_id") == str(order_id):
                return existing_nonce
        nonce = secrets.token_hex(6)
        self.customer_cancel_nonces[nonce] = {
            "order_id": str(order_id),
            "chat_id": str(chat_id),
            "user_id": str(user_id),
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.save()
        return nonce


def match_opted_in_recipient(
    *,
    user_id: str,
    username: str,
    chat_id: str,
    recipients: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return matching website opt-in record if `enabled` is true and ID/username matches."""
    norm_u = normalize_username(username)
    for rec in recipients:
        if not isinstance(rec, dict) or not rec.get("enabled", False):
            continue
        rec_u = normalize_username(str(rec.get("telegram_username") or ""))
        rec_uid = str(rec.get("telegram_user_id") or "").strip()
        rec_cid = str(rec.get("telegram_chat_id") or rec.get("telegram_id") or "").strip().lstrip("@")
        if norm_u and rec_u and norm_u == rec_u:
            return rec
        if norm_u and rec_cid and norm_u == rec_cid.lower():
            return rec
        if user_id and rec_uid and user_id == rec_uid:
            return rec
        if user_id and rec_cid and user_id == rec_cid:
            return rec
        if chat_id and rec_cid and chat_id == rec_cid:
            return rec
    return None


def _is_operator_customer_chat(
    chat_id: str,
    cust_entry: dict[str, Any],
    state: TelegramStateStore,
) -> bool:
    """Return True if `customer_chats[chat_id]` belongs to the configured operator (`TELEGRAM_OPERATOR_USERNAME` / `TELEGRAM_OPERATOR_USER_ID`)."""
    cid = str(chat_id).strip()
    uid = str(cust_entry.get("telegram_user_id") or "").strip()
    uname = normalize_username(str(cust_entry.get("telegram_username") or ""))
    op_u = normalize_username(OPERATOR_USERNAME)
    op_uid = OPERATOR_USER_ID.strip()
    if op_u and uname and uname == op_u:
        return True
    if op_uid and (cid == op_uid or (uid and uid == op_uid)):
        return True
    if state.operator_chat_id and cid == str(state.operator_chat_id):
        return True
    return bool(state.operator_user_id and uid and uid == str(state.operator_user_id))


def prune_stale_customer_chats(
    state: TelegramStateStore,
    recipients: list[dict[str, Any]],
) -> list[str]:
    """Prune any non-operator `chat_id` in `state.customer_chats` no longer in `opted_in_recipients`."""
    pruned: list[str] = []
    for cid, entry in list(state.customer_chats.items()):
        if not isinstance(entry, dict):
            state.customer_chats.pop(cid, None)
            pruned.append(str(cid))
            continue
        if _is_operator_customer_chat(str(cid), entry, state):
            continue
        uid = str(entry.get("telegram_user_id") or cid).strip()
        uname = normalize_username(str(entry.get("telegram_username") or ""))
        if (
            match_opted_in_recipient(
                user_id=uid,
                username=uname,
                chat_id=str(cid),
                recipients=recipients,
            )
            is None
        ):
            state.customer_chats.pop(cid, None)
            if uname:
                state.verified_chats.pop(f"@{uname}", None)
            if uid:
                state.verified_chats.pop(uid, None)
            pruned.append(str(cid))
    if pruned:
        state.save()
    return pruned


def resolve_recipient_chat_id(rec: dict[str, Any], state: TelegramStateStore) -> str:
    """Resolve private numeric `chat_id` for a website-opted-in recipient."""
    raw_cid = str(rec.get("telegram_chat_id") or "").strip()
    if raw_cid.lstrip("-").isdigit() and not raw_cid.startswith("-"):
        return raw_cid
    raw_id = str(rec.get("telegram_id") or "").strip()
    if raw_id.isdigit():
        return state.verified_chats.get(raw_id, raw_id)
    rec_u = normalize_username(str(rec.get("telegram_username") or raw_id))
    if rec_u:
        return state.verified_chats.get(f"@{rec_u}", "")
    return ""


HARD_LOCK_RULE_IDS = {
    "R-HARD-DIET",
    "R-NOT-DELIVERABLE",
    "R-SALES-BLOCKED",
    "R-AGENT-NO-ORDER",
    "R-MAX-VALUE",
}
PLANT_BASED_HINTS = ("oat", "kaura", "soy", "soija", "plant", "kasvi")


def _extract_recipe_expected_qty_map(order: dict[str, Any]) -> dict[str, int]:
    """Extract per-SKU right-sized integer quantities from `recipe_sanity_warning`."""
    policy = order.get("policy") or {}
    warn = order.get("recipe_sanity_warning") or policy.get("recipe_sanity_warning")
    if not isinstance(warn, dict):
        return {}
    result: dict[str, int] = {}
    entries = warn.get("offending_lines") or warn.get("items") or warn.get("lines") or []
    if isinstance(entries, list):
        for item in entries:
            if not isinstance(item, dict):
                continue
            pid = str(item.get("product_id") or "").strip()
            exp = item.get("expected_qty")
            if exp is None:
                exp = item.get("expected_quantity")
            if pid and isinstance(exp, (int, float)) and exp > 0:
                result[pid] = max(1, math.ceil(float(exp)))
    top_pid = str(warn.get("product_id") or "").strip()
    top_exp = warn.get("expected_qty") or warn.get("expected_quantity")
    if top_pid and isinstance(top_exp, (int, float)) and top_exp > 0:
        result[top_pid] = max(1, math.ceil(float(top_exp)))
    return result


def infer_order_rescue_kind(order: dict[str, Any]) -> str | None:
    """Return Gate-2 1-tap safe rescue kind, or None if ineligible."""
    status = str(order.get("status") or "PENDING_APPROVAL")
    if status != "PENDING_APPROVAL":
        return None
    policy = order.get("policy") or {}
    if str(policy.get("outcome") or "") == "BLOCKED":
        return None
    rules = policy.get("rules") or []
    fired_ids = {
        str(r.get("rule_id") or "")
        for r in rules
        if isinstance(r, dict) and str(r.get("outcome") or "") != "AUTO"
    }
    if fired_ids & HARD_LOCK_RULE_IDS:
        return None
    lines = [ln for ln in (order.get("lines") or []) if isinstance(ln, dict)]
    if not lines:
        return None
    for ln in lines:
        flags = {str(f) for f in (ln.get("flags") or [])}
        if flags & {"UNIT_MISMATCH", "NOT_DELIVERABLE", "NO_PRICE", "SALES_BLOCKED"}:
            return None

    if "R-RECIPE-SANITY" in fired_ids and _extract_recipe_expected_qty_map(order):
        return "ADJUST_TO_RECIPE_YIELD"

    risky_lines = [
        ln
        for ln in lines
        if str(ln.get("delivery_status") or "") != "ON_TIME"
        or ({str(f) for f in (ln.get("flags") or [])} & {"PARTIAL", "CUTOFF_PASSED", "LATE"})
    ]
    if "R-DELIVERY-RISK" in fired_ids and 1 <= len(risky_lines) <= 3:
        return "SWAP_TO_VERIFIED_SUBSTITUTE"
    return None


def format_approval_card(order: dict[str, Any], nonce: str) -> tuple[str, dict[str, Any]]:
    """Build Telegram text + InlineKeyboardMarkup for a PENDING_APPROVAL order."""
    order_id = str(order.get("id") or "ord_unknown")
    total = order.get("total_excl_vat_eur")
    total_str = f"€{float(total):,.2f}" if isinstance(total, (int, float)) else str(total)
    delivery_date = order.get("requested_delivery_date") or "—"
    diet = order.get("diet") or "none"
    created_by = order.get("created_by") or {}
    actor_label = f"{created_by.get('name') or created_by.get('id')} ({created_by.get('kind', 'agent')})"
    content_hash = str(order.get("content_hash") or "")
    short_hash = f"{content_hash[:12]}…{content_hash[-6:]}" if len(content_hash) >= 20 else content_hash

    policy = order.get("policy") or {}
    rules = policy.get("rules") or []
    rule_ids = [
        str(r.get("rule_id"))
        for r in rules
        if isinstance(r, dict) and r.get("outcome") != "AUTO"
    ]
    rules_summary = ", ".join(rule_ids) if rule_ids else str(policy.get("outcome") or "APPROVAL_REQUIRED")

    recipe_warn = order.get("recipe_sanity_warning") or policy.get("recipe_sanity_warning")
    recipe_context_block = ""
    if isinstance(recipe_warn, dict) and recipe_warn.get("triggered", True):
        r_name = recipe_warn.get("recipe_name") or recipe_warn.get("recipe_id") or "Recipe"
        r_pax = recipe_warn.get("target_pax") or "—"
        summary = str(recipe_warn.get("message") or recipe_warn.get("summary") or "").strip()
        recipe_context_block = f"⚠️ Recipe Sanity ({r_name}, {r_pax} pax): {summary}\n"
    else:
        for r in rules:
            if isinstance(r, dict) and str(r.get("rule_id") or "") == "R-RECIPE-SANITY":
                msg_reason = str(r.get("message") or r.get("reason") or "").strip()
                if msg_reason:
                    recipe_context_block = f"⚠️ Recipe Sanity: {msg_reason}\n"
                    break

    res_status = str(order.get("reservation_status") or "").strip()
    res_expires_raw = str(order.get("reservation_expires_at") or "").strip()
    res_expires_short = res_expires_raw
    if res_expires_raw:
        try:
            dt_exp = datetime.fromisoformat(res_expires_raw)
            res_expires_short = dt_exp.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            res_expires_short = res_expires_raw
    warn_cnt_raw = order.get("company_warning_count_7d")
    warn_cnt = int(warn_cnt_raw) if isinstance(warn_cnt_raw, (int, float)) else 0
    warn_msg = str(order.get("reservation_warning_message") or "").strip()

    reservation_block = ""
    if res_status == "HELD_5H":
        until_part = f" until {res_expires_short}" if res_expires_short else ""
        reservation_block += f"🔒 Stock Hold: Platform stock reserved{until_part} (5h hold)\n"
    elif res_status == "HOLD_EXPIRED":
        reservation_block += "⏳ Stock Hold: 5h hold expired — places on approve if still available\n"
    elif res_status == "UNRESERVED_DUE_TO_WARNINGS":
        detail = warn_msg or "Stock hold disabled (repeated expired approvals) — places on approve only if stock remains"
        reservation_block += f"⚠️ Stock Hold Suspended: {detail}\n"
    if warn_cnt > 0 and res_status != "UNRESERVED_DUE_TO_WARNINGS":
        detail = warn_msg or f"Hanging-order warnings (7d): {warn_cnt}/3 — reject or approve within 5h to keep stock holds"
        reservation_block += f"⚠️ Fair-Use Warning ({warn_cnt}/3): {detail}\n"

    lines = order.get("lines") or []
    line_preview = []
    for ln in lines[:5]:
        if isinstance(ln, dict):
            pid = ln.get("product_id")
            name = ln.get("product_name") or ln.get("name") or pid
            qty = ln.get("quantity")
            unit = ln.get("sales_unit") or ""
            status = ln.get("delivery_status") or ""
            line_preview.append(f"  • {pid} {name}: {qty} {unit} [{status}]")
    if len(lines) > 5:
        line_preview.append(f"  • (+{len(lines) - 5} more lines)")

    text = (
        f"🛎 AimoAnnos Approval Required\n"
        f"Order: {order_id}\n"
        f"Actor: {actor_label}\n"
        f"Total (excl. VAT): {total_str}\n"
        f"Delivery: {delivery_date} · Diet: {diet}\n"
        f"Fired Rules: {rules_summary}\n"
        + recipe_context_block
        + reservation_block
        + f"SHA-256 Hash: {short_hash}\n"
        + ("\n".join(line_preview) + "\n" if line_preview else "")
        + "\nTap below to verify content_hash & decide:"
    )
    apr_cb = f"apr:{nonce}"
    rej_cb = f"rej:{nonce}"
    assert len(apr_cb.encode("utf-8")) <= 64 and len(rej_cb.encode("utf-8")) <= 64
    keyboard_rows: list[list[dict[str, str]]] = [
        [
            {"text": f"✅ Approve ({total_str})", "callback_data": apr_cb},
            {"text": "❌ Reject", "callback_data": rej_cb},
        ]
    ]
    fix_kind = infer_order_rescue_kind(order)
    if fix_kind is not None:
        fix_cb = f"fix:{nonce}"
        assert len(fix_cb.encode("utf-8")) <= 64
        fix_label = (
            "🔧 Right-Size to Recipe & Place"
            if fix_kind == "ADJUST_TO_RECIPE_YIELD"
            else "🔧 Safe Swap (ON_TIME) & Place"
        )
        keyboard_rows.append([{"text": fix_label, "callback_data": fix_cb}])
    reply_markup = {"inline_keyboard": keyboard_rows}
    return text, reply_markup


def format_inbox_entry(
    *,
    username: str,
    user_id: str,
    chat_id: str,
    message_id: str,
    content: str,
    now_utc: datetime | None = None,
) -> str:
    ts = (now_utc or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"\n\n### [{ts}] FROM: Human (Telegram @{username} · tg_user={user_id}"
        f" · chat={chat_id} · msg={message_id}) -> TO: Team Lead · [OPEN]\n"
        f"**Topic:** Telegram message from @{username}\n"
        f"{content.strip()}\n"
    )


class TelegramBotClient:
    """Thin async client for Telegram Bot API (`getUpdates`, `getChat`, `sendMessage`, callbacks)."""

    def __init__(self, token: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._token = token
        effective_transport = (
            transport
            if transport is not None
            else httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        )
        self._client = httpx.AsyncClient(
            base_url=f"{TELEGRAM_API_BASE}/bot{token}",
            timeout=25.0,
            transport=effective_transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = await self._client.post(f"/{method}", json=payload or {})
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(0.8 * (attempt + 1))
                    continue
                resp.raise_for_status()
                body = resp.json()
                if not body.get("ok"):
                    raise RuntimeError(f"Telegram {method} error: {body.get('description')}")
                return body.get("result")
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(0.8 * (attempt + 1))
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    async def get_me(self) -> dict[str, Any]:
        return await self._call("getMe")

    async def get_chat(self, chat_id: str) -> dict[str, Any]:
        res = await self._call("getChat", {"chat_id": chat_id})
        return res if isinstance(res, dict) else {}

    async def download_file(
        self,
        file_id: str,
        dest_dir: Path,
        *,
        prefix: str = "",
        fallback_name: str = "attachment.bin",
    ) -> Path:
        """Download a Telegram `photo`/`document` `file_id` into gitignored `dest_dir`."""
        info = await self._call("getFile", {"file_id": file_id})
        rel_path = str((info or {}).get("file_path") or "")
        if not rel_path:
            raise RuntimeError(f"Telegram getFile returned empty file_path for {file_id!r}")
        raw_name = Path(rel_path).name or fallback_name
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name)
        dest_dir.mkdir(parents=True, exist_ok=True)
        out_path = dest_dir / f"{prefix}{safe_name}"
        file_url = f"{TELEGRAM_API_BASE}/file/bot{self._token}/{rel_path.lstrip('/')}"
        resp = await self._client.get(file_url)
        resp.raise_for_status()
        out_path.write_bytes(resp.content)
        return out_path

    async def get_updates(self, *, offset: int = 0, timeout: int = 20) -> list[dict[str, Any]]:
        res = await self._call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": ["message", "callback_query"],
            },
        )
        return res if isinstance(res, list) else []

    async def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to_message_id: int | str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        sent: list[dict[str, Any]] = []
        chunks = chunk_message(text)
        for idx, chunk in enumerate(chunks):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if idx == 0 and reply_to_message_id is not None and str(reply_to_message_id).isdigit():
                payload["reply_parameters"] = {
                    "message_id": int(reply_to_message_id),
                    "allow_sending_without_reply": True,
                }
            if idx == len(chunks) - 1 and reply_markup is not None:
                payload["reply_markup"] = reply_markup
            res = await self._call("sendMessage", payload)
            if isinstance(res, dict):
                sent.append(res)
        return sent

    async def send_verified_operator_message(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to_message_id: int | str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Verify via `getChat` that `chat_id` matches the configured operator (`TELEGRAM_OPERATOR_USERNAME`) BEFORE sending."""
        op_u = normalize_username(OPERATOR_USERNAME)
        if not op_u:
            raise PermissionError(
                "Refused outbound operator message: TELEGRAM_OPERATOR_USERNAME is not configured."
            )
        chat = await self.get_chat(chat_id)
        chat_type = str(chat.get("type") or "")
        chat_user = normalize_username(str(chat.get("username") or ""))
        if chat_type != "private" or chat_user != op_u:
            raise PermissionError(
                f"Refused outbound Team Lead message: target chat {chat_id!r} resolved to "
                f"type={chat_type!r}, username=@{chat_user!r} (only private @{op_u} allowed)."
            )
        return await self.send_message(
            chat_id,
            text,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup,
        )

    async def send_photo(
        self,
        chat_id: str,
        photo_path: Path,
        *,
        caption: str = "",
        reply_to_message_id: int | str | None = None,
    ) -> dict[str, Any]:
        """Upload a photo via multipart `sendPhoto` with 3-attempt retry."""
        if not photo_path.is_file():
            raise FileNotFoundError(f"Photo file not found: {photo_path}")
        photo_bytes = photo_path.read_bytes()
        mime_type = mimetypes.guess_type(photo_path.name)[0] or "image/png"
        data: dict[str, str] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption[:1024]
        if reply_to_message_id is not None and str(reply_to_message_id).isdigit():
            data["reply_parameters"] = json.dumps(
                {
                    "message_id": int(reply_to_message_id),
                    "allow_sending_without_reply": True,
                }
            )
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = await self._client.post(
                    "/sendPhoto",
                    data=data,
                    files={"photo": (photo_path.name, photo_bytes, mime_type)},
                )
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(0.8 * (attempt + 1))
                    continue
                resp.raise_for_status()
                body = resp.json()
                if not body.get("ok"):
                    raise RuntimeError(f"Telegram sendPhoto error: {body.get('description')}")
                res = body.get("result")
                return res if isinstance(res, dict) else {}
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(0.8 * (attempt + 1))
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    async def send_verified_operator_photo(
        self,
        chat_id: str,
        photo_path: Path,
        *,
        caption: str = "",
        reply_to_message_id: int | str | None = None,
    ) -> dict[str, Any]:
        """Verify via `getChat` that `chat_id` matches the configured operator (`TELEGRAM_OPERATOR_USERNAME`) BEFORE uploading photo."""
        op_u = normalize_username(OPERATOR_USERNAME)
        if not op_u:
            raise PermissionError(
                "Refused outbound operator photo: TELEGRAM_OPERATOR_USERNAME is not configured."
            )
        chat = await self.get_chat(chat_id)
        chat_type = str(chat.get("type") or "")
        chat_user = normalize_username(str(chat.get("username") or ""))
        if chat_type != "private" or chat_user != op_u:
            raise PermissionError(
                f"Refused outbound Team Lead photo: target chat {chat_id!r} resolved to "
                f"type={chat_type!r}, username=@{chat_user!r} (only private @{op_u} allowed)."
            )
        return await self.send_photo(
            chat_id,
            photo_path,
            caption=caption,
            reply_to_message_id=reply_to_message_id,
        )

    async def edit_message_text(self, chat_id: str, message_id: int, text: str) -> None:
        await self._call(
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": text[:MAX_TELEGRAM_MSG_LEN]},
        )

    async def answer_callback_query(
        self, callback_query_id: str, text: str, show_alert: bool = False
    ) -> None:
        await self._call(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_query_id,
                "text": text[:195],
                "show_alert": show_alert,
            },
        )

    async def set_reaction_eyes(self, chat_id: str, message_id: int | str) -> None:
        if not str(message_id).isdigit():
            return
        with contextlib.suppress(httpx.HTTPError, RuntimeError):
            await self._call(
                "setMessageReaction",
                {
                    "chat_id": chat_id,
                    "message_id": int(message_id),
                    "reaction": [{"type": "emoji", "emoji": "👀"}],
                },
            )


class AimoCoreApiClient:
    """Authenticated human (`CurrentUser`) & HMAC bridge client to AimoAnnos Core API."""

    def __init__(
        self,
        base_url: str,
        *,
        email: str = "",
        password: str = "",
        bearer_token: str = "",
        bot_token: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._jwt = bearer_token
        self._bot_token = bot_token
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=15.0,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def configured(self) -> bool:
        return bool(self._jwt or (self._email and self._password))

    async def _ensure_jwt(self, *, force: bool = False) -> str:
        if self._jwt and not force:
            return self._jwt
        if not (self._email and self._password):
            raise RuntimeError(
                "Human approver credentials not configured (set TELEGRAM_APPROVER_EMAIL / "
                "TELEGRAM_APPROVER_PASSWORD in .env or ensure backend/.demo_credentials exists)."
            )
        resp = await self._client.post(
            "/api/auth/login",
            json={"email": self._email, "password": self._password},
        )
        resp.raise_for_status()
        data = resp.json()
        tok = data.get("token") or data.get("access_token")
        if not tok:
            raise RuntimeError("Login response missing 'token'")
        self._jwt = str(tok)
        return self._jwt

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        tok = await self._ensure_jwt(force=False)
        req_headers = {"Authorization": f"Bearer {tok}"}
        if headers:
            req_headers.update(headers)
        resp = await self._client.request(method, path, json=json_body, headers=req_headers)
        if resp.status_code == 401 and self._email and self._password:
            tok = await self._ensure_jwt(force=True)
            req_headers["Authorization"] = f"Bearer {tok}"
            resp = await self._client.request(method, path, json=json_body, headers=req_headers)
        return resp

    async def login_and_mint_link_token(self, *, email: str, password: str) -> httpx.Response:
        """Authenticate via `/api/auth/login` and mint a `/api/auth/telegram/link-token`."""
        login_resp = await self._client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
        )
        if login_resp.status_code != 200:
            return login_resp
        data = login_resp.json() if login_resp.content else {}
        tok = (data.get("token") or data.get("access_token")) if isinstance(data, dict) else None
        if not tok:
            return httpx.Response(
                502,
                json={"detail": "Login response missing token"},
                request=login_resp.request,
            )
        return await self._client.post(
            "/api/auth/telegram/link-token",
            headers={"Authorization": f"Bearer {tok}"},
        )

    async def list_opted_in_telegram_recipients(
        self,
        *,
        state: TelegramStateStore | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch company-scoped users who explicitly added & enabled Telegram in the website clone."""
        if not self.configured:
            return []
        try:
            resp = await self._request("GET", "/api/auth/telegram/recipients")
        except httpx.HTTPError:
            return []
        if resp.status_code == 404:
            # Endpoint not yet deployed or 404 -> fail closed (0 demo recipients) & prune stale non-op chats
            if state is not None:
                prune_stale_customer_chats(state, [])
            return []
        resp.raise_for_status()
        body = resp.json()
        items = body.get("items") if isinstance(body, dict) else body
        if not isinstance(items, list):
            return []
        active = [r for r in items if isinstance(r, dict) and r.get("enabled") is True]
        if state is not None:
            prune_stale_customer_chats(state, active)
        return active

    async def bind_verified_chat_id(
        self,
        *,
        telegram_user_id: str,
        telegram_username: str,
        telegram_chat_id: str,
    ) -> None:
        """Best-effort sync of verified private `chat_id` back to website opt-in record."""
        if not self.configured:
            return
        try:
            await self._request(
                "POST",
                "/api/auth/telegram/verify-chat",
                json_body={
                    "telegram_user_id": telegram_user_id,
                    "telegram_username": normalize_username(telegram_username),
                    "telegram_chat_id": telegram_chat_id,
                },
            )
        except httpx.HTTPError:
            pass

    async def list_quarantined_agents(self) -> list[dict[str, Any]]:
        """Fetch company-scoped quarantined/anomalous agents awaiting human review (`404` -> `[]`)."""
        if not self.configured:
            return []
        try:
            resp = await self._request("GET", "/api/agents/anomalies")
        except httpx.HTTPError:
            return []
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        body = resp.json()
        items = body.get("items") if isinstance(body, dict) else body
        return [a for a in items if isinstance(a, dict)] if isinstance(items, list) else []

    async def review_quarantined_agent(
        self,
        *,
        agent_id: str,
        decision: str,
        tg_user_id: str,
        tg_username: str,
    ) -> httpx.Response:
        return await self._request(
            "POST",
            f"/api/agents/{agent_id}/review",
            json_body={
                "action": decision,
                "note": f"Reviewed ({decision}) via Telegram by @{tg_username} (tg_user_id={tg_user_id})",
            },
        )

    async def list_pending_orders(self) -> list[dict[str, Any]]:
        resp = await self._request("GET", "/api/orders?status=PENDING_APPROVAL&limit=20")
        resp.raise_for_status()
        items = resp.json().get("items")
        return items if isinstance(items, list) else []

    async def get_order(self, order_id: str) -> dict[str, Any] | None:
        resp = await self._request("GET", f"/api/orders/{order_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def approve_order(
        self,
        *,
        order_id: str,
        content_hash: str,
        nonce: str,
        tg_user_id: str,
        tg_username: str,
    ) -> httpx.Response:
        return await self._request(
            "POST",
            f"/api/orders/{order_id}/approve",
            json_body={
                "content_hash": content_hash,
                "note": f"Approved via Telegram by @{tg_username} (tg_user_id={tg_user_id})",
            },
            headers={"Idempotency-Key": f"tg_apr_{nonce}"},
        )

    async def reject_order(
        self,
        *,
        order_id: str,
        nonce: str,
        tg_user_id: str,
        tg_username: str,
        note_override: str | None = None,
    ) -> httpx.Response:
        return await self._request(
            "POST",
            f"/api/orders/{order_id}/reject",
            json_body={
                "note": note_override
                or f"Rejected via Telegram by @{tg_username} (tg_user_id={tg_user_id})",
            },
            headers={"Idempotency-Key": f"tg_rej_{nonce}"},
        )

    async def _resolve_effective_order_diet(self, order: dict[str, Any]) -> str:
        """Merge `order.diet` with owning agent's `hard_diet`/`diet`."""
        parts: list[str] = []
        raw_order_diet = str(order.get("diet") or "").strip()
        if raw_order_diet:
            parts.extend(p.strip() for p in raw_order_diet.split(",") if p.strip())
        created_by = order.get("created_by") or {}
        if created_by.get("kind") == "agent" and created_by.get("id"):
            try:
                resp = await self._request("GET", "/api/agents")
                if resp.status_code == 200:
                    items = resp.json().get("items") or []
                    for ag in items:
                        if isinstance(ag, dict) and str(ag.get("id") or "") == str(created_by.get("id")):
                            ag_diet = str(ag.get("hard_diet") or ag.get("diet") or "").strip()
                            if ag_diet:
                                parts.extend(p.strip() for p in ag_diet.split(",") if p.strip())
                            break
            except httpx.HTTPError:
                pass
        deduped: list[str] = []
        for code in parts:
            if code not in deduped:
                deduped.append(code)
        return ",".join(deduped)

    async def rescue_and_place_order(
        self,
        *,
        order_id: str,
        expected_hash: str,
        nonce: str,
        tg_user_id: str,
        tg_username: str,
    ) -> tuple[int, dict[str, Any]]:
        """Execute Gate-2 Path A safe rescue (`GET` hash check -> `substitutes` -> `quotes` -> `orders` -> `reject`)."""
        fresh = await self.get_order(order_id)
        if not fresh:
            return 404, {"error": f"Order {order_id} not found."}
        if fresh.get("status") != "PENDING_APPROVAL" or str(fresh.get("content_hash") or "") != expected_hash:
            return 409, {"error": "Order state or content_hash changed before rescue."}

        fix_kind = infer_order_rescue_kind(fresh)
        if not fix_kind:
            return 422, {"error": "Order is not eligible for 1-tap safe rescue."}

        effective_diet = await self._resolve_effective_order_diet(fresh)
        delivery_date = str(fresh.get("requested_delivery_date") or "").strip()
        lines = [ln for ln in (fresh.get("lines") or []) if isinstance(ln, dict)]
        clean_lines: list[dict[str, Any]] = []

        if fix_kind == "ADJUST_TO_RECIPE_YIELD":
            exp_map = _extract_recipe_expected_qty_map(fresh)
            if not exp_map:
                return 422, {"error": "Missing expected recipe quantities for right-sizing."}
            for ln in lines:
                pid = str(ln.get("product_id") or "")
                qty = float(ln.get("quantity") or 1)
                clean_lines.append({"product_id": pid, "quantity": exp_map.get(pid, qty)})
        else:
            for ln in lines:
                pid = str(ln.get("product_id") or "")
                qty = float(ln.get("quantity") or 1)
                orig_name = str(ln.get("product_name") or ln.get("name") or "").lower()
                orig_is_plant = any(k in orig_name for k in PLANT_BASED_HINTS)
                flags = {str(f) for f in (ln.get("flags") or [])}
                is_risky = str(ln.get("delivery_status") or "") != "ON_TIME" or bool(
                    flags & {"PARTIAL", "CUTOFF_PASSED", "LATE"}
                )
                if not is_risky:
                    clean_lines.append({"product_id": pid, "quantity": qty})
                    continue
                sub_body: dict[str, Any] = {
                    "product_id": pid,
                    "quantity": qty,
                    "requested_delivery_date": delivery_date,
                }
                if effective_diet:
                    sub_body["diet"] = effective_diet
                sub_resp = await self._request("POST", "/api/products/substitutes", json_body=sub_body)
                if sub_resp.status_code not in (200, 201):
                    return 422, {"error": f"Substitute lookup failed ({sub_resp.status_code}) for {pid}."}
                chosen_pid: str | None = None
                for cand in sub_resp.json().get("items") or []:
                    if not isinstance(cand, dict):
                        continue
                    prod = cand.get("product") or {}
                    ql = cand.get("quote_line") or {}
                    ql_flags = {str(f) for f in (ql.get("flags") or [])}
                    if str(ql.get("delivery_status") or "") != "ON_TIME":
                        continue
                    if ql_flags & {"UNIT_MISMATCH", "PARTIAL", "CUTOFF_PASSED", "LATE", "NOT_DELIVERABLE", "NO_PRICE", "SALES_BLOCKED"}:
                        continue
                    cand_text = f"{prod.get('name', '')} {prod.get('category_name', '')}".lower()
                    if not orig_is_plant and any(k in cand_text for k in PLANT_BASED_HINTS):
                        continue
                    cand_pid = str(prod.get("id") or ql.get("product_id") or "").strip()
                    if cand_pid:
                        chosen_pid = cand_pid
                        break
                if not chosen_pid:
                    return 422, {"error": f"No verified ON_TIME diet-safe dairy substitute for {pid}."}
                clean_lines.append({"product_id": chosen_pid, "quantity": qty})

        quote_req: dict[str, Any] = {
            "requested_delivery_date": delivery_date,
            "lines": clean_lines,
        }
        if effective_diet:
            quote_req["diet"] = effective_diet
        q_resp = await self._request("POST", "/api/quotes", json_body=quote_req)
        if q_resp.status_code not in (200, 201):
            return 422, {"error": f"Replacement quote failed ({q_resp.status_code})."}
        quote = q_resp.json()
        if str((quote.get("policy_preview") or {}).get("outcome") or "") == "BLOCKED":
            return 422, {"error": "Replacement quote was BLOCKED by policy."}
        for ql in quote.get("lines") or []:
            if not isinstance(ql, dict):
                continue
            ql_flags = {str(f) for f in (ql.get("flags") or [])}
            if str(ql.get("delivery_status") or "") != "ON_TIME" or "UNIT_MISMATCH" in ql_flags:
                return 422, {"error": "Replacement quote line is not ON_TIME or has UNIT_MISMATCH."}

        idem_place = f"rescue-place:{order_id}:{expected_hash[:24]}:{fix_kind}"[:128]
        ord_req: dict[str, Any] = {
            "quote_id": str(quote["id"]),
            "note": f"Rescued ({fix_kind}) from {order_id} via Telegram by @{tg_username} (tg_user_id={tg_user_id})",
        }
        if effective_diet:
            ord_req["diet"] = effective_diet
        o_resp = await self._request(
            "POST",
            "/api/orders",
            json_body=ord_req,
            headers={"Idempotency-Key": idem_place},
        )
        if o_resp.status_code not in (200, 201):
            return o_resp.status_code, {"error": f"Replacement order placement failed ({o_resp.status_code})."}
        new_order = o_resp.json()

        old_total = float(fresh.get("total_excl_vat_eur") or 0.0)
        new_total = float(new_order.get("total_excl_vat_eur") or 0.0)
        savings = max(0.0, old_total - new_total)
        sup_note = (
            f"Superseded by rescued order {new_order.get('id')} "
            f"(saved €{savings:,.2f}) via Telegram by @{tg_username}"
        )
        await self.reject_order(
            order_id=order_id,
            nonce=f"sup_{nonce}",
            tg_user_id=tg_user_id,
            tg_username=tg_username,
            note_override=sup_note,
        )
        return 201, {
            "new_order": new_order,
            "superseded_order_id": order_id,
            "fix_kind": fix_kind,
            "savings_eur": savings,
        }

    async def submit_agentic_order_prompt(
        self,
        *,
        prompt: str,
        tg_user_id: str,
        tg_username: str,
    ) -> httpx.Response:
        """Send natural-language order request to `/api/orders` (or fallback `/api/assistant/chat`)."""
        idem = f"tg_ord_{secrets.token_hex(8)}"
        resp = await self._request(
            "POST",
            "/api/orders",
            json_body={
                "prompt": prompt,
                "source_channel": f"telegram:{tg_user_id}:@{tg_username}",
            },
            headers={"Idempotency-Key": idem},
        )
        if resp.status_code == 422:
            resp = await self._request(
                "POST",
                "/api/assistant/chat",
                json_body={"message": prompt},
            )
        return resp

    async def _bridge_post(
        self,
        path: str,
        json_body: dict[str, Any],
        *,
        bot_token: str = "",
        extra_headers: dict[str, str] | None = None,
        timeout: float = BRIDGE_POST_TIMEOUT_SECONDS,
    ) -> httpx.Response:
        """POST to local Backend `/api/telegram/*` authenticated via `X-Telegram-Bridge-Secret`."""
        effective_token = bot_token or self._bot_token
        headers: dict[str, str] = {
            "X-Telegram-Bridge-Secret": compute_bridge_secret(effective_token),
        }
        if extra_headers:
            headers.update(extra_headers)
        return await self._client.post(
            path,
            json=json_body,
            headers=headers,
            timeout=timeout,
        )

    async def unlink_telegram_customer(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str,
        bot_token: str = "",
    ) -> httpx.Response | None:
        """Best-effort `POST /api/telegram/unlink` so backend flips `enabled=False` & deletes session."""
        try:
            return await self._bridge_post(
                "/api/telegram/unlink",
                {
                    "telegram_user_id": str(telegram_user_id),
                    "telegram_chat_id": str(telegram_chat_id),
                },
                bot_token=bot_token,
                timeout=15.0,
            )
        except httpx.HTTPError:
            return None

    async def claim_telegram_link(
        self,
        *,
        token: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        telegram_username: str,
        bot_token: str = "",
    ) -> httpx.Response:
        return await self._bridge_post(
            "/api/telegram/link-claim",
            {
                "token": str(token),
                "telegram_user_id": str(telegram_user_id),
                "telegram_chat_id": str(telegram_chat_id),
                "telegram_username": normalize_username(telegram_username),
            },
            bot_token=bot_token,
        )

    async def select_customer_branch(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str,
        branch_id: str,
        bot_token: str = "",
    ) -> httpx.Response:
        return await self._bridge_post(
            "/api/telegram/select-branch",
            {
                "telegram_user_id": str(telegram_user_id),
                "telegram_chat_id": str(telegram_chat_id),
                "branch_id": str(branch_id),
            },
            bot_token=bot_token,
        )

    async def send_customer_chat(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str,
        telegram_username: str,
        message: str,
        bot_token: str = "",
    ) -> httpx.Response:
        return await self._bridge_post(
            "/api/telegram/customer-chat",
            {
                "telegram_user_id": str(telegram_user_id),
                "telegram_chat_id": str(telegram_chat_id),
                "telegram_username": normalize_username(telegram_username),
                "message": str(message),
            },
            bot_token=bot_token,
        )

    async def place_customer_quote(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str,
        quote_id: str,
        idempotency_key: str,
        bot_token: str = "",
    ) -> httpx.Response:
        idem = str(idempotency_key)
        return await self._bridge_post(
            "/api/telegram/place-quote",
            {
                "telegram_user_id": str(telegram_user_id),
                "telegram_chat_id": str(telegram_chat_id),
                "quote_id": str(quote_id),
                "idempotency_key": idem,
            },
            bot_token=bot_token,
            extra_headers={"Idempotency-Key": idem},
        )

    async def _bridge_get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        bot_token: str = "",
        timeout: float = 15.0,
    ) -> httpx.Response:
        """GET from local Backend `/api/telegram/*` authenticated via `X-Telegram-Bridge-Secret`."""
        effective_token = bot_token or self._bot_token
        headers: dict[str, str] = {
            "X-Telegram-Bridge-Secret": compute_bridge_secret(effective_token),
        }
        return await self._client.get(
            path,
            params=params,
            headers=headers,
            timeout=timeout,
        )

    async def list_connected_sessions(self, *, bot_token: str = "") -> list[dict[str, Any]]:
        """Fetch all active connected private Telegram user sessions across all companies."""
        effective_token = bot_token or self._bot_token
        if not effective_token:
            return []
        try:
            resp = await self._bridge_get("/api/telegram/connected-sessions", bot_token=bot_token)
        except httpx.HTTPError:
            return []
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        body = resp.json() if resp.content else {}
        items = body.get("items") if isinstance(body, dict) else body
        return [s for s in items if isinstance(s, dict)] if isinstance(items, list) else []

    async def list_order_notifications(
        self,
        *,
        since: str | None = None,
        include_same_chat_origin: bool = False,
        limit: int = 50,
        bot_token: str = "",
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Fetch multi-company order confirmations & pending approvals (`404` -> `([], None)`)."""
        effective_token = bot_token or self._bot_token
        if not effective_token:
            return [], None
        params: dict[str, Any] = {
            "include_same_chat_origin": "true" if include_same_chat_origin else "false",
            "limit": max(1, min(limit, 200)),
        }
        if since and since.strip():
            params["since"] = since.strip()
        try:
            resp = await self._bridge_get(
                "/api/telegram/order-notifications",
                params=params,
                bot_token=bot_token,
            )
        except httpx.HTTPError:
            return [], None
        if resp.status_code == 404:
            return [], None
        resp.raise_for_status()
        body = resp.json() if resp.content else {}
        if not isinstance(body, dict):
            return [], None
        raw_items = body.get("items")
        items = [n for n in raw_items if isinstance(n, dict)] if isinstance(raw_items, list) else []
        srv_time = body.get("server_time")
        return items, (str(srv_time) if srv_time else None)

    async def decide_customer_order(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str,
        order_id: str,
        decision: str,
        content_hash: str | None = None,
        note: str | None = None,
        idempotency_key: str | None = None,
        bot_token: str = "",
    ) -> httpx.Response:
        """Approve or reject a company order via `POST /api/telegram/decide-order`."""
        payload: dict[str, Any] = {
            "telegram_user_id": str(telegram_user_id),
            "telegram_chat_id": str(telegram_chat_id),
            "order_id": str(order_id),
            "decision": str(decision),
        }
        if content_hash:
            payload["content_hash"] = str(content_hash)
        if note:
            payload["note"] = str(note)
        extra_headers: dict[str, str] = {}
        if idempotency_key:
            idem = str(idempotency_key)
            payload["idempotency_key"] = idem
            extra_headers["Idempotency-Key"] = idem
        return await self._bridge_post(
            "/api/telegram/decide-order",
            payload,
            bot_token=bot_token,
            extra_headers=extra_headers or None,
        )

    async def cancel_customer_order(
        self,
        *,
        telegram_user_id: int | str,
        telegram_chat_id: int | str,
        order_id: str,
        bot_token: str = "",
    ) -> dict[str, Any]:
        resp = await self._bridge_post(
            "/api/telegram/cancel-order",
            {
                "telegram_user_id": str(telegram_user_id),
                "telegram_chat_id": str(telegram_chat_id),
                "order_id": str(order_id),
            },
            bot_token=bot_token,
            timeout=BRIDGE_POST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        body = resp.json() if resp.content else {}
        return body if isinstance(body, dict) else {"result": body}


def is_verified_operator(
    *,
    username: str,
    user_id: str,
    chat_type: str,
    allowed_user_ids: set[str],
) -> bool:
    """Strictly verify private configured operator (`TELEGRAM_OPERATOR_USERNAME` / `TELEGRAM_OPERATOR_USER_ID`) plus optional `allowed_user_ids` pin.

    Returns False if either `OPERATOR_USERNAME` or `OPERATOR_USER_ID` is empty/unset.
    """
    if chat_type != "private":
        return False
    op_u = normalize_username(OPERATOR_USERNAME)
    op_uid = OPERATOR_USER_ID.strip()
    if not op_u or not op_uid:
        return False
    norm_u = normalize_username(username)
    uid = str(user_id or "").strip()
    if (norm_u and norm_u == op_u) or (uid and uid == op_uid and not norm_u):
        return not (allowed_user_ids and uid not in allowed_user_ids)
    return False


def _build_branch_buttons(branches: list[dict[str, Any]]) -> list[list[dict[str, str]]]:
    rows: list[list[dict[str, str]]] = []
    for b in branches:
        if not isinstance(b, dict):
            continue
        b_id = str(b.get("id") or b.get("branch_id") or "").strip()
        if not b_id:
            continue
        b_short = str(b.get("short_name") or b.get("name") or b_id).strip()
        cb_data = f"brn:{b_id}"[:64]
        rows.append([{"text": f"🏨 {b_short}", "callback_data": cb_data}])
    return rows


def _format_branch_choice_sentence(branches: list[dict[str, Any]]) -> str:
    names = [
        str(b.get("short_name") or b.get("name") or b.get("id") or "Kitchen").strip()
        for b in branches
        if isinstance(b, dict)
    ]
    names = [n for n in names if n]
    if not names:
        return "your kitchen"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} or {names[1]}"
    return f"{', '.join(names[:-1])}, or {names[-1]}"


def _finalize_customer_markup(
    rows: list[list[dict[str, str]]],
    *,
    include_back_to_lead: bool,
) -> dict[str, Any] | None:
    out_rows = list(rows)
    if include_back_to_lead:
        out_rows.append([{"text": "🔙 Back to Team Lead", "callback_data": "mode:lead"}])
    return {"inline_keyboard": out_rows} if out_rows else None


def _update_customer_branch_cache(
    state: TelegramStateStore,
    chat_id: str,
    payload: dict[str, Any],
) -> None:
    cust = state.customer_chats.get(str(chat_id))
    if not isinstance(cust, dict):
        return
    changed = False
    raw_branches = payload.get("branches")
    if isinstance(raw_branches, list) and raw_branches:
        cust["branches"] = [b for b in raw_branches if isinstance(b, dict)]
        changed = True
    raw_active = payload.get("active_branch")
    if isinstance(raw_active, dict):
        cust["active_branch"] = raw_active
        changed = True
    if changed:
        state.save()


def _is_numeric_score(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _format_assurance_badge_line(
    *,
    assurance_score: Any,
    assurance_threshold: Any,
    auto_executed: bool,
) -> str:
    if not _is_numeric_score(assurance_score):
        return ""
    score_int = round(float(assurance_score))
    thresh_part = (
        f" (threshold {round(float(assurance_threshold))})"
        if _is_numeric_score(assurance_threshold)
        else ""
    )
    auto_part = " · Auto-placed" if auto_executed else ""
    return f"🎯 Assurance {score_int}/100{thresh_part}{auto_part}"


def _build_undo_order_button_row(
    *,
    state: TelegramStateStore,
    order_id: str,
    order_status: str,
    chat_id: str,
    user_id: str,
) -> list[dict[str, str]] | None:
    if not order_id or order_status.upper() not in {"PLACED", "PENDING_APPROVAL"}:
        return None
    cnl_nonce = state.register_customer_cancel_nonce(
        order_id=order_id,
        chat_id=chat_id,
        user_id=user_id,
    )
    cnl_cb = f"cnl:{cnl_nonce}"
    assert len(cnl_cb.encode("utf-8")) <= 64
    return [{"text": "↩️ Undo / Cancel Order", "callback_data": cnl_cb}]


async def _dispatch_customer_chat_payload(
    payload: dict[str, Any],
    *,
    chat_id: str,
    user_id: str,
    is_op: bool,
    state: TelegramStateStore,
    tg: TelegramBotClient,
    reply_to_message_id: str | int | None = None,
    prefix: str = "",
) -> str:
    """Render & send a `customer-chat` (or `select-branch` `auto_reply`) response cleanly."""
    _update_customer_branch_cache(state, chat_id, payload)
    cust = state.customer_chats.get(str(chat_id)) or {}
    cached_branches = [b for b in (cust.get("branches") or []) if isinstance(b, dict)]
    include_lead_btn = is_op and state.operator_mode == "customer"

    status = str(payload.get("status") or "ok").strip().lower()
    if status == "branch_required":
        branches = [
            b for b in (payload.get("branches") or cached_branches) if isinstance(b, dict)
        ]
        reply_text = clean_telegram_plain_text(str(payload.get("reply_text") or ""))
        if not reply_text:
            reply_text = f"Are you making an order for {_format_branch_choice_sentence(branches)}?"
        full_text = f"{prefix}{reply_text}".strip()
        markup = _finalize_customer_markup(
            _build_branch_buttons(branches),
            include_back_to_lead=include_lead_btn,
        )
        await tg.send_message(
            chat_id,
            full_text,
            reply_to_message_id=reply_to_message_id,
            reply_markup=markup,
        )
        return "customer_branch_required"

    reply_text = clean_telegram_plain_text(
        str(payload.get("reply_text") or payload.get("reply") or "")
    )
    if not reply_text:
        reply_text = "How can I help your kitchen today?"
    full_text = f"{prefix}{reply_text}".strip()

    rows: list[list[dict[str, str]]] = []
    order = payload.get("order")
    order_id = (
        str(order.get("id") or order.get("order_id") or "").strip()
        if isinstance(order, dict)
        else ""
    )
    if isinstance(order, dict) and order_id:
        order_status = str(order.get("status") or "PLACED").strip().upper()
        raw_total = (
            order.get("total_excl_vat_eur")
            if order.get("total_excl_vat_eur") is not None
            else order.get("total_eur")
            if order.get("total_eur") is not None
            else order.get("total")
        )
        total_part = (
            f" · €{float(raw_total):.2f} excl. VAT"
            if _is_numeric_score(raw_total)
            else ""
        )
        delivery_date = str(order.get("requested_delivery_date") or "").strip()
        deliv_part = f" · Delivery {delivery_date}" if delivery_date else ""
        assurance_score = (
            payload.get("assurance_score")
            if payload.get("assurance_score") is not None
            else order.get("assurance_score")
        )
        assurance_threshold = (
            payload.get("assurance_threshold")
            if payload.get("assurance_threshold") is not None
            else order.get("assurance_threshold")
        )
        auto_executed = bool(payload.get("auto_executed") or order.get("auto_executed"))

        summary_lines: list[str] = []
        assurance_line = _format_assurance_badge_line(
            assurance_score=assurance_score,
            assurance_threshold=assurance_threshold,
            auto_executed=auto_executed,
        )
        if assurance_line:
            summary_lines.append(assurance_line)
        status_icon = (
            "🛎"
            if order_status == "PENDING_APPROVAL"
            else "✅"
            if order_status == "PLACED"
            else "📦"
        )
        summary_lines.append(f"{status_icon} Order {order_id} ({order_status}){total_part}{deliv_part}")
        if order_status == "PENDING_APPROVAL":
            res_status = str(order.get("reservation_status") or "").strip()
            res_expires_raw = str(order.get("reservation_expires_at") or "").strip()
            if res_status == "HELD_5H":
                until_part = ""
                if res_expires_raw:
                    try:
                        dt_exp = datetime.fromisoformat(res_expires_raw)
                        until_part = f" until {dt_exp.strftime('%Y-%m-%d %H:%M')}"
                    except ValueError:
                        until_part = f" until {res_expires_raw}"
                summary_lines.append(
                    f"🔒 Stock reserved for 5h{until_part} (HELD_5H) while your manager reviews!"
                )
        full_text = f"{full_text}\n\n" + "\n".join(summary_lines)
        undo_row = _build_undo_order_button_row(
            state=state,
            order_id=order_id,
            order_status=order_status,
            chat_id=chat_id,
            user_id=user_id,
        )
        if undo_row is not None:
            rows.append(undo_row)
    else:
        quote = payload.get("quote")
        if isinstance(quote, dict):
            quote_id = str(quote.get("id") or quote.get("quote_id") or "").strip()
            policy_outcome = str(
                (quote.get("policy_preview") or quote.get("policy") or {}).get("outcome") or ""
            ).upper()
            if quote_id and quote.get("placeable") is not False and policy_outcome != "BLOCKED":
                raw_total = (
                    quote.get("total_excl_vat_eur")
                    if quote.get("total_excl_vat_eur") is not None
                    else quote.get("total_eur")
                    if quote.get("total_eur") is not None
                    else quote.get("total")
                )
                nonce = state.register_customer_order_nonce(
                    chat_id=chat_id,
                    user_id=user_id,
                    quote_id=quote_id,
                    total_eur=raw_total if _is_numeric_score(raw_total) else None,
                )
                btn_label = (
                    f"🛒 Place Order (€{float(raw_total):.2f})"
                    if _is_numeric_score(raw_total)
                    else "🛒 Place Order"
                )
                rows.append([{"text": btn_label, "callback_data": f"ord:{nonce}"}])
                q_score = (
                    payload.get("assurance_score")
                    if payload.get("assurance_score") is not None
                    else quote.get("assurance_score")
                )
                q_thresh = (
                    payload.get("assurance_threshold")
                    if payload.get("assurance_threshold") is not None
                    else quote.get("assurance_threshold")
                )
                if not payload.get("auto_executed") and _is_numeric_score(q_score):
                    s_int = round(float(q_score))
                    t_int = round(float(q_thresh)) if _is_numeric_score(q_thresh) else 80
                    full_text = f"{full_text}\n\nAssurance {s_int}/{t_int} — tap Confirm to place"

    raw_opts = payload.get("clarification_options")
    if isinstance(raw_opts, list) and raw_opts:
        clean_opts = [clean_telegram_plain_text(str(o)) for o in raw_opts if str(o).strip()][:4]
        clean_opts = [o for o in clean_opts if o]
        if clean_opts:
            opt_nonce = state.register_customer_options_nonce(
                chat_id=chat_id,
                user_id=user_id,
                options=clean_opts,
            )
            for idx, opt_text in enumerate(clean_opts):
                rows.append(
                    [
                        {
                            "text": opt_text[:60],
                            "callback_data": f"opt:{opt_nonce}:{idx}",
                        }
                    ]
                )

    markup = _finalize_customer_markup(rows, include_back_to_lead=include_lead_btn)
    await tg.send_message(
        chat_id,
        full_text,
        reply_to_message_id=reply_to_message_id,
        reply_markup=markup,
    )
    return "customer_chat_replied"


async def _handle_customer_chat_turn(
    *,
    message_text: str,
    chat_id: str,
    user_id: str,
    raw_username: str,
    is_op: bool,
    state: TelegramStateStore,
    tg: TelegramBotClient,
    aimo: AimoCoreApiClient,
    reply_to_message_id: str | int | None = None,
) -> str:
    """Call `POST /api/telegram/customer-chat` with fail-closed `404` cleanup & timeout fallback."""
    include_lead_btn = is_op and state.operator_mode == "customer"
    try:
        resp = await aimo.send_customer_chat(
            telegram_user_id=user_id,
            telegram_chat_id=chat_id,
            telegram_username=raw_username,
            message=message_text,
            bot_token=tg._token,
        )
    except httpx.HTTPError:
        await tg.send_message(
            chat_id,
            CUSTOMER_TIMEOUT_FALLBACK_MESSAGE,
            reply_to_message_id=reply_to_message_id,
            reply_markup=_finalize_customer_markup([], include_back_to_lead=include_lead_btn),
        )
        return "customer_chat_timeout"

    if resp.status_code == 404:
        state.customer_chats.pop(str(chat_id), None)
        if is_op:
            state.operator_mode = "lead"
            state.save()
            await tg.send_message(
                chat_id,
                "Customer Telegram link is no longer active — switched back to Team Lead operator channel.",
                reply_to_message_id=reply_to_message_id,
            )
            return "customer_unlinked_404"
        state.save()
        return "customer_stale_silent_drop"

    if resp.status_code not in (200, 201):
        await tg.send_message(
            chat_id,
            "Sorry, the kitchen assistant couldn't complete that right now — please try again in a moment!",
            reply_to_message_id=reply_to_message_id,
            reply_markup=_finalize_customer_markup([], include_back_to_lead=include_lead_btn),
        )
        return "customer_chat_error"

    payload = resp.json() if resp.content else {}
    if not isinstance(payload, dict):
        payload = {"status": "ok", "reply_text": str(payload)}
    return await _dispatch_customer_chat_payload(
        payload,
        chat_id=chat_id,
        user_id=user_id,
        is_op=is_op,
        state=state,
        tg=tg,
        reply_to_message_id=reply_to_message_id,
    )


async def _handle_branch_menu(
    *,
    chat_id: str,
    user_id: str,
    raw_username: str,
    is_op: bool,
    state: TelegramStateStore,
    tg: TelegramBotClient,
    aimo: AimoCoreApiClient,
    reply_to_message_id: str | int | None = None,
) -> str:
    """Render 1-tap `[🏨 ...]` branch buttons from cached branches (or fetch via `customer-chat`)."""
    include_lead_btn = is_op and state.operator_mode == "customer"
    cust = state.customer_chats.get(str(chat_id)) or {}
    branches = [b for b in (cust.get("branches") or []) if isinstance(b, dict)]
    if not branches:
        try:
            resp = await aimo.send_customer_chat(
                telegram_user_id=user_id,
                telegram_chat_id=chat_id,
                telegram_username=raw_username,
                message="/branch",
                bot_token=tg._token,
            )
        except httpx.HTTPError:
            await tg.send_message(
                chat_id,
                CUSTOMER_TIMEOUT_FALLBACK_MESSAGE,
                reply_to_message_id=reply_to_message_id,
                reply_markup=_finalize_customer_markup([], include_back_to_lead=include_lead_btn),
            )
            return "customer_chat_timeout"
        if resp.status_code == 404:
            state.customer_chats.pop(str(chat_id), None)
            if is_op:
                state.operator_mode = "lead"
            state.save()
            return "customer_stale_silent_drop"
        if resp.status_code in (200, 201):
            body = resp.json()
            if isinstance(body, dict):
                _update_customer_branch_cache(state, chat_id, body)
                cust = state.customer_chats.get(str(chat_id)) or {}
                branches = [b for b in (cust.get("branches") or []) if isinstance(b, dict)]
                if not branches:
                    return await _dispatch_customer_chat_payload(
                        body,
                        chat_id=chat_id,
                        user_id=user_id,
                        is_op=is_op,
                        state=state,
                        tg=tg,
                        reply_to_message_id=reply_to_message_id,
                    )

    include_lead_btn = is_op and state.operator_mode == "customer"
    active = cust.get("active_branch") if isinstance(cust.get("active_branch"), dict) else {}
    active_label = str(active.get("name") or active.get("short_name") or "").strip()
    if branches:
        cur_part = f" (currently: {active_label})" if active_label else ""
        prompt_txt = (
            f"📍 Which kitchen are you making an order for{cur_part}?\n"
            f"Tap a location below:"
        )
        markup = _finalize_customer_markup(
            _build_branch_buttons(branches),
            include_back_to_lead=include_lead_btn,
        )
    else:
        comp = str(cust.get("company_name") or "your kitchen")
        prompt_txt = f"📍 Connected to {active_label or comp} (single kitchen location)."
        markup = _finalize_customer_markup([], include_back_to_lead=include_lead_btn)

    await tg.send_message(
        chat_id,
        prompt_txt,
        reply_to_message_id=reply_to_message_id,
        reply_markup=markup,
    )
    return "branch_menu"


async def _forward_operator_to_inbox(
    *,
    msg: dict[str, Any],
    override_text: str | None,
    user_id: str,
    chat_id: str,
    msg_id: str,
    inbox_path: Path,
    tg: TelegramBotClient,
) -> str | None:
    """Download operator attachments (`logs/telegram_attachments/`) and log to `INFORMATIONSHARING.md`."""
    effective_text = (
        override_text
        if override_text is not None
        else str(msg.get("text") or msg.get("caption") or "").strip()
    )
    saved_paths: list[Path] = []
    attach_dir = REPO_ROOT / "logs" / "telegram_attachments"
    photos = msg.get("photo")
    if isinstance(photos, list) and photos:
        best_photo = photos[-1] if isinstance(photos[-1], dict) else {}
        fid = str(best_photo.get("file_id") or "")
        if fid:
            try:
                saved_paths.append(
                    await tg.download_file(
                        fid, attach_dir, prefix=f"msg{msg_id}_", fallback_name="photo.jpg"
                    )
                )
            except (httpx.HTTPError, OSError, RuntimeError) as exc:
                effective_text = (
                    f"{effective_text}\n[Photo download error: {redact_secrets(str(exc), tg._token)}]"
                ).strip()
    doc = msg.get("document")
    if isinstance(doc, dict) and doc.get("file_id"):
        fid = str(doc.get("file_id"))
        fname = re.sub(r"[^A-Za-z0-9._-]+", "_", str(doc.get("file_name") or "document.bin"))
        try:
            saved_paths.append(
                await tg.download_file(
                    fid, attach_dir, prefix=f"msg{msg_id}_{fname}_", fallback_name=fname
                )
            )
        except (httpx.HTTPError, OSError, RuntimeError) as exc:
            effective_text = (
                f"{effective_text}\n[Document download error: {redact_secrets(str(exc), tg._token)}]"
            ).strip()

    if saved_paths:
        attach_lines = "\n".join(f"- Saved attachment: `{p}`" for p in saved_paths)
        effective_text = (
            f"{effective_text}\n{attach_lines}".strip()
            if effective_text
            else f"[Telegram attachment]\n{attach_lines}"
        )

    if effective_text:
        entry = format_inbox_entry(
            username=normalize_username(OPERATOR_USERNAME) or "operator",
            user_id=user_id,
            chat_id=chat_id,
            message_id=msg_id,
            content=effective_text,
        )
        with inbox_path.open("a", encoding="utf-8") as fh:
            fh.write(entry)
        await tg.set_reaction_eyes(chat_id, msg_id)
        return "logged_to_team_lead"
    return None


async def _complete_customer_link_claim(
    *,
    claim_resp: httpx.Response,
    chat_id: str,
    user_id: str,
    raw_username: str,
    display_username: str,
    is_op: bool,
    state: TelegramStateStore,
    tg: TelegramBotClient,
    reply_to_message_id: int | str | None = None,
    callback_query_id: str | None = None,
) -> str:
    """Persist claimed customer session, optionally switch operator mode, and send branch/greeting UI."""
    claim_body = claim_resp.json() if claim_resp.content else {}
    if not isinstance(claim_body, dict):
        claim_body = {}
    user_name = str(claim_body.get("user_name") or claim_body.get("name") or display_username).strip()
    user_email = str(claim_body.get("user_email") or claim_body.get("email") or "").strip()
    company_name = str(claim_body.get("company_name") or "AimoAnnos").strip()
    branches = [b for b in (claim_body.get("branches") or []) if isinstance(b, dict)]
    active_branch = (
        claim_body.get("active_branch")
        if isinstance(claim_body.get("active_branch"), dict)
        else None
    )
    if "needs_branch_selection" in claim_body and claim_body["needs_branch_selection"] is not None:
        needs_branch = bool(claim_body["needs_branch_selection"]) and bool(branches)
    else:
        needs_branch = len(branches) > 1 and not active_branch

    state.customer_chats[str(chat_id)] = {
        "telegram_user_id": str(user_id),
        "telegram_username": normalize_username(raw_username),
        "user_name": user_name,
        "user_email": user_email,
        "company_name": company_name,
        "branches": branches,
        "active_branch": active_branch,
    }
    if is_op:
        state.operator_mode = "customer"
    state.save()

    if callback_query_id:
        await tg.answer_callback_query(callback_query_id, f"Connected as {user_name} ({company_name})")

    kb_rows: list[list[dict[str, str]]] = []
    if needs_branch:
        greeting = (
            f"Hi {user_name}! 👋 Connected to {company_name}. "
            f"Are you making an order for {_format_branch_choice_sentence(branches)}?"
        )
        kb_rows.extend(_build_branch_buttons(branches))
    else:
        connected_target = str(
            (active_branch or {}).get("name")
            or (active_branch or {}).get("short_name")
            or company_name
        ).strip()
        greeting = (
            f"Hi {user_name}! 👋 Connected to {connected_target}. "
            f"Just message me what your kitchen needs — like '10 L lactose-free milk for tomorrow'!"
        )
        if len(branches) > 1:
            kb_rows.append([{"text": "📍 Switch Kitchen", "callback_data": "cmd:branch"}])

    if is_op:
        greeting += (
            "\n\n(Operator customer test mode active — tap below or send /lead anytime "
            "to switch back to Team Lead channel.)"
        )

    await tg.send_message(
        chat_id,
        greeting,
        reply_to_message_id=reply_to_message_id,
        reply_markup=_finalize_customer_markup(kb_rows, include_back_to_lead=is_op),
    )
    return "customer_linked"


async def _login_and_link_operator_customer(
    *,
    email: str,
    password: str,
    fallback_demo_password: bool = False,
    chat_id: str,
    user_id: str,
    raw_username: str,
    display_username: str,
    state: TelegramStateStore,
    tg: TelegramBotClient,
    aimo: AimoCoreApiClient,
    reply_to_message_id: int | str | None = None,
    callback_query_id: str | None = None,
) -> str:
    """Authenticate an operator-selected buyer account, mint & claim a Telegram link token, and greet."""
    try:
        mint_resp = await aimo.login_and_mint_link_token(email=email, password=password)
        if (
            fallback_demo_password
            and mint_resp.status_code == 401
            and password != "demo1234"
        ):
            mint_resp = await aimo.login_and_mint_link_token(email=email, password="demo1234")
    except httpx.HTTPError:
        if callback_query_id:
            await tg.answer_callback_query(
                callback_query_id,
                "Backend unreachable — try again in a moment.",
                show_alert=True,
            )
        await tg.send_message(
            chat_id,
            CUSTOMER_TIMEOUT_FALLBACK_MESSAGE,
            reply_to_message_id=reply_to_message_id,
        )
        return "operator_login_timeout"

    if mint_resp.status_code not in (200, 201):
        err_msg = (
            f"Login failed for {email} (invalid email or password)."
            if mint_resp.status_code == 401
            else f"Could not mint Telegram link token for {email} (HTTP {mint_resp.status_code})."
        )
        if callback_query_id:
            await tg.answer_callback_query(callback_query_id, err_msg, show_alert=True)
        await tg.send_message(
            chat_id,
            f"❌ {err_msg}",
            reply_to_message_id=reply_to_message_id,
        )
        return "operator_login_failed"

    mint_body = mint_resp.json() if mint_resp.content else {}
    raw_tok = str((mint_body if isinstance(mint_body, dict) else {}).get("token") or "").strip()
    if not raw_tok:
        if callback_query_id:
            await tg.answer_callback_query(
                callback_query_id,
                "Link token missing in backend response.",
                show_alert=True,
            )
        await tg.send_message(
            chat_id,
            "❌ Backend returned an empty Telegram link token.",
            reply_to_message_id=reply_to_message_id,
        )
        return "operator_login_failed"

    try:
        claim_resp = await aimo.claim_telegram_link(
            token=raw_tok,
            telegram_user_id=user_id,
            telegram_chat_id=chat_id,
            telegram_username=raw_username,
            bot_token=tg._token,
        )
    except httpx.HTTPError:
        if callback_query_id:
            await tg.answer_callback_query(callback_query_id, "Link claim timed out.", show_alert=True)
        await tg.send_message(
            chat_id,
            CUSTOMER_TIMEOUT_FALLBACK_MESSAGE,
            reply_to_message_id=reply_to_message_id,
        )
        return "link_claim_timeout"

    if claim_resp.status_code not in (200, 201):
        if callback_query_id:
            await tg.answer_callback_query(
                callback_query_id,
                "Could not claim Telegram link.",
                show_alert=True,
            )
        await tg.send_message(
            chat_id,
            "❌ Failed to claim freshly minted Telegram link token.",
            reply_to_message_id=reply_to_message_id,
        )
        return "operator_login_failed"

    return await _complete_customer_link_claim(
        claim_resp=claim_resp,
        chat_id=chat_id,
        user_id=user_id,
        raw_username=raw_username,
        display_username=display_username,
        is_op=True,
        state=state,
        tg=tg,
        reply_to_message_id=reply_to_message_id,
        callback_query_id=callback_query_id,
    )


async def process_telegram_update(
    upd: dict[str, Any],
    *,
    allowed_user_ids: set[str],
    state: TelegramStateStore,
    inbox_path: Path,
    tg: TelegramBotClient,
    aimo: AimoCoreApiClient,
) -> str:
    """Process one Telegram update (`message` or `callback_query`) with fail-closed auth."""
    update_id = int(upd.get("update_id") or 0)

    # 1. Inline button tap (`callback_query`)
    cb = upd.get("callback_query")
    if isinstance(cb, dict):
        cb_id = str(cb.get("id") or "")
        from_user = cb.get("from") or {}
        user_id = str(from_user.get("id") or "")
        raw_username = str(from_user.get("username") or "")
        display_username = normalize_username(raw_username) or user_id
        msg_obj = cb.get("message") or {}
        chat_obj = msg_obj.get("chat") or {}
        chat_id = str(chat_obj.get("id") or "")
        chat_type = str(chat_obj.get("type") or "")
        msg_id = msg_obj.get("message_id")

        if not state.mark_update(update_id):
            return "duplicate"

        if not is_private_chat(chat_type, chat_id):
            return "unauthorized_callback"

        is_op = is_verified_operator(
            username=raw_username,
            user_id=user_id,
            chat_type=chat_type,
            allowed_user_ids=allowed_user_ids,
        )
        if is_op:
            state.record_operator_chat(chat_id=chat_id, user_id=user_id)

        data = str(cb.get("data") or "")
        action, _, nonce = data.partition(":")

        # 1a. Operator Dual-Mode switch back (`mode:lead`) & 1-tap Buyer Login (`lgn:<persona>`)
        if data == "mode:lead":
            if not is_op:
                return "unauthorized_callback"
            state.operator_mode = "lead"
            state.save()
            await tg.answer_callback_query(cb_id, "Switched back to Team Lead channel.")
            await tg.send_message(chat_id, "Switched back to Team Lead operator channel.")
            return "operator_mode_lead"

        if action == "lgn":
            if not is_op:
                return "unauthorized_callback"
            persona_info = DEMO_LOGIN_PERSONAS.get(nonce.strip().lower())
            if persona_info is None:
                await tg.answer_callback_query(cb_id, "Unknown demo persona.", show_alert=True)
                return "operator_login_unknown_persona"
            persona_email, _ = persona_info
            return await _login_and_link_operator_customer(
                email=persona_email,
                password=resolve_default_demo_password(),
                fallback_demo_password=True,
                chat_id=chat_id,
                user_id=user_id,
                raw_username=raw_username,
                display_username=display_username,
                state=state,
                tg=tg,
                aimo=aimo,
                callback_query_id=cb_id,
            )

        # 1b. Customer Buyer Callbacks (`cmd:branch`, `brn:<id>`, `ord:<nonce>`, `opt:<nonce>:<i>`, `cnl:<nonce>`)
        if data == "cmd:branch" or action in ("brn", "ord", "opt", "cnl"):
            if not is_op and aimo.configured:
                await aimo.list_opted_in_telegram_recipients(state=state)
            cust_entry = state.customer_chats.get(str(chat_id))
            cust_uid = str((cust_entry or {}).get("telegram_user_id") or "")
            is_valid_customer_caller = (
                isinstance(cust_entry, dict)
                and (not cust_uid or cust_uid == str(user_id) or is_op)
            ) or (is_op and state.operator_mode == "customer")
            if not is_valid_customer_caller:
                return "unauthorized_callback"
            if action == "cnl" and is_op and state.operator_mode != "customer" and not isinstance(cust_entry, dict):
                return "unauthorized_callback"

            if data == "cmd:branch":
                await tg.answer_callback_query(cb_id, "Choose kitchen")
                return await _handle_branch_menu(
                    chat_id=chat_id,
                    user_id=user_id,
                    raw_username=raw_username,
                    is_op=is_op,
                    state=state,
                    tg=tg,
                    aimo=aimo,
                )

            if action == "brn":
                branch_id = nonce.strip()
                if not branch_id:
                    await tg.answer_callback_query(cb_id, "Unknown kitchen branch.", show_alert=True)
                    return "unknown_branch"
                include_lead_btn = is_op and state.operator_mode == "customer"
                try:
                    b_resp = await aimo.select_customer_branch(
                        telegram_user_id=user_id,
                        telegram_chat_id=chat_id,
                        branch_id=branch_id,
                        bot_token=tg._token,
                    )
                except httpx.HTTPError:
                    with contextlib.suppress(httpx.HTTPError, RuntimeError):
                        await tg.answer_callback_query(
                            cb_id,
                            CUSTOMER_TIMEOUT_FALLBACK_MESSAGE,
                            show_alert=True,
                        )
                    await tg.send_message(
                        chat_id,
                        CUSTOMER_TIMEOUT_FALLBACK_MESSAGE,
                        reply_markup=_finalize_customer_markup(
                            [], include_back_to_lead=include_lead_btn
                        ),
                    )
                    return "branch_select_timeout"
                if b_resp.status_code == 404:
                    state.customer_chats.pop(str(chat_id), None)
                    if is_op:
                        state.operator_mode = "lead"
                        state.save()
                        await tg.answer_callback_query(
                            cb_id,
                            "Customer Telegram link is no longer active — switched back to Team Lead.",
                            show_alert=True,
                        )
                        return "customer_unlinked_404"
                    state.save()
                    return "unauthorized_callback"
                if b_resp.status_code not in (200, 201):
                    await tg.answer_callback_query(
                        cb_id,
                        "Couldn't switch kitchen right now — please try again.",
                        show_alert=True,
                    )
                    return "branch_select_error"
                b_body = b_resp.json() if b_resp.content else {}
                if not isinstance(b_body, dict):
                    b_body = {}
                _update_customer_branch_cache(state, chat_id, b_body)
                active_branch = b_body.get("active_branch") or b_body.get("branch")
                if not isinstance(active_branch, dict):
                    active_branch = {}
                    for cand in (state.customer_chats.get(str(chat_id)) or {}).get("branches") or []:
                        if isinstance(cand, dict) and str(cand.get("id") or "") == branch_id:
                            active_branch = cand
                            state.customer_chats[str(chat_id)]["active_branch"] = cand
                            state.save()
                            break
                branch_name = str(
                    active_branch.get("name") or active_branch.get("short_name") or branch_id
                ).strip()
                await tg.answer_callback_query(cb_id, f"Kitchen set to {branch_name}")
                confirm_line = f"📍 Kitchen set to {branch_name}."
                auto_reply = b_body.get("auto_reply")
                if isinstance(auto_reply, dict):
                    await _dispatch_customer_chat_payload(
                        auto_reply,
                        chat_id=chat_id,
                        user_id=user_id,
                        is_op=is_op,
                        state=state,
                        tg=tg,
                        prefix=f"{confirm_line}\n\n",
                    )
                    return "branch_selected_auto_replied"
                if isinstance(auto_reply, str) and auto_reply.strip():
                    await _dispatch_customer_chat_payload(
                        {
                            "status": "ok",
                            "reply_text": auto_reply,
                            "quote": b_body.get("quote"),
                            "order": b_body.get("order"),
                            "assurance_score": b_body.get("assurance_score"),
                            "assurance_threshold": b_body.get("assurance_threshold"),
                            "auto_executed": b_body.get("auto_executed"),
                            "clarification_options": b_body.get("clarification_options"),
                        },
                        chat_id=chat_id,
                        user_id=user_id,
                        is_op=is_op,
                        state=state,
                        tg=tg,
                        prefix=f"{confirm_line}\n\n",
                    )
                    return "branch_selected_auto_replied"

                await tg.send_message(
                    chat_id,
                    f"{confirm_line} Just message me what your kitchen needs!",
                    reply_markup=_finalize_customer_markup([], include_back_to_lead=include_lead_btn),
                )
                return "branch_selected"

            if action == "ord":
                ord_meta = state.customer_order_nonces.get(nonce)
                if not ord_meta or str(ord_meta.get("chat_id") or "") != str(chat_id):
                    await tg.answer_callback_query(
                        cb_id,
                        "This quote button expired — message me for a fresh quote!",
                        show_alert=True,
                    )
                    return "unknown_order_nonce"
                quote_id = str(ord_meta.get("quote_id") or "")
                include_lead_btn = is_op and state.operator_mode == "customer"
                try:
                    o_resp = await aimo.place_customer_quote(
                        telegram_user_id=user_id,
                        telegram_chat_id=chat_id,
                        quote_id=quote_id,
                        idempotency_key=f"tg-ord-{nonce}",
                        bot_token=tg._token,
                    )
                except httpx.HTTPError:
                    with contextlib.suppress(httpx.HTTPError, RuntimeError):
                        await tg.answer_callback_query(
                            cb_id,
                            CUSTOMER_TIMEOUT_FALLBACK_MESSAGE,
                            show_alert=True,
                        )
                    await tg.send_message(
                        chat_id,
                        CUSTOMER_TIMEOUT_FALLBACK_MESSAGE,
                        reply_markup=_finalize_customer_markup(
                            [], include_back_to_lead=include_lead_btn
                        ),
                    )
                    return "customer_order_timeout"
                if o_resp.status_code not in (200, 201):
                    err_detail = "Quote expired or stock changed"
                    with contextlib.suppress(ValueError):
                        err_json = o_resp.json()
                        if isinstance(err_json, dict):
                            err_obj = err_json.get("error")
                            if isinstance(err_obj, dict) and err_obj.get("message"):
                                err_detail = str(err_obj["message"])
                            elif err_json.get("detail"):
                                err_detail = str(err_json["detail"])
                    await tg.answer_callback_query(cb_id, err_detail[:190], show_alert=True)
                    await tg.send_message(
                        chat_id,
                        f"⚠️ Couldn't place that quote ({err_detail}). Just message me again and I'll check fresh stock & prices!",
                        reply_markup=_finalize_customer_markup(
                            [], include_back_to_lead=include_lead_btn
                        ),
                    )
                    return "customer_order_error"

                resp_json = o_resp.json() if o_resp.content else {}
                order_obj = (
                    resp_json.get("order")
                    if isinstance(resp_json, dict) and isinstance(resp_json.get("order"), dict)
                    else resp_json
                    if isinstance(resp_json, dict)
                    else {}
                )
                order_id = str(order_obj.get("id") or order_obj.get("order_id") or "")
                order_status = str(
                    order_obj.get("status")
                    or (resp_json.get("status") if isinstance(resp_json, dict) else "")
                    or "PLACED"
                )
                res_status = str(
                    order_obj.get("reservation_status")
                    or (resp_json.get("reservation_status") if isinstance(resp_json, dict) else "")
                    or ""
                ).strip()
                res_expires_raw = str(
                    order_obj.get("reservation_expires_at")
                    or (
                        resp_json.get("reservation_expires_at")
                        if isinstance(resp_json, dict)
                        else ""
                    )
                    or ""
                ).strip()
                raw_total = (
                    order_obj.get("total_excl_vat_eur")
                    if order_obj.get("total_excl_vat_eur") is not None
                    else ord_meta.get("total_eur")
                )
                total_part = (
                    f" (€{float(raw_total):.2f} excl. VAT)"
                    if isinstance(raw_total, (int, float))
                    else ""
                )
                delivery_date = str(order_obj.get("requested_delivery_date") or "").strip()
                deliv_part = f" for delivery {delivery_date}" if delivery_date else ""
                assurance_score = (
                    order_obj.get("assurance_score")
                    if order_obj.get("assurance_score") is not None
                    else resp_json.get("assurance_score")
                    if isinstance(resp_json, dict)
                    else None
                )
                assurance_threshold = (
                    order_obj.get("assurance_threshold")
                    if order_obj.get("assurance_threshold") is not None
                    else resp_json.get("assurance_threshold")
                    if isinstance(resp_json, dict)
                    else None
                )
                auto_executed = bool(
                    order_obj.get("auto_executed")
                    or (resp_json.get("auto_executed") if isinstance(resp_json, dict) else False)
                )
                assurance_line = _format_assurance_badge_line(
                    assurance_score=assurance_score,
                    assurance_threshold=assurance_threshold,
                    auto_executed=auto_executed,
                )
                assurance_prefix = f"{assurance_line}\n" if assurance_line else ""

                if order_status == "PENDING_APPROVAL":
                    await tg.answer_callback_query(
                        cb_id, f"Sent for manager approval ({order_id})"
                    )
                    hold_note = ""
                    if res_status == "HELD_5H":
                        until_part = ""
                        if res_expires_raw:
                            try:
                                dt_exp = datetime.fromisoformat(res_expires_raw)
                                until_part = f" until {dt_exp.strftime('%Y-%m-%d %H:%M')}"
                            except ValueError:
                                until_part = f" until {res_expires_raw}"
                        hold_note = (
                            f"\n🔒 Stock reserved for 5h{until_part} (HELD_5H) "
                            f"while your manager reviews!"
                        )
                    elif res_status == "UNRESERVED_DUE_TO_WARNINGS":
                        warn_txt = str(
                            order_obj.get("reservation_warning_message")
                            or "recent expired holds"
                        ).strip()
                        hold_note = (
                            f"\n⚠️ 5h stock hold is paused ({warn_txt}) — "
                            f"ships on approval if stock remains."
                        )
                    confirm_txt = (
                        f"{assurance_prefix}🛎 Order {order_id}{total_part}{deliv_part} is now PENDING_APPROVAL.\n"
                        f"Your manager has been notified to approve with 1 tap!{hold_note}"
                    )
                else:
                    await tg.answer_callback_query(
                        cb_id, f"Order {order_status}! ({order_id})"
                    )
                    confirm_txt = (
                        f"{assurance_prefix}✅ Order {order_id}{total_part}{deliv_part} confirmed ({order_status})!\n"
                        f"Your kitchen order is placed via Aimo Annos."
                    )

                placed_rows: list[list[dict[str, str]]] = []
                undo_row = _build_undo_order_button_row(
                    state=state,
                    order_id=order_id,
                    order_status=order_status,
                    chat_id=chat_id,
                    user_id=user_id,
                )
                if undo_row is not None:
                    placed_rows.append(undo_row)

                await tg.send_message(
                    chat_id,
                    confirm_txt,
                    reply_markup=_finalize_customer_markup(
                        placed_rows, include_back_to_lead=include_lead_btn
                    ),
                )
                return "customer_order_placed"

            if action == "cnl":
                cnl_meta = state.customer_cancel_nonces.get(nonce)
                if cnl_meta is not None:
                    stored_cid = str(cnl_meta.get("chat_id") or "")
                    stored_uid = str(cnl_meta.get("user_id") or "")
                    if stored_cid != str(chat_id) or (
                        stored_uid and stored_uid != str(user_id) and not is_op
                    ):
                        return "unauthorized_callback"
                if not isinstance(cnl_meta, dict) or not cnl_meta.get("order_id"):
                    await tg.answer_callback_query(
                        cb_id,
                        "This undo button expired — check your order on the website.",
                        show_alert=True,
                    )
                    return "unknown_cancel_nonce"

                cancel_order_id = str(cnl_meta["order_id"]).strip()
                include_lead_btn = is_op and state.operator_mode == "customer"
                uid_arg: int | str = int(user_id) if user_id.isdigit() else user_id
                cid_arg: int | str = int(chat_id) if chat_id.isdigit() else chat_id
                try:
                    cancel_res = await aimo.cancel_customer_order(
                        telegram_user_id=uid_arg,
                        telegram_chat_id=cid_arg,
                        order_id=cancel_order_id,
                        bot_token=tg._token,
                    )
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    api_msg = ""
                    with contextlib.suppress(ValueError):
                        err_json = exc.response.json()
                        if isinstance(err_json, dict):
                            err_obj = err_json.get("error")
                            if isinstance(err_obj, dict) and err_obj.get("message"):
                                api_msg = str(err_obj["message"]).strip()
                            elif err_json.get("detail"):
                                api_msg = str(err_json["detail"]).strip()
                    if status_code == 404 and "not linked" in api_msg.lower():
                        state.customer_chats.pop(str(chat_id), None)
                        if is_op:
                            state.operator_mode = "lead"
                            state.save()
                            await tg.answer_callback_query(
                                cb_id,
                                "Customer Telegram link is no longer active — switched back to Team Lead.",
                                show_alert=True,
                            )
                            return "customer_unlinked_404"
                        state.save()
                        return "unauthorized_callback"
                    if status_code == 409:
                        detail = api_msg or "already past delivery cutoff or already cancelled"
                        friendly_err = f"Order {cancel_order_id} can no longer be cancelled ({detail})."
                    elif api_msg:
                        friendly_err = api_msg
                    else:
                        friendly_err = f"Couldn't cancel order {cancel_order_id} right now — please check the website."
                    with contextlib.suppress(httpx.HTTPError, RuntimeError):
                        await tg.answer_callback_query(cb_id, friendly_err[:190], show_alert=True)
                    await tg.send_message(
                        chat_id,
                        f"⚠️ {friendly_err}",
                        reply_markup=_finalize_customer_markup(
                            [], include_back_to_lead=include_lead_btn
                        ),
                    )
                    return (
                        "customer_order_cancel_conflict"
                        if status_code == 409
                        else "customer_order_cancel_error"
                    )
                except httpx.HTTPError:
                    with contextlib.suppress(httpx.HTTPError, RuntimeError):
                        await tg.answer_callback_query(
                            cb_id,
                            CUSTOMER_TIMEOUT_FALLBACK_MESSAGE,
                            show_alert=True,
                        )
                    await tg.send_message(
                        chat_id,
                        CUSTOMER_TIMEOUT_FALLBACK_MESSAGE,
                        reply_markup=_finalize_customer_markup(
                            [], include_back_to_lead=include_lead_btn
                        ),
                    )
                    return "customer_order_cancel_timeout"

                ord_out = (
                    cancel_res.get("order")
                    if isinstance(cancel_res.get("order"), dict)
                    else cancel_res
                )
                resolved_id = str(
                    ord_out.get("id") or ord_out.get("order_id") or cancel_order_id
                )
                confirm_cancel_txt = f"↩️ Order {resolved_id} cancelled."
                await tg.answer_callback_query(cb_id, confirm_cancel_txt)
                await tg.send_message(
                    chat_id,
                    confirm_cancel_txt,
                    reply_markup=_finalize_customer_markup(
                        [], include_back_to_lead=include_lead_btn
                    ),
                )
                return "customer_order_cancelled"

            if action == "opt":
                opt_nonce, _, idx_str = nonce.partition(":")
                opt_meta = state.customer_option_nonces.get(opt_nonce)
                opts_list = (opt_meta or {}).get("options") if isinstance(opt_meta, dict) else None
                if (
                    not isinstance(opt_meta, dict)
                    or str(opt_meta.get("chat_id") or "") != str(chat_id)
                    or not idx_str.isdigit()
                    or not isinstance(opts_list, list)
                    or int(idx_str) >= len(opts_list)
                ):
                    await tg.answer_callback_query(
                        cb_id,
                        "That option expired — please type your choice.",
                        show_alert=True,
                    )
                    return "unknown_option_nonce"
                chosen_option = str(opts_list[int(idx_str)])
                await tg.answer_callback_query(cb_id, f"Selected: {chosen_option[:150]}")
                return await _handle_customer_chat_turn(
                    message_text=chosen_option,
                    chat_id=chat_id,
                    user_id=user_id,
                    raw_username=raw_username,
                    is_op=is_op,
                    state=state,
                    tg=tg,
                    aimo=aimo,
                )

        # 1c. Opted-in Order Approval / Quarantine Callbacks (`apr`, `rej`, `fix`, `qar`, `qrv`)
        opted_in_list = await aimo.list_opted_in_telegram_recipients(state=state)
        matched_opt_in = match_opted_in_recipient(
            user_id=user_id,
            username=raw_username,
            chat_id=chat_id,
            recipients=opted_in_list,
        )
        has_linked_session = bool(chat_id and str(chat_id) in state.customer_chats)
        pending_meta = state.approval_nonces.get(nonce) if action in ("apr", "rej") else None
        matched_connected_session = False
        if (
            not is_op
            and matched_opt_in is None
            and not has_linked_session
            and isinstance(pending_meta, dict)
            and pending_meta.get("via_bridge")
        ):
            for sess in await aimo.list_connected_sessions():
                if str(sess.get("telegram_chat_id") or "") == str(chat_id) and str(
                    sess.get("telegram_user_id") or chat_id
                ) == str(user_id):
                    matched_connected_session = True
                    break

        if (
            not is_op
            and matched_opt_in is None
            and not has_linked_session
            and not matched_connected_session
        ):
            # Fail closed: never mutate chat state and never send any chat message
            return "unauthorized_callback"

        if matched_opt_in is not None:
            state.record_opted_in_chat(chat_id=chat_id, user_id=user_id, username=raw_username)

        if action in ("qar", "qrv"):
            meta_q = state.approval_nonces.get(nonce)
            if not meta_q or not meta_q.get("agent_id"):
                await tg.answer_callback_query(cb_id, "Security review token expired.", show_alert=True)
                return "unknown_nonce"
            agent_id = str(meta_q["agent_id"])
            decision = "restore" if action == "qar" else "revoke"
            q_resp = await aimo.review_quarantined_agent(
                agent_id=agent_id,
                decision=decision,
                tg_user_id=user_id,
                tg_username=display_username,
            )
            if q_resp.status_code in (200, 201):
                label = "✅ RESTORED" if decision == "restore" else "🛑 PERMANENTLY REVOKED"
                await tg.answer_callback_query(cb_id, f"Agent {agent_id}: {decision}")
                if chat_id and isinstance(msg_id, int):
                    await tg.edit_message_text(
                        chat_id,
                        msg_id,
                        f"{label} via Telegram by @{display_username}\nAgent: {agent_id}",
                    )
                return f"agent_{decision}d"
            await tg.answer_callback_query(
                cb_id,
                f"API error ({q_resp.status_code}) reviewing {agent_id}",
                show_alert=True,
            )
            return "agent_review_error"

        meta = state.approval_nonces.get(nonce)
        if action not in ("apr", "rej", "fix") or not meta:
            await tg.answer_callback_query(cb_id, "Approval token expired or unknown.", show_alert=True)
            return "unknown_nonce"

        order_id = str(meta["order_id"])
        expected_hash = str(meta["content_hash"])

        if action == "fix":
            status_code, res_data = await aimo.rescue_and_place_order(
                order_id=order_id,
                expected_hash=expected_hash,
                nonce=nonce,
                tg_user_id=user_id,
                tg_username=display_username,
            )
            if status_code in (200, 201):
                new_ord = res_data["new_order"]
                new_id = new_ord.get("id")
                new_status = new_ord.get("status")
                new_hash = str(new_ord.get("content_hash") or "")
                savings = float(res_data.get("savings_eur") or 0.0)
                await tg.answer_callback_query(cb_id, f"Rescued -> {new_id} ({new_status})!")
                if chat_id and isinstance(msg_id, int):
                    await tg.edit_message_text(
                        chat_id,
                        msg_id,
                        f"🔧 RESCUED & PLACED via Telegram by @{display_username}\n"
                        f"New Order: {new_id} ({new_status})\n"
                        f"Superseded Draft: {order_id} (REJECTED)\n"
                        f"Saved: €{savings:,.2f}\n"
                        f"New SHA-256 Hash: {new_hash[:16]}…",
                    )
                return "rescued"
            if status_code == 409:
                await tg.answer_callback_query(
                    cb_id,
                    f"409 Conflict Refused: {res_data.get('error')}",
                    show_alert=True,
                )
                return "conflict_409"
            await tg.answer_callback_query(
                cb_id,
                f"Safe rescue aborted ({status_code}): {res_data.get('error')}",
                show_alert=True,
            )
            return "rescue_error"

        use_bridge_decide = bool(
            meta.get("via_bridge") or has_linked_session or matched_connected_session or not aimo.configured
        )

        if action == "apr":
            resp: httpx.Response | None = None
            if use_bridge_decide and chat_id and user_id:
                b_resp = await aimo.decide_customer_order(
                    telegram_user_id=user_id,
                    telegram_chat_id=chat_id,
                    order_id=order_id,
                    decision="approve",
                    content_hash=expected_hash,
                    note=f"Approved via Telegram by @{display_username} (tg_user_id={user_id})",
                    idempotency_key=f"tg_apr_{nonce}",
                )
                if b_resp.status_code not in (403, 404) or not aimo.configured:
                    resp = b_resp
            if resp is None:
                resp = await aimo.approve_order(
                    order_id=order_id,
                    content_hash=expected_hash,
                    nonce=nonce,
                    tg_user_id=user_id,
                    tg_username=display_username,
                )
            if resp.status_code in (200, 201):
                raw_body = resp.json()
                body = (
                    raw_body.get("order")
                    if isinstance(raw_body, dict) and isinstance(raw_body.get("order"), dict)
                    else raw_body
                )
                new_status = body.get("status")
                gw_ref = body.get("gateway_reference") or "—"
                await tg.answer_callback_query(cb_id, f"Approved! Status: {new_status}")
                if chat_id and isinstance(msg_id, int):
                    await tg.edit_message_text(
                        chat_id,
                        msg_id,
                        f"✅ APPROVED via Telegram by @{display_username}\n"
                        f"Order: {order_id}\n"
                        f"Status: {new_status} (Gateway Ref: {gw_ref})\n"
                        f"Verified content_hash: {expected_hash[:16]}…",
                    )
                return "approved"
            if resp.status_code == 409:
                err_payload = resp.json() if resp.content else {}
                err_msg = (
                    (err_payload.get("error") or {}).get("message")
                    if isinstance(err_payload, dict) and isinstance(err_payload.get("error"), dict)
                    else "Order state or content_hash changed."
                ) or "Order state or content_hash changed."
                await tg.answer_callback_query(
                    cb_id,
                    f"409 Conflict Refused: {err_msg}",
                    show_alert=True,
                )
                fresh = await aimo.get_order(order_id) if aimo.configured else None
                if fresh and fresh.get("status") == "PENDING_APPROVAL" and chat_id:
                    new_nonce = state.register_approval_nonce(
                        order_id=order_id,
                        content_hash=str(fresh.get("content_hash") or ""),
                        total_excl_vat_eur=fresh.get("total_excl_vat_eur"),
                        via_bridge=bool(meta.get("via_bridge")),
                    )
                    card_txt, markup = format_approval_card(fresh, new_nonce)
                    await tg.send_message(
                        chat_id,
                        f"⚠️ Refreshed Order Card (previous hash was stale):\n\n{card_txt}",
                        reply_markup=markup,
                    )
                return "conflict_409"
            await tg.answer_callback_query(
                cb_id,
                f"API error ({resp.status_code}) approving {order_id}",
                show_alert=True,
            )
            return "approve_error"

        rej_resp: httpx.Response | None = None
        if use_bridge_decide and chat_id and user_id:
            b_rej = await aimo.decide_customer_order(
                telegram_user_id=user_id,
                telegram_chat_id=chat_id,
                order_id=order_id,
                decision="reject",
                note=f"Rejected via Telegram by @{display_username} (tg_user_id={user_id})",
                idempotency_key=f"tg_rej_{nonce}",
            )
            if b_rej.status_code not in (403, 404) or not aimo.configured:
                rej_resp = b_rej
        if rej_resp is None:
            rej_resp = await aimo.reject_order(
                order_id=order_id,
                nonce=nonce,
                tg_user_id=user_id,
                tg_username=display_username,
            )
        if rej_resp.status_code in (200, 201):
            await tg.answer_callback_query(cb_id, f"Order {order_id} rejected.")
            if chat_id and isinstance(msg_id, int):
                await tg.edit_message_text(
                    chat_id,
                    msg_id,
                    f"❌ REJECTED via Telegram by @{display_username}\nOrder: {order_id}",
                )
            return "rejected"
        await tg.answer_callback_query(
            cb_id,
            f"API error ({rej_resp.status_code}) rejecting {order_id}",
            show_alert=True,
        )
        return "reject_error"

    # 2. Standard private chat message (`message`)
    msg = upd.get("message")
    if not isinstance(msg, dict):
        state.mark_update(update_id)
        return "ignored"

    from_user = msg.get("from") or {}
    if from_user.get("is_bot"):
        state.mark_update(update_id)
        return "bot_ignored"

    user_id = str(from_user.get("id") or "")
    raw_username = str(from_user.get("username") or "")
    display_username = normalize_username(raw_username) or user_id
    chat_obj = msg.get("chat") or {}
    chat_id = str(chat_obj.get("id") or "")
    chat_type = str(chat_obj.get("type") or "")
    msg_id = str(msg.get("message_id") or "")
    text = str(msg.get("text") or msg.get("caption") or "").strip()

    if not state.mark_update(update_id):
        return "duplicate"

    if not is_private_chat(chat_type, chat_id):
        return "unauthorized_silent_drop"

    is_op = is_verified_operator(
        username=raw_username,
        user_id=user_id,
        chat_type=chat_type,
        allowed_user_ids=allowed_user_ids,
    )
    if is_op:
        state.record_operator_chat(chat_id=chat_id, user_id=user_id)

    # 2a. Instant Identity Pairing (`/start link_<token>`) from ANY private chat (`chat_id > 0`)
    link_match = re.match(r"^/start(?:@[A-Za-z0-9_]+)?\s+link_(.*)$", text)
    if link_match is not None:
        raw_tok = link_match.group(1).strip()
        if not raw_tok:
            if is_op:
                await tg.send_message(
                    chat_id,
                    "This Telegram pairing link expired or was already used — generate a fresh QR/link on the website.",
                    reply_to_message_id=msg_id,
                )
                return "link_claim_expired_operator"
            return "unauthorized_silent_drop"

        try:
            claim_resp = await aimo.claim_telegram_link(
                token=raw_tok,
                telegram_user_id=user_id,
                telegram_chat_id=chat_id,
                telegram_username=raw_username,
                bot_token=tg._token,
            )
        except httpx.HTTPError:
            if is_op:
                await tg.send_message(
                    chat_id,
                    CUSTOMER_TIMEOUT_FALLBACK_MESSAGE,
                    reply_to_message_id=msg_id,
                )
                return "link_claim_timeout"
            return "unauthorized_silent_drop"
        if claim_resp.status_code not in (200, 201):
            if is_op:
                await tg.send_message(
                    chat_id,
                    "This Telegram pairing link expired or was already used — generate a fresh QR/link on the website.",
                    reply_to_message_id=msg_id,
                )
                return "link_claim_expired_operator"
            # Strict lock: 0 outbound Telegram messages for non-operator invalid/expired links
            return "unauthorized_silent_drop"

        return await _complete_customer_link_claim(
            claim_resp=claim_resp,
            chat_id=chat_id,
            user_id=user_id,
            raw_username=raw_username,
            display_username=display_username,
            is_op=is_op,
            state=state,
            tg=tg,
            reply_to_message_id=msg_id,
        )

    # 2b. Operator Dual-Mode commands (`/lead`, `/lead <msg>`, `/customer`, `/login`)
    if is_op:
        if re.match(r"^/lead(?:@[A-Za-z0-9_]+)?\s*$", text):
            state.operator_mode = "lead"
            state.save()
            await tg.send_message(
                chat_id,
                "Switched back to Team Lead operator channel.",
                reply_to_message_id=msg_id,
            )
            return "operator_mode_lead"

        lead_msg_match = re.match(r"^/lead(?:@[A-Za-z0-9_]+)?\s+([\s\S]+)$", text)
        if lead_msg_match is not None:
            forced_msg = lead_msg_match.group(1).strip()
            res = await _forward_operator_to_inbox(
                msg=msg,
                override_text=forced_msg,
                user_id=user_id,
                chat_id=chat_id,
                msg_id=msg_id,
                inbox_path=inbox_path,
                tg=tg,
            )
            return res or "logged_to_team_lead"

        if re.match(r"^/customer(?:@[A-Za-z0-9_]+)?\s*$", text):
            cust_entry = state.customer_chats.get(str(chat_id))
            if isinstance(cust_entry, dict):
                state.operator_mode = "customer"
                state.save()
                comp = str(cust_entry.get("company_name") or "AimoAnnos")
                await tg.send_message(
                    chat_id,
                    f"Switched to Customer buyer mode ({comp}). Message what your kitchen needs, or send /lead to switch back.",
                    reply_to_message_id=msg_id,
                    reply_markup=_finalize_customer_markup([], include_back_to_lead=True),
                )
                return "operator_mode_customer"
            await tg.send_message(
                chat_id,
                "No customer account linked yet — tap /login for 1-tap demo buyer pairing or scan a QR/link on the website.",
                reply_to_message_id=msg_id,
            )
            return "operator_customer_unlinked"

        if re.match(r"^/login(?:@[A-Za-z0-9_]+)?\s*$", text):
            await tg.send_message(
                chat_id,
                "🔑 Tap a demo buyer persona below to connect in 1 tap (no browser needed), "
                "or send /login <email> <password> (website QR pairing also works anytime):",
                reply_to_message_id=msg_id,
                reply_markup=build_operator_login_menu_markup(),
            )
            return "operator_login_menu"

        login_args_match = re.match(r"^/login(?:@[A-Za-z0-9_]+)?\s+([\s\S]+)$", text)
        if login_args_match is not None:
            parts = login_args_match.group(1).strip().split(None, 1)
            if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
                await tg.send_message(
                    chat_id,
                    "Usage: /login <email> <password> — or tap a 1-tap demo buyer persona below:",
                    reply_to_message_id=msg_id,
                    reply_markup=build_operator_login_menu_markup(),
                )
                return "operator_login_usage"
            return await _login_and_link_operator_customer(
                email=parts[0].strip(),
                password=parts[1].strip(),
                chat_id=chat_id,
                user_id=user_id,
                raw_username=raw_username,
                display_username=display_username,
                state=state,
                tg=tg,
                aimo=aimo,
                reply_to_message_id=msg_id,
            )

    # 2c. Strict Recipient Lock (configured operator, paired `customer_chats`, or website `opted_in`)
    is_unlink_cmd = bool(re.match(r"^/unlink(?:@[A-Za-z0-9_]+)?\s*$", text))
    opted_in_list: list[dict[str, Any]] | None = None
    if not is_op and not is_unlink_cmd and aimo.configured:
        opted_in_list = await aimo.list_opted_in_telegram_recipients(state=state)

    cust_entry = state.customer_chats.get(str(chat_id))
    cust_uid = str((cust_entry or {}).get("telegram_user_id") or "")
    is_linked_customer = isinstance(cust_entry, dict) and (
        not cust_uid or cust_uid == str(user_id) or is_op
    )

    matched_opt_in: dict[str, Any] | None = None
    if not is_op and opted_in_list is not None:
        matched_opt_in = match_opted_in_recipient(
            user_id=user_id,
            username=raw_username,
            chat_id=chat_id,
            recipients=opted_in_list,
        )

    if not is_op and not is_linked_customer:
        if opted_in_list is None:
            opted_in_list = await aimo.list_opted_in_telegram_recipients(state=state)
            matched_opt_in = match_opted_in_recipient(
                user_id=user_id,
                username=raw_username,
                chat_id=chat_id,
                recipients=opted_in_list,
            )
        if matched_opt_in is None:
            # STRICT HUMAN DIRECTIVE: 0 outbound messages and 0 backend spam for unlinked strangers!
            return "unauthorized_silent_drop"

    if matched_opt_in is not None:
        state.record_opted_in_chat(chat_id=chat_id, user_id=user_id, username=raw_username)
        await aimo.bind_verified_chat_id(
            telegram_user_id=user_id,
            telegram_username=raw_username,
            telegram_chat_id=chat_id,
        )

    # 2d. Customer `/unlink` & `/branch` commands
    if is_unlink_cmd and (is_linked_customer or is_op or matched_opt_in is not None):
        await aimo.unlink_telegram_customer(
            telegram_user_id=user_id,
            telegram_chat_id=chat_id,
            bot_token=tg._token,
        )
        state.customer_chats.pop(str(chat_id), None)
        if not is_op:
            norm_u = normalize_username(raw_username)
            if norm_u:
                state.verified_chats.pop(f"@{norm_u}", None)
            if user_id:
                state.verified_chats.pop(str(user_id), None)
        if is_op:
            state.operator_mode = "lead"
        state.save()
        unlink_msg = (
            "🔌 Unlinked customer test session and switched back to Team Lead operator channel."
            if is_op
            else "🔌 Disconnected this Telegram chat from AimoAnnos. You can reconnect anytime from the website."
        )
        await tg.send_message(chat_id, unlink_msg, reply_to_message_id=msg_id)
        return "customer_unlinked"

    if re.match(r"^/branch(?:es)?(?:@[A-Za-z0-9_]+)?\s*$", text) and is_linked_customer:
        return await _handle_branch_menu(
            chat_id=chat_id,
            user_id=user_id,
            raw_username=raw_username,
            is_op=is_op,
            state=state,
            tg=tg,
            aimo=aimo,
            reply_to_message_id=msg_id,
        )

    if re.match(r"^/login(?:@[A-Za-z0-9_]+)?(?:\s+[\s\S]*)?$", text) and not is_op:
        if is_linked_customer:
            cust_info = state.customer_chats.get(str(chat_id)) or {}
            comp_label = str(cust_info.get("company_name") or "AimoAnnos")
            act_br = (
                cust_info.get("active_branch")
                if isinstance(cust_info.get("active_branch"), dict)
                else {}
            )
            br_label = str(act_br.get("name") or act_br.get("short_name") or "").strip()
            target_desc = (
                f"{comp_label} ({br_label})"
                if br_label and br_label != comp_label
                else comp_label
            )
            await tg.send_message(
                chat_id,
                f"✅ Already connected to {target_desc}. Use /branch to switch kitchen or /unlink to disconnect.",
                reply_to_message_id=msg_id,
            )
            return "customer_already_linked"
        await tg.send_message(
            chat_id,
            "Please pair your account using the QR/link on the AimoAnnos website to connect this Telegram chat.",
            reply_to_message_id=msg_id,
        )
        return "customer_login_web_only"

    if re.match(r"^/(?:start|help)(?:@[A-Za-z0-9_]+)?\s*$", text):
        if is_linked_customer and not is_op and not aimo.configured:
            # Verify non-operator customer session directly when approver JWT is unconfigured
            try:
                probe_resp = await aimo.send_customer_chat(
                    telegram_user_id=user_id,
                    telegram_chat_id=chat_id,
                    telegram_username=raw_username,
                    message=text,
                    bot_token=tg._token,
                )
            except httpx.HTTPError:
                probe_resp = None
            if probe_resp is not None and probe_resp.status_code == 404:
                state.customer_chats.pop(str(chat_id), None)
                norm_u = normalize_username(raw_username)
                if norm_u:
                    state.verified_chats.pop(f"@{norm_u}", None)
                if user_id:
                    state.verified_chats.pop(str(user_id), None)
                state.save()
                return "unauthorized_silent_drop"

        if is_linked_customer and (not is_op or state.operator_mode == "customer"):
            cust_info = state.customer_chats.get(str(chat_id)) or {}
            act_br = (
                cust_info.get("active_branch")
                if isinstance(cust_info.get("active_branch"), dict)
                else {}
            )
            loc_label = str(
                act_br.get("name")
                or act_br.get("short_name")
                or cust_info.get("company_name")
                or "AimoAnnos"
            )
            branches = [b for b in (cust_info.get("branches") or []) if isinstance(b, dict)]
            rows_help: list[list[dict[str, str]]] = []
            if len(branches) > 1:
                rows_help.append([{"text": "📍 Switch Kitchen", "callback_data": "cmd:branch"}])
            include_lead_btn = is_op and state.operator_mode == "customer"
            lead_hint = "\n• /lead — Switch back to Team Lead operator mode." if include_lead_btn else ""
            await tg.send_message(
                chat_id,
                f"👋 Connected to {loc_label}!\n"
                f"Just message me in plain words what your kitchen needs — like '10 L lactose-free milk for tomorrow'.\n"
                f"• /branch — Switch kitchen location.\n"
                f"• /pending — Check orders awaiting manager approval.\n"
                f"• /unlink — Disconnect this Telegram chat.{lead_hint}",
                reply_to_message_id=msg_id,
                reply_markup=_finalize_customer_markup(
                    rows_help, include_back_to_lead=include_lead_btn
                ),
            )
            return "help"

        role_note = (
            "• Direct Team Lead channel active (configured operator verified) — send any normal message to talk to the Team Lead.\n"
            if is_op
            else "• Website opt-in verified — you will receive 1-tap approval cards for your company's orders.\n"
        )
        await tg.send_message(
            chat_id,
            f"👋 Connected to AimoAnnos Telegram Bot!\n"
            f"• Verified Telegram: @{display_username} (id={user_id}, chat_id={chat_id})\n"
            f"{role_note}"
            f"• /pending — List PENDING_APPROVAL orders with 1-tap ✅/❌ buttons.\n"
            f"• /order <request> — Route a natural-language order through the Agentic Service -> Core API.",
            reply_to_message_id=msg_id,
        )
        return "help"

    if text.startswith("/pending"):
        if not aimo.configured:
            await tg.send_message(
                chat_id,
                "⚠️ Approver login not configured yet.",
                reply_to_message_id=msg_id,
            )
            return "pending_unconfigured"
        pending = await aimo.list_pending_orders()
        if not pending:
            await tg.send_message(
                chat_id,
                "✅ No orders currently awaiting approval.",
                reply_to_message_id=msg_id,
            )
            return "pending_empty"
        for order in pending[:5]:
            oid = str(order.get("id"))
            chash = str(order.get("content_hash") or "")
            nonce = state.register_approval_nonce(
                order_id=oid,
                content_hash=chash,
                total_excl_vat_eur=order.get("total_excl_vat_eur"),
            )
            card_txt, markup = format_approval_card(order, nonce)
            await tg.send_message(chat_id, card_txt, reply_markup=markup)
        return "pending_listed"

    if text.startswith("/order "):
        prompt = text[len("/order ") :].strip()
        if prompt and aimo.configured:
            await tg.set_reaction_eyes(chat_id, msg_id)
            resp = await aimo.submit_agentic_order_prompt(
                prompt=prompt,
                tg_user_id=user_id,
                tg_username=display_username,
            )
            route_hdr = resp.headers.get("X-Aimo-Route", "agentic-service")
            if resp.status_code in (200, 201):
                body = resp.json()
                reply = body.get("reply") or (
                    f"Order {body.get('id')} created ({body.get('status')}) via {route_hdr}."
                )
                await tg.send_message(chat_id, f"🤖 [{route_hdr}] {reply}", reply_to_message_id=msg_id)
                return "agentic_order_submitted"
            await tg.send_message(
                chat_id,
                f"⚠️ Order request returned HTTP {resp.status_code}: {resp.text[:300]}",
                reply_to_message_id=msg_id,
            )
            return "agentic_order_error"

    # 2e. Operator attachments (`photo`/`document`) or Operator `"lead"` mode -> `INFORMATIONSHARING.md`
    has_operator_attachment = bool(msg.get("photo") or msg.get("document"))
    if is_op and (state.operator_mode != "customer" or has_operator_attachment or not is_linked_customer):
        if state.operator_mode == "customer" and not is_linked_customer:
            state.operator_mode = "lead"
            state.save()
        logged = await _forward_operator_to_inbox(
            msg=msg,
            override_text=None,
            user_id=user_id,
            chat_id=chat_id,
            msg_id=msg_id,
            inbox_path=inbox_path,
            tg=tg,
        )
        if logged:
            return logged

    # 2f. Linked Customer Buyer Chat (or Operator in `"customer"` mode)
    if is_linked_customer and text:
        return await _handle_customer_chat_turn(
            message_text=text,
            chat_id=chat_id,
            user_id=user_id,
            raw_username=raw_username,
            is_op=is_op,
            state=state,
            tg=tg,
            aimo=aimo,
            reply_to_message_id=msg_id,
        )

    return "opted_in_non_command_ignored"


async def poll_and_push_pending_approvals(
    *,
    state: TelegramStateStore,
    tg: TelegramBotClient,
    aimo: AimoCoreApiClient,
) -> int:
    """Push new `PENDING_APPROVAL` cards ONLY to recipients explicitly opted-in on the website clone."""
    if not aimo.configured:
        return 0
    recipients = await aimo.list_opted_in_telegram_recipients(state=state)
    if not recipients:
        return 0

    # Resolve & verify each opted-in recipient's private chat via `getChat` before sending
    verified_target_chats: list[str] = []
    for rec in recipients:
        candidate_chat = resolve_recipient_chat_id(rec, state)
        if not candidate_chat:
            continue
        try:
            chat_info = await tg.get_chat(candidate_chat)
        except (httpx.HTTPError, RuntimeError):
            continue
        if str(chat_info.get("type") or "") != "private":
            continue
        chat_uid = str(chat_info.get("id") or "")
        chat_uname = normalize_username(str(chat_info.get("username") or ""))
        if match_opted_in_recipient(
            user_id=chat_uid,
            username=chat_uname,
            chat_id=candidate_chat,
            recipients=[rec],
        ) is None:
            continue
        if candidate_chat not in verified_target_chats:
            verified_target_chats.append(candidate_chat)

    if not verified_target_chats:
        return 0

    pushed = 0
    pending = await aimo.list_pending_orders()
    for order in pending:
        oid = str(order.get("id") or "")
        chash = str(order.get("content_hash") or "")
        if not oid or not chash:
            continue
        nonce = state.register_approval_nonce(
            order_id=oid,
            content_hash=chash,
            total_excl_vat_eur=order.get("total_excl_vat_eur"),
        )
        card_txt, markup = format_approval_card(order, nonce)
        for target_chat in verified_target_chats:
            dedup_key = f"{oid}:{target_chat}"
            if state.notified_pending_orders.get(dedup_key) == chash:
                continue
            await tg.send_message(target_chat, card_txt, reply_markup=markup)
            state.notified_pending_orders[dedup_key] = chash
            state.save()
            pushed += 1

    # Also push immediate Human Review alerts for any quarantined/spamming agents
    anomalies = await aimo.list_quarantined_agents()
    for item in anomalies:
        aid = str(item.get("agent_id") or item.get("id") or "")
        aname = str(item.get("agent_name") or item.get("name") or aid)
        reason = str(item.get("reason") or item.get("trigger_reason") or "Suspicious purchasing pattern")
        event_id = str(item.get("audit_event_id") or item.get("quarantined_at") or reason)
        if not aid:
            continue
        q_nonce = secrets.token_hex(6)
        state.approval_nonces[q_nonce] = {"agent_id": aid, "reason": reason}
        alert_txt = (
            f"🚨 AimoAnnos Agent Quarantined (Immediate Review)\n"
            f"Agent: {aname} ({aid})\n"
            f"Reason: {reason}\n\n"
            f"Agent JWT access has been blocked immediately. Choose action:"
        )
        q_markup = {
            "inline_keyboard": [
                [
                    {"text": "🛑 Confirm Revoke", "callback_data": f"qrv:{q_nonce}"},
                    {"text": "✅ Restore Access", "callback_data": f"qar:{q_nonce}"},
                ]
            ]
        }
        for target_chat in verified_target_chats:
            q_key = f"quar:{aid}:{target_chat}"
            if state.notified_pending_orders.get(q_key) == event_id:
                continue
            await tg.send_message(target_chat, alert_txt, reply_markup=q_markup)
            state.notified_pending_orders[q_key] = event_id
            state.save()
            pushed += 1
    return pushed


def format_order_confirmation_card(
    notif: dict[str, Any],
    *,
    cancel_nonce: str | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Format a multi-company `ORDER_CONFIRMED` notification card for a connected Telegram user."""
    summary = str(notif.get("summary_text") or "").strip()
    order = notif.get("order") if isinstance(notif.get("order"), dict) else {}
    lines = order.get("lines") if isinstance(order.get("lines"), list) else []
    branch = notif.get("active_branch") if isinstance(notif.get("active_branch"), dict) else None
    branch_label = str((branch or {}).get("short_name") or (branch or {}).get("name") or "").strip()
    origin = str(notif.get("origin_channel") or "web").strip()

    parts: list[str] = [summary or f"✅ Order confirmed: {order.get('id', '—')}"]
    meta_bits: list[str] = [f"Channel: {origin}"]
    if branch_label:
        meta_bits.append(f"Branch: {branch_label}")
    parts.append(" · ".join(meta_bits))

    line_rows: list[str] = []
    for ln in lines[:6]:
        if not isinstance(ln, dict):
            continue
        qty = ln.get("quantity") if ln.get("quantity") is not None else ln.get("qty")
        qty_txt = f"{float(qty):g}" if isinstance(qty, (int, float)) else str(qty or "1")
        name = str(ln.get("product_name") or ln.get("name") or ln.get("product_id") or "Item")
        sku = str(ln.get("product_id") or "").strip()
        status = str(ln.get("delivery_status") or "ON_TIME").replace("_", " ").title()
        sku_suffix = f" ({sku})" if sku else ""
        line_rows.append(f"• {qty_txt} × {name}{sku_suffix} — {status}")
    if len(lines) > 6:
        line_rows.append(f"• …and {len(lines) - 6} more line(s)")
    if line_rows:
        parts.append("\n".join(line_rows))

    markup: dict[str, Any] | None = None
    if cancel_nonce:
        markup = {
            "inline_keyboard": [
                [{"text": "↩️ Undo order", "callback_data": f"cnl:{cancel_nonce}"}]
            ]
        }
    return "\n\n".join(parts), markup


async def poll_and_push_order_notifications(
    *,
    state: TelegramStateStore,
    tg: TelegramBotClient,
    aimo: AimoCoreApiClient,
    since: str | None = None,
) -> int:
    """Poll `GET /api/telegram/order-notifications` and push confirmations/approvals to connected chats."""
    effective_since = since if since is not None else state.order_notifications_since
    items, server_time = await aimo.list_order_notifications(
        since=effective_since,
        include_same_chat_origin=False,
    )

    pushed = 0
    verified_cache: dict[str, dict[str, Any] | None] = {}
    for notif in items:
        target_chat = str(notif.get("telegram_chat_id") or "").strip()
        notif_id = str(notif.get("notification_id") or "").strip()
        event_type = str(notif.get("event_type") or "").strip()
        order = notif.get("order") if isinstance(notif.get("order"), dict) else {}
        oid = str(order.get("id") or "").strip()
        chash = str(order.get("content_hash") or "").strip()
        if not target_chat or not notif_id or not event_type or not oid:
            continue

        dedup_token = (
            chash
            if event_type == "ORDER_PENDING_APPROVAL" and chash
            else f"{event_type}:{chash or notif.get('event_at') or oid}"
        )
        if state.seen_order_notifications.get(notif_id) == dedup_token:
            continue
        if (
            event_type == "ORDER_PENDING_APPROVAL"
            and chash
            and state.notified_pending_orders.get(f"{oid}:{target_chat}") == chash
        ):
            state.seen_order_notifications[notif_id] = dedup_token
            continue

        if target_chat not in verified_cache:
            try:
                verified_cache[target_chat] = await tg.get_chat(target_chat)
            except (httpx.HTTPError, RuntimeError):
                verified_cache[target_chat] = None
        chat_info = verified_cache[target_chat]
        if not isinstance(chat_info, dict) or str(chat_info.get("type") or "") != "private":
            continue
        chat_uid = str(chat_info.get("id") or "")
        chat_uname = normalize_username(str(chat_info.get("username") or ""))
        expected_uid = str(notif.get("telegram_user_id") or target_chat)
        op_u = normalize_username(OPERATOR_USERNAME)
        if chat_uid != expected_uid and not (op_u and chat_uname == op_u):
            continue

        target_uid = str(notif.get("telegram_user_id") or chat_uid or target_chat)
        company_name = str(notif.get("company_name") or "").strip()

        if event_type == "ORDER_PENDING_APPROVAL" and chash:
            nonce = state.register_approval_nonce(
                order_id=oid,
                content_hash=chash,
                total_excl_vat_eur=order.get("total_excl_vat_eur"),
                via_bridge=True,
            )
            card_txt, markup = format_approval_card(order, nonce)
            if company_name and company_name not in card_txt:
                card_txt = f"🏢 {company_name}\n{card_txt}"
            await tg.send_message(target_chat, card_txt, reply_markup=markup)
            state.notified_pending_orders[f"{oid}:{target_chat}"] = chash
            state.seen_order_notifications[notif_id] = dedup_token
            pushed += 1
        elif event_type == "ORDER_CONFIRMED":
            cnl_nonce: str | None = None
            if notif.get("can_undo_in_telegram"):
                cnl_nonce = state.register_customer_cancel_nonce(
                    order_id=oid,
                    chat_id=target_chat,
                    user_id=target_uid,
                )
            conf_txt, conf_markup = format_order_confirmation_card(notif, cancel_nonce=cnl_nonce)
            await tg.send_message(target_chat, conf_txt, reply_markup=conf_markup)
            state.seen_order_notifications[notif_id] = dedup_token
            pushed += 1
        elif event_type in ("ORDER_CANCELLED", "ORDER_REJECTED"):
            summary_txt = str(notif.get("summary_text") or "").strip()
            if summary_txt:
                await tg.send_message(target_chat, summary_txt)
                state.seen_order_notifications[notif_id] = dedup_token
                pushed += 1

    if since is None and server_time:
        state.order_notifications_since = server_time
    if pushed or (since is None and server_time):
        state.save()
    return pushed


async def run_daemon(
    *,
    token: str,
    allowed_user_ids: set[str],
    state: TelegramStateStore,
    inbox_path: Path,
    aimo: AimoCoreApiClient,
) -> None:
    tg = TelegramBotClient(token)
    try:
        me = await tg.get_me()
        if not state.order_notifications_since:
            state.order_notifications_since = datetime.now(UTC).isoformat()
            state.save()
        op_label = (
            f"@{normalize_username(OPERATOR_USERNAME)}"
            if OPERATOR_USERNAME and OPERATOR_USER_ID
            else "disabled"
        )
        print(
            f"[telegram-bridge] Connected as @{me.get('username')} (id={me.get('id')}). "
            f"Operator mode={op_label} · Approvals=website opt-in & QR sessions · "
            f"Core API={aimo.base_url} (configured={aimo.configured})",
            flush=True,
        )

        async def _approval_watcher() -> None:
            while True:
                await asyncio.sleep(4.0)
                try:
                    await poll_and_push_order_notifications(
                        state=state,
                        tg=tg,
                        aimo=aimo,
                    )
                    await poll_and_push_pending_approvals(
                        state=state,
                        tg=tg,
                        aimo=aimo,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[telegram-bridge] Pending-order poll note: {redact_secrets(str(exc), token)}",
                        flush=True,
                    )

        watcher_task = asyncio.create_task(_approval_watcher())
        try:
            while True:
                try:
                    updates = await tg.get_updates(offset=state.update_offset, timeout=20)
                    for upd in updates:
                        await process_telegram_update(
                            upd,
                            allowed_user_ids=allowed_user_ids,
                            state=state,
                            inbox_path=inbox_path,
                            tg=tg,
                            aimo=aimo,
                        )
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[telegram-bridge] Poll retry in 3s ({redact_secrets(str(exc), token)})",
                        flush=True,
                    )
                    await asyncio.sleep(3.0)
        finally:
            watcher_task.cancel()
    finally:
        await tg.aclose()
        await aimo.aclose()


async def _run_cli(argv: list[str] | None = None) -> int:
    global OPERATOR_USERNAME, OPERATOR_USER_ID

    parser = argparse.ArgumentParser(
        description="Telegram Customer & Approval Bridge for Aimo Annos."
    )
    parser.add_argument("--check", action="store_true", help="Verify Telegram bot token via getMe.")
    sub = parser.add_subparsers(dest="cmd")

    listen_p = sub.add_parser("listen", help="Run continuous getUpdates + website-opt-in approval daemon.")
    listen_p.add_argument("--inbox", type=Path, default=DEFAULT_INBOX_PATH)

    send_p = sub.add_parser(
        "send",
        help="Send a Team Lead reply strictly to the configured operator (TELEGRAM_OPERATOR_USERNAME).",
    )
    send_p.add_argument("--message", "-m", required=True, help="Message text.")
    send_p.add_argument(
        "--to",
        default="",
        help="Recipient (strictly verified against TELEGRAM_OPERATOR_USERNAME).",
    )
    send_p.add_argument(
        "--chat-id",
        default="",
        help="Override chat_id (still verified against TELEGRAM_OPERATOR_USERNAME).",
    )
    send_p.add_argument("--reply-to", default="", help="Optional message_id to reply to.")
    send_p.add_argument(
        "--login-menu",
        action="store_true",
        help="Attach 1-tap demo buyer login inline buttons (lgn:anna / lgn:mikko / lgn:sari).",
    )

    photo_p = sub.add_parser(
        "send-photo",
        help="Send a Team Lead photo strictly to the configured operator (TELEGRAM_OPERATOR_USERNAME).",
    )
    photo_p.add_argument("--photo", "-p", type=Path, required=True, help="Path to PNG/JPG image.")
    photo_p.add_argument("--caption", "-c", default="", help="Optional photo caption (<=1024 chars).")
    photo_p.add_argument(
        "--to",
        default="",
        help="Recipient (strictly verified against TELEGRAM_OPERATOR_USERNAME).",
    )
    photo_p.add_argument(
        "--chat-id",
        default="",
        help="Override chat_id (still verified against TELEGRAM_OPERATOR_USERNAME).",
    )
    photo_p.add_argument("--reply-to", default="", help="Optional message_id to reply to.")

    args = parser.parse_args(argv)
    backend_env_vars = load_kv_file(REPO_ROOT / "backend" / ".env")
    root_env_vars = load_kv_file(DEFAULT_ENV_PATH)
    env_vars = {**backend_env_vars, **root_env_vars}
    demo_email, demo_password = load_demo_user_creds(DEFAULT_DEMO_CREDS_PATH)

    if not OPERATOR_USERNAME:
        OPERATOR_USERNAME = resolve_setting("TELEGRAM_OPERATOR_USERNAME", env_vars).lstrip("@")
    if not OPERATOR_USER_ID:
        OPERATOR_USER_ID = resolve_setting("TELEGRAM_OPERATOR_USER_ID", env_vars)
    explicit_bridge_secret = resolve_setting("TELEGRAM_BRIDGE_SECRET", env_vars)
    if explicit_bridge_secret and not os.environ.get("TELEGRAM_BRIDGE_SECRET"):
        os.environ["TELEGRAM_BRIDGE_SECRET"] = explicit_bridge_secret

    token = resolve_bot_token(env_vars)
    if not token:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "Neither TELEGRAM_BOT_TOKEN nor telegram_api_key is set in environment or .env",
                }
            ),
            file=sys.stderr,
        )
        return 1

    raw_allowed = resolve_setting("TELEGRAM_ALLOWED_USER_IDS", env_vars)
    allowed_users = {u.strip() for u in raw_allowed.split(",") if u.strip()}
    configured_chat_id = resolve_setting("TELEGRAM_CHAT_ID", env_vars)
    base_url = (
        resolve_setting("AIMO_CORE_API_URL", env_vars)
        or resolve_setting("AIMO_BASE_URL", root_env_vars)
        or "http://127.0.0.1:8001"
    )
    approver_email = resolve_setting("TELEGRAM_APPROVER_EMAIL", env_vars, demo_email)
    approver_password = resolve_setting("TELEGRAM_APPROVER_PASSWORD", env_vars, demo_password)

    state = TelegramStateStore(DEFAULT_STATE_PATH)
    op_handle = (
        f"@{normalize_username(OPERATOR_USERNAME)}"
        if OPERATOR_USERNAME and OPERATOR_USER_ID
        else None
    )

    try:
        if args.check:
            tg = TelegramBotClient(token)
            try:
                me = await tg.get_me()
            finally:
                await tg.aclose()
            print(
                json.dumps(
                    {
                        "ok": True,
                        "bot_id": me.get("id"),
                        "bot_username": me.get("username"),
                        "operator_locked_to": op_handle,
                        "operator_chat_id": state.operator_chat_id or configured_chat_id or None,
                        "operator_mode": state.operator_mode,
                        "linked_customer_chats": len(state.customer_chats),
                        "demo_approvals_mode": "website_opt_in_only",
                        "approver_configured": bool(approver_email and approver_password),
                    },
                    indent=2,
                )
            )
            return 0

        if args.cmd == "send":
            tg = TelegramBotClient(token)
            try:
                if not OPERATOR_USERNAME or not OPERATOR_USER_ID:
                    raise PermissionError(
                        "Operator mode is disabled (set TELEGRAM_OPERATOR_USERNAME and TELEGRAM_OPERATOR_USER_ID in .env)."
                    )
                target_chat = args.chat_id.strip() or configured_chat_id or (state.operator_chat_id or "")
                if not target_chat:
                    raise RuntimeError(
                        f"No verified @{OPERATOR_USERNAME} chat_id known yet. "
                        f"Send one message (e.g. /start) from @{OPERATOR_USERNAME} to the bot on Telegram first."
                    )
                sent = await tg.send_verified_operator_message(
                    target_chat,
                    args.message,
                    reply_to_message_id=args.reply_to.strip() or None,
                    reply_markup=build_operator_login_menu_markup() if args.login_menu else None,
                )
                state.operator_chat_id = target_chat
                state.save()
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "verified_recipient": f"@{normalize_username(OPERATOR_USERNAME)}",
                            "chat_id": target_chat,
                            "sent_message_ids": [m.get("message_id") for m in sent],
                        }
                    )
                )
                return 0
            finally:
                await tg.aclose()

        if args.cmd == "send-photo":
            tg = TelegramBotClient(token)
            try:
                if not OPERATOR_USERNAME or not OPERATOR_USER_ID:
                    raise PermissionError(
                        "Operator mode is disabled (set TELEGRAM_OPERATOR_USERNAME and TELEGRAM_OPERATOR_USER_ID in .env)."
                    )
                target_chat = args.chat_id.strip() or configured_chat_id or (state.operator_chat_id or "")
                if not target_chat:
                    raise RuntimeError(
                        f"No verified @{OPERATOR_USERNAME} chat_id known yet. "
                        f"Send one message (e.g. /start) from @{OPERATOR_USERNAME} to the bot on Telegram first."
                    )
                sent_msg = await tg.send_verified_operator_photo(
                    target_chat,
                    args.photo,
                    caption=args.caption,
                    reply_to_message_id=args.reply_to.strip() or None,
                )
                state.operator_chat_id = target_chat
                state.save()
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "verified_recipient": f"@{normalize_username(OPERATOR_USERNAME)}",
                            "chat_id": target_chat,
                            "photo": str(args.photo),
                            "sent_message_ids": [sent_msg.get("message_id")],
                        }
                    )
                )
                return 0
            finally:
                await tg.aclose()

        inbox = getattr(args, "inbox", DEFAULT_INBOX_PATH)
        aimo = AimoCoreApiClient(
            base_url,
            email=approver_email,
            password=approver_password,
            bot_token=token,
        )
        await run_daemon(
            token=token,
            allowed_user_ids=allowed_users,
            state=state,
            inbox_path=inbox,
            aimo=aimo,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        raw_err = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        safe = redact_secrets(raw_err, token, approver_password)
        print(json.dumps({"ok": False, "error": safe}), file=sys.stderr)
        return 1


def main() -> int:
    return asyncio.run(_run_cli())


if __name__ == "__main__":
    raise SystemExit(main())
