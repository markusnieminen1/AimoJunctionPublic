# Demo Scenarios & Exception Handling

> **Note:** Example SKU IDs below (such as `464306`, `469363`, `463038`, `460013`) match the synthetic fixture catalog seeded locally by `demo_seed.py`. Always discover SKU IDs dynamically from your own seeded catalog via `search_products` before quoting.

## 1. Happy Path -> Human Approval (`1,000 L` Lactose-Free Milk)
Prompt: *"We need 1,000 litres of lactose-free milk by Monday for our hotels."*
1. Call `search_products(q="laktoositon maito", diet="L")`.
   - Verify product `464306` (`sales_unit: "ST"`, `1 L` pack, `FREE_FROM_LACTOSE` -> matches `L`, not `VL`).
2. Call `create_quote(requested_delivery_date="<Monday>", diet="L", lines=[{"product_id": "464306", "quantity": 1000}])`.
   - Confirm `delivery_status: "ON_TIME"`, `unit_price_eur`, `total_excl_vat_eur` (exceeding the agent's `auto_approve_limit_eur`), `co2_kg` + `co2_basis`, and `policy_preview.outcome == "APPROVAL_REQUIRED"` (`R-AUTO-LIMIT`).
3. Call `place_order(quote_id="<quote_id>", diet="L", idempotency_key="ord-<quote_id>-1", note="Monday hotel milk delivery")`.
4. Receive `status: "PENDING_APPROVAL"` with `content_hash` and `approval_expires_at`. Report the order ID and fired rules to the user so a human can approve it in the AimoAnnos UI.

## 2. Partial Stock Exception (`469363`: only 384 of 1,000 available)
1. `create_quote` returns `delivery_status: "AT_RISK"`, `available_qty_state: "FINITE"`, `available_amount: 384`, `flags: ["PARTIAL"]`, and `policy_preview` includes `R-DELIVERY-RISK`.
2. Do **not** blindly order a partial line if the kitchen needs 1,000 L. Call REST:
   `POST /api/products/substitutes` with `{"product_id": "469363", "quantity": 1000, "requested_delivery_date": "<Monday>", "diet": "L"}`.
3. Present returned `ON_TIME` substitutes (e.g., `464306` chilled Stock or `464398` UHT ambient) along with `tradeoffs` and `price_delta_eur`.

## 3. Terminal Cut-off Passed (`463038`) & Undeliverable (`460013`)
- If `flags` includes `CUTOFF_PASSED` or `delivery_status == "LATE"`: explain the passed `closing_time` and offer `POST /api/products/substitutes` or a later `requested_delivery_date`.
- If `delivery_status == "NOT_DELIVERABLE"` (`R-NOT-DELIVERABLE`): `place_order` will return `403 POLICY_BLOCKED`; find an `ON_TIME` substitute first.

## 4. Uncertain Gateway Outcome (`SUBMISSION_UNKNOWN`)
- If `place_order` returns `status: "SUBMISSION_UNKNOWN"` (or `"SUBMITTING"`):
  - **Do NOT** create a second quote or call `place_order` with a new `idempotency_key`.
  - Poll `get_order(order_id)` until the backend reconciler resolves it to `PLACED` or `FAILED`.

## 5. Menu Planning with Budget & Diet (`plan_menu`)
1. Call `plan_menu(pax=60, meal_type="lunch", diet="L,G", prefer_on_sale=true, requested_delivery_date="<date>")`.
2. Review matched Valio recipes, savings vs `listPrice`, CO2 estimate/basis, and `policy_preview`.
3. Confirm quantities and pass `quote_draft_lines` to `create_quote(..., diet="L,G")` before ordering.
