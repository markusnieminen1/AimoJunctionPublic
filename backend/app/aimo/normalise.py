"""Normalise Valio Aimo price + availability responses into contract QuoteLine items."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.aimo.client import AimoClient
from app.domain import valio_rules
from app.errors import AppError
from app.models import Product
from app.schemas import LineFlag, QuoteLine
from app.services.co2 import compute_line_co2
from app.services.delivery import compute_delivery_status
from app.services.inventory_holds import get_competing_held_quantities, sync_expired_holds


def _parse_aimo_ts(raw: Any) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            return None
    return clock.to_helsinki(dt)


def _unit_size_in_base(product: Product, unit_id: str | None) -> float | None:
    if not unit_id:
        return None
    for entry in product.units or []:
        if isinstance(entry, dict) and entry.get("unit_id") == unit_id:
            size = entry.get("size_in_base_units")
            if size is not None and float(size) > 0:
                return float(size)
    if unit_id == product.base_unit:
        return 1.0
    return None


def normalise_product_line(
    product: Product,
    price_item: dict[str, Any] | None,
    avail_item: dict[str, Any] | None,
    *,
    quantity: float,
    requested_delivery_date: str,
    now: datetime,
    competing_held_qty: float = 0.0,
) -> QuoteLine:
    """Normalise a single product's price and availability responses into a QuoteLine."""
    flags: list[LineFlag] = []
    qty = float(quantity)

    # Price: always catalogPrice.value (never listPrice)
    catalog_price = price_item.get("catalogPrice") if isinstance(price_item, dict) else None
    if (
        price_item is None
        or price_item.get("error")
        or not isinstance(catalog_price, dict)
        or catalog_price.get("value") is None
    ):
        flags.append("NO_PRICE")
        unit_price_eur: float | None = None
        line_total_eur: float | None = None
        tax_rate: float | None = None
    else:
        unit_price_eur = float(catalog_price["value"])
        line_total_eur = round(unit_price_eur * qty, 2)
        tax_obj = price_item.get("tax") or {}
        raw_tax = tax_obj.get("taxRate") if isinstance(tax_obj, dict) else None
        tax_rate = float(raw_tax) if raw_tax is not None else None

    # Availability
    avail_dict = avail_item if isinstance(avail_item, dict) else {}
    raw_logistic = avail_dict.get("logisticModel")
    logistic_model: Literal["Stock", "Terminal"] | None = (
        raw_logistic if raw_logistic in ("Stock", "Terminal") else None
    )
    closing_dt = _parse_aimo_ts(avail_dict.get("closingTime"))
    closing_time_str = clock.format_helsinki_iso(closing_dt) if closing_dt is not None else None
    schedule_lines = avail_dict.get("scheduleLines") or []
    top_sales_block = bool(avail_dict.get("salesBlock"))

    available_date: str | None = None
    available_qty_state: Literal["FINITE", "UNLIMITED", "TERMINAL_UNKNOWN", "ZERO"] | None = None
    available_amount: float | None = None

    if not schedule_lines:
        flags.append("NOT_DELIVERABLE")
        if top_sales_block:
            flags.append("SALES_BLOCKED")
    else:
        line = schedule_lines[0] if isinstance(schedule_lines[0], dict) else {}
        raw_avail_date = line.get("availableDate")
        available_date = str(raw_avail_date).strip() if raw_avail_date else None

        if top_sales_block or bool(line.get("salesBlock")):
            flags.append("SALES_BLOCKED")

        # Cut-off check: null closingTime or now >= closingTime -> CUTOFF_PASSED
        if closing_dt is None or now >= closing_dt:
            flags.append("CUTOFF_PASSED")

        # Date check: null availableDate or availableDate > requested -> LATE
        if not available_date or available_date > requested_delivery_date:
            flags.append("LATE")

        # Quantity sentinel arithmetic + unit alignment
        amt = float(line.get("availableAmount") or 0.0)
        sched_unit = str(line.get("salesUnit") or product.sales_unit)
        sched_size = _unit_size_in_base(product, sched_unit)
        catalog_size = _unit_size_in_base(product, product.sales_unit)

        if amt == 0:
            available_qty_state = "ZERO"
        elif logistic_model == "Terminal" and amt == valio_rules.TERMINAL_UNKNOWN_QTY:
            available_qty_state = "TERMINAL_UNKNOWN"
        elif (
            amt * (sched_size if sched_size is not None else 1.0)
            >= 0.99 * valio_rules.UNLIMITED_BASE_UNITS
        ):
            available_qty_state = "UNLIMITED"
        else:
            available_qty_state = "FINITE"

        unit_mismatch = bool(sched_unit and product.sales_unit and sched_unit != product.sales_unit)
        if unit_mismatch:
            flags.append("UNIT_MISMATCH")

        if available_qty_state == "TERMINAL_UNKNOWN":
            flags.append("SUPPLIER_QTY_UNKNOWN")
        elif available_qty_state == "ZERO":
            if qty > 0:
                flags.append("PARTIAL")
        elif available_qty_state == "FINITE":
            if not unit_mismatch:
                converted_amt: float | None = amt
            elif sched_size is not None and catalog_size is not None and catalog_size > 0:
                converted_amt = round((amt * sched_size) / catalog_size, 4)
            else:
                converted_amt = None

            if converted_amt is None:
                flags.append("PARTIAL")
            else:
                if competing_held_qty > 0:
                    converted_amt = max(0.0, round(converted_amt - float(competing_held_qty), 4))
                available_amount = converted_amt
                if converted_amt < qty:
                    flags.append("PARTIAL")

    # CO2 estimation
    co2_kg, co2_basis, co2_estimated = compute_line_co2(product, price_item, qty)
    if co2_estimated:
        flags.append("CO2_ESTIMATED")

    # Delivery status + probability (may append LOW_RELIABILITY)
    delivery_status, on_time_probability = compute_delivery_status(
        flags,
        logistic_model,
        product.delivery_reliability,
    )

    dedup_flags = list(dict.fromkeys(flags))
    return QuoteLine(
        product_id=product.id,
        product_name=product.name,
        quantity=qty,
        sales_unit=product.sales_unit,
        unit_price_eur=unit_price_eur,
        line_total_eur=line_total_eur,
        tax_rate=tax_rate,
        available_date=available_date,
        closing_time=closing_time_str,
        logistic_model=logistic_model,
        available_qty_state=available_qty_state,
        available_amount=available_amount,
        delivery_status=delivery_status,
        on_time_probability=on_time_probability,
        co2_kg=co2_kg,
        co2_basis=co2_basis,
        flags=dedup_flags,
    )


def check_products_live(
    db: Session,
    aimo_client: AimoClient,
    product_ids: list[str],
    quantities: dict[str, float] | None,
    requested_delivery_date: str,
    customer_id: str = "123",
    *,
    exclude_order_id: str | None = None,
) -> list[QuoteLine]:
    """Validate IDs against local catalog, fetch live price+availability, and normalise lines."""
    unique_ids = list(dict.fromkeys(product_ids))
    rows = db.scalars(select(Product).where(Product.id.in_(unique_ids))).all()
    product_map = {p.id: p for p in rows}
    missing = [pid for pid in unique_ids if pid not in product_map]
    if missing:
        raise AppError(
            "UNKNOWN_PRODUCT",
            422,
            f"Unknown product IDs: {missing}",
            details={"missing_product_ids": missing},
        )

    now = clock.now()
    sync_expired_holds(db, now=now)
    held_by_pid = get_competing_held_quantities(
        db,
        product_ids=unique_ids,
        requested_delivery_date=requested_delivery_date,
        now=now,
        exclude_order_id=exclude_order_id,
    )

    price_resp = aimo_client.price(
        unique_ids, requested_date=requested_delivery_date, customer=customer_id
    )
    avail_resp = aimo_client.availability(
        unique_ids, requested_date=requested_delivery_date, customer=customer_id
    )

    price_by_id = {
        str(item["productId"]): item
        for item in (price_resp.get("products") or [])
        if isinstance(item, dict) and "productId" in item
    }
    avail_by_id = {
        str(item["productId"]): item
        for item in (avail_resp.get("products") or [])
        if isinstance(item, dict) and "productId" in item
    }

    qty_map = quantities or {}
    return [
        normalise_product_line(
            product_map[pid],
            price_by_id.get(pid),
            avail_by_id.get(pid),
            quantity=float(qty_map.get(pid, 1.0)),
            requested_delivery_date=requested_delivery_date,
            now=now,
            competing_held_qty=held_by_pid.get(pid, 0.0),
        )
        for pid in product_ids
    ]
