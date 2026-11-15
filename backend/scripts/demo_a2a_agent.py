#!/usr/bin/env python3
"""Standalone external Hotel Aurora Kitchen Agent speaking A2A JSON-RPC 2.0."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

_BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CREDENTIALS_PATH = _BACKEND_DIR / ".demo_credentials"


def _is_chilled(option: dict[str, Any]) -> bool:
    storage = str(option.get("storage") or "").lower()
    if storage == "chilled":
        return True
    temp = option.get("temperature_condition")
    return temp in (1, 2, 3, 4)


def _is_uht_or_ambient(option: dict[str, Any]) -> bool:
    storage = str(option.get("storage") or "").lower()
    if storage in ("uht", "ambient"):
        return True
    temp = option.get("temperature_condition")
    name = str(option.get("name") or "").lower()
    return temp == 8 or "uht" in name


def _option_price(option: dict[str, Any]) -> float:
    for key in ("line_total_eur", "unit_price_eur"):
        val = option.get(key)
        if val is not None:
            return float(val)
    return float("inf")


@dataclass(frozen=True)
class KitchenProfile:
    """Decision profile for the external Hotel Aurora Kitchen Agent."""

    has_cold_storage: bool = True
    diet: str = "L"
    max_uht_premium_pct: float = 20.0

    def choose_option(self, options: list[dict[str, Any]]) -> dict[str, Any]:
        """Select the best product option from A2A input-required options per kitchen profile."""
        if not options:
            raise ValueError("No options available to choose from")

        deliverable = [
            opt for opt in options if opt.get("delivery_status") not in ("NOT_DELIVERABLE", "LATE")
        ]
        pool = deliverable or list(options)
        on_time = [opt for opt in pool if opt.get("delivery_status") == "ON_TIME"]
        if on_time:
            pool = on_time

        chilled_opts = sorted(
            [opt for opt in pool if _is_chilled(opt)],
            key=_option_price,
        )
        uht_opts = sorted(
            [opt for opt in pool if _is_uht_or_ambient(opt)],
            key=_option_price,
        )

        if not self.has_cold_storage:
            if uht_opts:
                return uht_opts[0]
            return sorted(pool, key=_option_price)[0]

        if chilled_opts and uht_opts:
            best_chilled = chilled_opts[0]
            best_uht = uht_opts[0]
            chilled_price = _option_price(best_chilled)
            uht_price = _option_price(best_uht)
            if chilled_price > 0 and uht_price < float("inf"):
                premium_pct = ((uht_price - chilled_price) / chilled_price) * 100.0
                if premium_pct <= self.max_uht_premium_pct and uht_price <= chilled_price:
                    return best_uht
            return best_chilled

        if chilled_opts:
            return chilled_opts[0]
        if uht_opts:
            return uht_opts[0]
        return sorted(pool, key=_option_price)[0]


def extract_typed_options(task_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract structured options from an A2A Task result (message data or options artifact)."""
    status = task_result.get("status") or {}
    msg = status.get("message") or {}
    for part in msg.get("parts") or []:
        if isinstance(part, dict) and part.get("kind") == "data":
            data = part.get("data") or {}
            if isinstance(data.get("options"), list):
                return list(data["options"])

    for art in task_result.get("artifacts") or []:
        if isinstance(art, dict) and art.get("name") == "options":
            for part in art.get("parts") or []:
                if isinstance(part, dict) and part.get("kind") == "data":
                    data = part.get("data") or {}
                    if isinstance(data.get("options"), list):
                        return list(data["options"])
    return []


def extract_status_data(task_result: dict[str, Any]) -> dict[str, Any]:
    """Extract the structured status data payload from an A2A Task result."""
    status = task_result.get("status") or {}
    msg = status.get("message") or {}
    for part in msg.get("parts") or []:
        if isinstance(part, dict) and part.get("kind") == "data":
            data = part.get("data")
            if isinstance(data, dict):
                return data
    return {}


def _load_kitchen_credentials(creds_path: Path) -> tuple[str, str]:
    if not creds_path.exists():
        raise FileNotFoundError(
            f"Credentials file not found at {creds_path}. Run scripts/demo_seed.py first."
        )
    raw = json.loads(creds_path.read_text(encoding="utf-8"))
    kitchen = (raw.get("agents") or {}).get("kitchen_agent")
    if not isinstance(kitchen, dict) or not kitchen.get("id") or not kitchen.get("agent_secret"):
        raise ValueError(f"Missing kitchen_agent credentials in {creds_path}")
    return str(kitchen["id"]), str(kitchen["agent_secret"])


def _rpc_call(
    client: httpx.Client,
    a2a_url: str,
    headers: dict[str, str],
    *,
    rpc_id: str,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    resp = client.post(
        a2a_url,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": method,
            "params": params,
        },
    )
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"A2A JSON-RPC error on {method}: {body['error']}")
    result = body.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected A2A response for {method}: {body}")
    return result


def run_a2a_session(
    *,
    base_url: str,
    credentials_path: Path,
    profile: KitchenProfile,
    delivery_date: str = "2026-11-16",
    quantity: float = 1000.0,
    wait_for_approval: bool = True,
    poll_interval: float = 2.0,
    poll_timeout: float = 120.0,
    http_client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Run the full Hotel Aurora Kitchen Agent multi-turn A2A procurement session."""
    clean_base = base_url.rstrip("/")
    agent_id, agent_secret = _load_kitchen_credentials(credentials_path)

    owns_client = http_client is None
    client = http_client if http_client is not None else httpx.Client(timeout=20.0)
    try:
        # 1. Discover gateway capabilities & skills via public Agent Card
        card_resp = client.get(f"{clean_base}/api/.well-known/agent-card.json")
        card_resp.raise_for_status()
        card = card_resp.json()
        raw_url = str(card.get("url") or f"{clean_base}/api/a2a")
        a2a_url = raw_url if http_client is None else f"{clean_base}/api/a2a"
        skill_ids = [s.get("id") for s in (card.get("skills") or [])]
        print(f"[1/5] Discovered Agent Card: {card.get('name')} ({a2a_url})")
        print(f"      Skills: {', '.join(str(s) for s in skill_ids)}")

        # 2. Exchange agent credentials for a scoped Bearer JWT
        tok_resp = client.post(
            f"{clean_base}/api/agent-auth/token",
            json={"agent_id": agent_id, "agent_secret": agent_secret},
        )
        tok_resp.raise_for_status()
        token = tok_resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        print(f"[2/5] Authenticated as {agent_id} (expires {tok_resp.json().get('expires_at')})")

        # 3. Turn 1: send natural-language prep request
        prompt_text = f"[{profile.diet}] {quantity:,.0f} L milk for {delivery_date}"
        print(f"[3/5] Sending A2A message/send: {prompt_text!r}")
        turn1 = _rpc_call(
            client,
            a2a_url,
            headers,
            rpc_id="turn-1",
            method="message/send",
            params={
                "message": {
                    "role": "user",
                    "parts": [
                        {"kind": "text", "text": prompt_text},
                        {
                            "kind": "data",
                            "data": {
                                "requested_delivery_date": delivery_date,
                                "diet": profile.diet,
                                "quantity": quantity,
                            },
                        },
                    ],
                }
            },
        )
        task_id = str(turn1["id"])
        print(f"      Task {task_id} -> state={turn1['state']}")
        if turn1["state"] != "input-required":
            return turn1

        options = extract_typed_options(turn1)
        if not options:
            print("      No clarification options returned; returning task state.")
            return turn1

        print("      Gateway offered typed options:")
        for opt in options:
            print(
                f"        - {opt['product_id']} ({opt.get('storage')}): "
                f"{opt.get('name')} | €{opt.get('unit_price_eur')}/{opt.get('sales_unit')} "
                f"(total €{opt.get('line_total_eur')}) | {opt.get('delivery_status')} "
                f"| CO2={opt.get('co2_kg')} kg ({opt.get('co2_basis')})"
            )

        # 4. Select option programmatically using KitchenProfile
        chosen = profile.choose_option(options)
        print(
            f"[4/5] KitchenProfile(has_cold_storage={profile.has_cold_storage}, "
            f"diet={profile.diet}, max_uht_premium_pct={profile.max_uht_premium_pct}) "
            f"selected {chosen['product_id']} ({chosen.get('storage')}, "
            f"€{chosen.get('line_total_eur')})"
        )
        turn2 = _rpc_call(
            client,
            a2a_url,
            headers,
            rpc_id="turn-2",
            method="message/send",
            params={
                "taskId": task_id,
                "message": {
                    "role": "user",
                    "parts": [
                        {
                            "kind": "text",
                            "text": f"Select {chosen['product_id']} ({chosen.get('storage')})",
                        },
                        {
                            "kind": "data",
                            "data": {
                                "action": "select_option",
                                "product_id": chosen["product_id"],
                                "quantity": chosen.get("quantity", quantity),
                                "requested_delivery_date": delivery_date,
                                "diet": profile.diet,
                            },
                        },
                    ],
                },
            },
        )
        status_data = extract_status_data(turn2)
        print(
            f"      Task {task_id} -> state={turn2['state']} (reason={status_data.get('reason')})"
        )
        if status_data.get("order_id"):
            print(
                f"      Order {status_data['order_id']} | "
                f"content_hash={status_data.get('content_hash')} | "
                f"rules={status_data.get('rule_ids')}"
            )

        if turn2["state"] != "input-required" or not wait_for_approval:
            if turn2["state"] == "input-required":
                print(
                    "[5/5] Order is PENDING_APPROVAL. Approve in the mobile/web UI "
                    f"or via POST /api/orders/{status_data.get('order_id')}/approve."
                )
            return turn2

        # 5. Poll tasks/get until human approves on mobile/web UI
        print(
            f"[5/5] Polling tasks/get for {task_id} every {poll_interval:.1f}s "
            f"(timeout {poll_timeout:.0f}s) waiting for human approval..."
        )
        deadline = time.monotonic() + poll_timeout
        current = turn2
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            current = _rpc_call(
                client,
                a2a_url,
                headers,
                rpc_id="poll",
                method="tasks/get",
                params={"taskId": task_id},
            )
            print(f"      tasks/get -> state={current['state']}")
            if current["state"] in ("completed", "failed", "canceled"):
                final_data = extract_status_data(current)
                print(
                    f"      Final outcome: state={current['state']}, "
                    f"order_status={final_data.get('order_status')}, "
                    f"gateway_reference={final_data.get('gateway_reference')}"
                )
                return current

        print("      Timed out waiting for human approval.")
        return current
    finally:
        if owns_client:
            client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="External Hotel Aurora Kitchen Agent speaking A2A JSON-RPC 2.0."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("BASE_URL", "http://127.0.0.1:8001"),
        help="Gateway base URL (default: $BASE_URL or http://127.0.0.1:8001).",
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        default=DEFAULT_CREDENTIALS_PATH,
        help="Path to .demo_credentials JSON (default: backend/.demo_credentials).",
    )
    parser.add_argument(
        "--delivery-date",
        default="2026-11-16",
        help="Requested delivery date YYYY-MM-DD (default: 2026-11-16).",
    )
    parser.add_argument(
        "--quantity",
        type=float,
        default=1000.0,
        help="Requested milk quantity (default: 1000).",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        default=False,
        help="Exit immediately after observing PENDING_APPROVAL instead of polling tasks/get.",
    )
    parser.add_argument(
        "--poll",
        action="store_true",
        default=False,
        help="Poll tasks/get until human approval completes (default unless --no-wait).",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between tasks/get polls (default: 2.0).",
    )
    parser.add_argument(
        "--poll-timeout",
        type=float,
        default=120.0,
        help="Max seconds to wait for human approval (default: 120.0).",
    )
    args = parser.parse_args()

    wait = False if args.no_wait else True
    if args.poll and not args.no_wait:
        wait = True

    profile = KitchenProfile(
        has_cold_storage=True,
        diet="L",
        max_uht_premium_pct=20.0,
    )
    final_task = run_a2a_session(
        base_url=args.base_url,
        credentials_path=args.credentials,
        profile=profile,
        delivery_date=args.delivery_date,
        quantity=args.quantity,
        wait_for_approval=wait,
        poll_interval=args.poll_interval,
        poll_timeout=args.poll_timeout,
    )
    if final_task.get("state") == "failed":
        sys.exit(1)


if __name__ == "__main__":
    main()
