"""Idempotent demo seed script creating Hotel Aurora Oy, Anna Korhonen, and 3 demo agents."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

# Allow running directly as `uv run python scripts/demo_seed.py`
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app import clock  # noqa: E402
from app.audit import record_audit  # noqa: E402
from app.auth import hash_secret, verify_secret  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.ids import new_id  # noqa: E402
from app.models import (  # noqa: E402
    Agent,
    AimoPlatformCampaign,
    Company,
    CompanyBranch,
    CompanyBrandPreference,
    GatewaySubmission,
    Order,
    OrderEventModel,
    OrderLineModel,
    Product,
    PromotionRule,
    Quote,
    QuoteLineModel,
    User,
)
from app.schemas import Principal, PrincipalRef, QuoteLine  # noqa: E402
from app.services.orders import compute_content_hash  # noqa: E402

DEFAULT_CREDENTIALS_PATH = _BACKEND_DIR / ".demo_credentials"

DEMO_COMPANY_NAME = "Hotel Aurora Oy"
DEMO_BUSINESS_ID = "1234567-8"
DEMO_USER_NAME = "Anna Korhonen"
DEMO_USER_EMAIL = "anna.korhonen@hotel.fi"
LEGACY_DEMO_USER_EMAIL = "demo@aimoannos.test"
DEMO_USER_PASSWORD = "demo1234"
_SAMPLE_ORDER_NOTE = "[demo-seed] Historical weekly lactose-free dairy restock"

@dataclass(frozen=True)
class ExtraPersonaSpec:
    company_name: str
    business_id: str
    user_name: str
    user_email: str
    legacy_email: str | None
    branches: tuple[tuple[str, str, str, str], ...]


EXTRA_DEMO_PERSONAS: tuple[ExtraPersonaSpec, ...] = (
    ExtraPersonaSpec(
        company_name="Bistro Satama Oy",
        business_id="2345678-9",
        user_name="Mikko Laine",
        user_email="mikko.laine@restaurant.fi",
        legacy_email="mikko@bistrosatama.test",
        branches=(
            (
                "brn_mikko_1",
                "Bistro Satama Hietalahti",
                "Hietalahti Harbour",
                "Hietalahdenranta 6, 00120 Helsinki",
            ),
            (
                "brn_mikko_2",
                "Bistro Satama Kruununhaka",
                "Kruununhaka",
                "Mariankatu 18, 00170 Helsinki",
            ),
        ),
    ),
    ExtraPersonaSpec(
        company_name="Nordic Campus Catering Oy",
        business_id="3456789-0",
        user_name="Sari Virtanen",
        user_email="sari.virtanen@festival.fi",
        legacy_email="sari@nordiccampus.test",
        branches=(
            (
                "brn_sari_1",
                "Suvilahti Festival Main Kitchen",
                "Suvilahti Main",
                "Sörnäisten rantatie 22, 00540 Helsinki",
            ),
            (
                "brn_sari_2",
                "Kumpula Campus Hall",
                "Kumpula Hall",
                "Gustaf Hällströmin katu 2, 00560 Helsinki",
            ),
        ),
    ),
)


@dataclass(frozen=True)
class DemoAgentSpec:
    key: str
    name: str
    description: str
    auto_approve_limit_eur: float
    max_order_value_eur: float
    daily_spend_limit_eur: float
    can_place_orders: bool
    allowed_categories: tuple[str, ...] = ()


DEMO_AGENTS: tuple[DemoAgentSpec, ...] = (
    DemoAgentSpec(
        key="kitchen_agent",
        name="Hotel Aurora Kitchen Agent",
        description="Primary kitchen purchasing agent for Hotel Aurora Oy",
        auto_approve_limit_eur=500.0,
        max_order_value_eur=5000.0,
        daily_spend_limit_eur=3000.0,
        can_place_orders=True,
    ),
    DemoAgentSpec(
        key="reorder_bot",
        name="Kitchen Reorder Bot",
        description="Automated pantry reorder bot for Hotel Aurora Oy",
        auto_approve_limit_eur=500.0,
        max_order_value_eur=5000.0,
        daily_spend_limit_eur=3000.0,
        can_place_orders=True,
    ),
    DemoAgentSpec(
        key="read_only_scout",
        name="Read-only Scout",
        description="Catalog and price scouting agent without order placement authority",
        auto_approve_limit_eur=0.0,
        max_order_value_eur=0.0,
        daily_spend_limit_eur=0.0,
        can_place_orders=False,
    ),
)


def _generate_agent_secret() -> str:
    return f"aks_{secrets.token_urlsafe(32)}"


def _ensure_company_and_user(db: Session, now: Any) -> tuple[Company, User, bool, bool]:
    user = db.scalar(select(User).where(User.email == DEMO_USER_EMAIL))
    if user is None:
        legacy_user = db.scalar(select(User).where(User.email == LEGACY_DEMO_USER_EMAIL))
        if legacy_user is not None:
            legacy_user.email = DEMO_USER_EMAIL
            db.flush()
            user = legacy_user
    company_created = False
    user_created = False

    if user is not None:
        company = db.get(Company, user.company_id)
        if company is None:
            company = Company(
                id=user.company_id,
                name=DEMO_COMPANY_NAME,
                business_id=DEMO_BUSINESS_ID,
                created_at=now,
            )
            db.add(company)
            db.flush()
            company_created = True
        else:
            company.name = DEMO_COMPANY_NAME
            company.business_id = DEMO_BUSINESS_ID

        user.name = DEMO_USER_NAME
        if not verify_secret(DEMO_USER_PASSWORD, user.password_hash):
            user.password_hash = hash_secret(DEMO_USER_PASSWORD)
        db.flush()
        return company, user, company_created, user_created

    company = db.scalars(
        select(Company)
        .where(Company.business_id == DEMO_BUSINESS_ID)
        .order_by(Company.created_at.asc(), Company.id.asc())
    ).first()
    if company is None:
        company = db.scalars(
            select(Company)
            .where(Company.name == DEMO_COMPANY_NAME)
            .order_by(Company.created_at.asc(), Company.id.asc())
        ).first()

    if company is None:
        company = Company(
            id=new_id("cmp_"),
            name=DEMO_COMPANY_NAME,
            business_id=DEMO_BUSINESS_ID,
            created_at=now,
        )
        db.add(company)
        db.flush()
        company_created = True
    else:
        company.name = DEMO_COMPANY_NAME
        company.business_id = DEMO_BUSINESS_ID

    user = User(
        id=new_id("usr_"),
        company_id=company.id,
        name=DEMO_USER_NAME,
        email=DEMO_USER_EMAIL,
        password_hash=hash_secret(DEMO_USER_PASSWORD),
        created_at=now,
    )
    db.add(user)
    db.flush()
    user_created = True

    user_principal = Principal(
        kind="user",
        id=user.id,
        name=user.name,
        company_id=company.id,
        delegated_by=None,
    )
    record_audit(
        db,
        company_id=company.id,
        actor=user_principal,
        action="user.registered",
        entity_type="user",
        entity_id=user.id,
        outcome="ok",
        details={
            "email": user.email,
            "company_id": company.id,
            "company_name": company.name,
            "source": "demo_seed",
        },
    )
    return company, user, company_created, user_created


def _seed_sample_historical_order(
    db: Session,
    *,
    company: Company,
    user: User,
    kitchen_agent: Agent,
    now: Any,
) -> bool:
    existing = db.scalars(
        select(Order)
        .where(Order.company_id == company.id, Order.note == _SAMPLE_ORDER_NOTE)
        .limit(1)
    ).first()
    if existing is not None:
        return False

    product = db.get(Product, "464306") or db.scalars(select(Product).limit(1)).first()
    if product is None:
        return False

    yesterday = now - timedelta(days=1)
    qty = 240.0
    unit_price = 1.01
    line_total = round(qty * unit_price, 2)
    tax_rate = 13.5
    total_incl_vat = round(line_total * (1.0 + tax_rate / 100.0), 2)
    co2_kg_per_kg = float(product.co2_kg_per_kg) if product.co2_kg_per_kg is not None else 1.2
    co2_kg = round(co2_kg_per_kg * qty, 4)
    delivery_date = (yesterday.date() + timedelta(days=2)).isoformat()

    quote_line = QuoteLine(
        product_id=product.id,
        product_name=product.name,
        quantity=qty,
        sales_unit=product.sales_unit or "ST",
        unit_price_eur=unit_price,
        line_total_eur=line_total,
        tax_rate=tax_rate,
        available_date=delivery_date,
        closing_time=clock.format_helsinki_iso(yesterday + timedelta(hours=4)),
        logistic_model="Stock",
        available_qty_state="UNLIMITED",
        available_amount=None,
        delivery_status="ON_TIME",
        on_time_probability=product.delivery_reliability or 0.99,
        co2_kg=co2_kg,
        co2_basis=f"demo historical order: {co2_kg_per_kg:.2f} kg/kg × {qty:g}",
        flags=[],
    )
    policy_payload = {
        "outcome": "AUTO",
        "rules": [
            {
                "rule_id": "R-AGENT-WITHIN-LIMITS",
                "outcome": "AUTO",
                "message": "Order satisfies all agent limits and delivery checks",
            }
        ],
    }

    quote = Quote(
        id=new_id("qte_"),
        company_id=company.id,
        status="USED",
        created_at=yesterday,
        expires_at=yesterday + timedelta(minutes=30),
        requested_delivery_date=delivery_date,
        total_excl_vat_eur=line_total,
        total_incl_vat_eur=total_incl_vat,
        total_co2_kg=co2_kg,
        overall_delivery_status="ON_TIME",
        policy_preview=policy_payload,
        created_by_kind="agent",
        created_by_id=kitchen_agent.id,
        created_by_name=kitchen_agent.name,
        delegated_by_id=user.id,
        delegated_by_name=user.name,
    )
    db.add(quote)
    db.flush()

    db.add(
        QuoteLineModel(
            quote_id=quote.id,
            line_index=0,
            product_id=quote_line.product_id,
            product_name=quote_line.product_name,
            quantity=quote_line.quantity,
            sales_unit=quote_line.sales_unit,
            unit_price_eur=quote_line.unit_price_eur,
            line_total_eur=quote_line.line_total_eur,
            tax_rate=quote_line.tax_rate,
            available_date=quote_line.available_date,
            closing_time=quote_line.closing_time,
            logistic_model=quote_line.logistic_model,
            available_qty_state=quote_line.available_qty_state,
            available_amount=quote_line.available_amount,
            delivery_status=quote_line.delivery_status,
            on_time_probability=quote_line.on_time_probability,
            co2_kg=quote_line.co2_kg,
            co2_basis=quote_line.co2_basis,
            flags=[],
        )
    )

    order_id = new_id("ord_")
    gateway_ref = f"AIMO-DEMO-{order_id[-8:]}"
    content_hash = compute_content_hash([quote_line], line_total, delivery_date)
    agent_actor = Principal(
        kind="agent",
        id=kitchen_agent.id,
        name=kitchen_agent.name,
        company_id=company.id,
        delegated_by=PrincipalRef(id=user.id, name=user.name),
    )

    order = Order(
        id=order_id,
        company_id=company.id,
        quote_id=quote.id,
        status="PLACED",
        created_at=yesterday,
        requested_delivery_date=delivery_date,
        total_excl_vat_eur=line_total,
        total_incl_vat_eur=total_incl_vat,
        total_co2_kg=co2_kg,
        policy=policy_payload,
        created_by_kind="agent",
        created_by_id=kitchen_agent.id,
        created_by_name=kitchen_agent.name,
        delegated_by_id=user.id,
        delegated_by_name=user.name,
        decided_by_kind=None,
        decided_by_id=None,
        decided_by_name=None,
        decided_at=None,
        decision_note=None,
        approval_expires_at=None,
        content_hash=content_hash,
        gateway_reference=gateway_ref,
        note=_SAMPLE_ORDER_NOTE,
        reconcile_attempts=1,
        updated_at=yesterday,
    )
    db.add(order)
    db.flush()

    db.add(
        OrderLineModel(
            order_id=order.id,
            line_index=0,
            product_id=quote_line.product_id,
            product_name=quote_line.product_name,
            quantity=quote_line.quantity,
            sales_unit=quote_line.sales_unit,
            unit_price_eur=quote_line.unit_price_eur,
            line_total_eur=quote_line.line_total_eur,
            tax_rate=quote_line.tax_rate,
            available_date=quote_line.available_date,
            closing_time=quote_line.closing_time,
            logistic_model=quote_line.logistic_model,
            available_qty_state=quote_line.available_qty_state,
            available_amount=quote_line.available_amount,
            delivery_status=quote_line.delivery_status,
            on_time_probability=quote_line.on_time_probability,
            co2_kg=quote_line.co2_kg,
            co2_basis=quote_line.co2_basis,
            flags=[],
        )
    )
    db.add(
        OrderEventModel(
            id=new_id("evt_"),
            order_id=order.id,
            at=yesterday,
            type="order.placed",
            actor=agent_actor.model_dump(mode="json"),
            message=f"Historical demo order placed via simulated gateway ({gateway_ref})",
            request_id=new_id("req_"),
        )
    )
    db.add(
        GatewaySubmission(
            order_id=order.id,
            gateway_reference=gateway_ref,
            payload={"order_id": order.id, "total_excl_vat_eur": line_total},
            status="ACCEPTED",
            created_at=yesterday,
        )
    )
    record_audit(
        db,
        company_id=company.id,
        actor=agent_actor,
        action="order.placed",
        entity_type="order",
        entity_id=order.id,
        outcome="ok",
        details={"gateway_reference": gateway_ref, "source": "demo_seed"},
    )
    return True


def _seed_demo_promotions(
    db: Session,
    *,
    company: Company,
    user: User,
    now: Any,
) -> int:
    """Idempotently seed 2 realistic management promotions for Hotel Aurora Oy."""
    specs = (
        {
            "title": "Valio Chef's Choice — Nordic Dairy",
            "description": (
                "Subtle culinary boost for Nordic Valio dairy & lactose-free "
                "foodservice essentials."
            ),
            "brand_filter": "Valio",
            "category_prefix": None,
            "product_ids": [],
            "boost_weight": 1.6,
            "prefer_on_sale": False,
        },
        {
            "title": "Kitchen Margin — On-Sale Pantry & Cream",
            "description": (
                "Prioritize active supplier discounts (listPrice > catalogPrice) "
                "across dairy & cream."
            ),
            "brand_filter": None,
            "category_prefix": "104",
            "product_ids": [],
            "boost_weight": 1.4,
            "prefer_on_sale": True,
        },
    )
    user_principal = Principal(
        kind="user",
        id=user.id,
        company_id=company.id,
        name=user.name,
        email=user.email,
    )
    created = 0
    for spec in specs:
        existing = db.scalar(
            select(PromotionRule).where(
                PromotionRule.company_id == company.id,
                PromotionRule.title == spec["title"],
            )
        )
        if existing is not None:
            continue
        rule = PromotionRule(
            id=new_id("prm_"),
            company_id=company.id,
            title=str(spec["title"]),
            description=str(spec["description"]),
            brand_filter=spec["brand_filter"],
            category_prefix=spec["category_prefix"],
            product_ids=list(spec["product_ids"]),
            boost_weight=float(spec["boost_weight"]),
            prefer_on_sale=bool(spec["prefer_on_sale"]),
            active=True,
            created_by_user_id=user.id,
            created_at=now,
        )
        db.add(rule)
        db.flush()
        record_audit(
            db,
            company_id=company.id,
            actor=user_principal,
            action="promotion.create",
            entity_type="promotion",
            entity_id=rule.id,
            outcome="ok",
            details={
                "seeded": True,
                "title": rule.title,
                "brand_filter": rule.brand_filter,
                "category_prefix": rule.category_prefix,
                "prefer_on_sale": rule.prefer_on_sale,
                "boost_weight": rule.boost_weight,
            },
        )
        created += 1
    return created


def _ensure_company_brand_pref(
    db: Session,
    *,
    company: Company,
    now: Any,
) -> None:
    """Idempotently ensure category '22' Valio/Oddlygood brand preference on company."""
    existing_pref = db.scalar(
        select(CompanyBrandPreference).where(
            CompanyBrandPreference.company_id == company.id,
            CompanyBrandPreference.category_prefix == "22",
        )
    )
    if existing_pref is None:
        db.add(
            CompanyBrandPreference(
                id=new_id("bprf_"),
                company_id=company.id,
                category_prefix="22",
                category_label="Dairy & Plant-Based Alternatives",
                preferred_brands=["Valio", "Oddlygood"],
                active=True,
                updated_at=now,
            )
        )


def _ensure_company_branches(
    db: Session,
    *,
    company: Company,
    branch_specs: tuple[tuple[str, str, str, str], ...],
    now: Any,
) -> None:
    """Idempotently upsert 2 active CompanyBranch rows for the given company."""
    for idx, (b_id, b_name, b_short, b_addr) in enumerate(branch_specs):
        b_row = db.get(CompanyBranch, b_id)
        if b_row is None:
            db.add(
                CompanyBranch(
                    id=b_id,
                    company_id=company.id,
                    name=b_name,
                    short_name=b_short,
                    address=b_addr,
                    active=True,
                    created_at=now + timedelta(seconds=idx),
                )
            )
        else:
            b_row.company_id = company.id
            b_row.name = b_name
            b_row.short_name = b_short
            b_row.address = b_addr
            b_row.active = True
            b_row.created_at = now + timedelta(seconds=idx)
    db.flush()


def _seed_default_aimo_campaigns_and_brand_prefs(
    db: Session,
    *,
    company: Company,
    user: User,
    now: Any,
) -> None:
    """Seed 2 default invisible Aimo platform campaigns + 1 default company brand preference."""
    campaign_specs: tuple[dict[str, Any], ...] = (
        {
            "title": "Valio Foodservice Dairy Spotlight",
            "brand_filter": "Valio",
            "category_prefix": "22",
            "product_ids": [],
            "boost_weight": 1.8,
        },
        {
            "title": "Oddlygood Plant-Based Barista & Cooking Boost",
            "brand_filter": "Oddlygood",
            "category_prefix": None,
            "product_ids": [],
            "boost_weight": 1.6,
        },
    )
    for spec in campaign_specs:
        existing = db.scalar(
            select(AimoPlatformCampaign).where(AimoPlatformCampaign.title == spec["title"])
        )
        if existing is not None:
            continue
        db.add(
            AimoPlatformCampaign(
                id=new_id("acmp_"),
                title=str(spec["title"]),
                brand_filter=spec["brand_filter"],
                category_prefix=spec["category_prefix"],
                product_ids=list(spec["product_ids"]),
                boost_weight=float(spec["boost_weight"]),
                active=True,
                created_by_user_id=user.id,
                created_at=now,
            )
        )
    _ensure_company_brand_pref(db, company=company, now=now)

    branch_specs: tuple[tuple[str, str, str, str], ...] = (
        (
            "brn_strand",
            "Hilton Helsinki Strand",
            "Hilton Strand",
            "John Stenbergin ranta 4, 00530 Helsinki",
        ),
        (
            "brn_kalastaja",
            "Hilton Helsinki Kalastajantorppa",
            "Hilton Kalastajantorppa",
            "Kalastajatorpantie 1, 00330 Helsinki",
        ),
    )
    _ensure_company_branches(db, company=company, branch_specs=branch_specs, now=now)


def _ensure_extra_persona_environment(
    db: Session,
    *,
    spec: ExtraPersonaSpec,
    now: Any,
    with_sample_orders: bool,
) -> tuple[int, int]:
    """Idempotently ensure an isolated extra Demo environment (0 Telegram settings)."""
    p_user = db.scalar(select(User).where(User.email == spec.user_email))
    if p_user is None and spec.legacy_email:
        legacy_p = db.scalar(select(User).where(User.email == spec.legacy_email))
        if legacy_p is not None:
            legacy_p.email = spec.user_email
            db.flush()
            p_user = legacy_p

    if p_user is not None:
        p_comp = db.get(Company, p_user.company_id)
        if p_comp is None:
            p_comp = Company(
                id=p_user.company_id,
                name=spec.company_name,
                business_id=spec.business_id,
                created_at=now,
            )
            db.add(p_comp)
            db.flush()
        else:
            p_comp.name = spec.company_name
            p_comp.business_id = spec.business_id

        p_user.name = spec.user_name
        if not verify_secret(DEMO_USER_PASSWORD, p_user.password_hash):
            p_user.password_hash = hash_secret(DEMO_USER_PASSWORD)
        db.flush()
    else:
        p_comp = db.scalars(
            select(Company)
            .where(Company.business_id == spec.business_id)
            .order_by(Company.created_at.asc(), Company.id.asc())
        ).first()
        if p_comp is None:
            p_comp = db.scalars(
                select(Company)
                .where(Company.name == spec.company_name)
                .order_by(Company.created_at.asc(), Company.id.asc())
            ).first()
        if p_comp is None:
            p_comp = Company(
                id=new_id("cmp_"),
                name=spec.company_name,
                business_id=spec.business_id,
                created_at=now,
            )
            db.add(p_comp)
            db.flush()
        else:
            p_comp.name = spec.company_name
            p_comp.business_id = spec.business_id

        p_user = User(
            id=new_id("usr_"),
            company_id=p_comp.id,
            name=spec.user_name,
            email=spec.user_email,
            password_hash=hash_secret(DEMO_USER_PASSWORD),
            created_at=now,
        )
        db.add(p_user)
        db.flush()

        p_principal = Principal(
            kind="user",
            id=p_user.id,
            name=p_user.name,
            company_id=p_comp.id,
            delegated_by=None,
        )
        record_audit(
            db,
            company_id=p_comp.id,
            actor=p_principal,
            action="user.registered",
            entity_type="user",
            entity_id=p_user.id,
            outcome="ok",
            details={
                "email": p_user.email,
                "company_id": p_comp.id,
                "company_name": p_comp.name,
                "source": "demo_seed",
            },
        )

    p_principal = Principal(
        kind="user",
        id=p_user.id,
        name=p_user.name,
        company_id=p_comp.id,
        delegated_by=None,
    )

    comp_short = spec.company_name.removesuffix(" Oy")
    extra_agent_specs: tuple[DemoAgentSpec, ...] = (
        DemoAgentSpec(
            key="kitchen_agent",
            name=f"{comp_short} Kitchen Agent",
            description=f"Primary kitchen purchasing agent for {spec.company_name}",
            auto_approve_limit_eur=500.0,
            max_order_value_eur=5000.0,
            daily_spend_limit_eur=3000.0,
            can_place_orders=True,
        ),
        DemoAgentSpec(
            key="reorder_bot",
            name=f"{comp_short} Reorder Bot",
            description=f"Automated pantry reorder bot for {spec.company_name}",
            auto_approve_limit_eur=350.0,
            max_order_value_eur=3500.0,
            daily_spend_limit_eur=2000.0,
            can_place_orders=True,
        ),
        DemoAgentSpec(
            key="read_only_scout",
            name=f"{comp_short} Read-only Scout",
            description=(
                f"Catalog and price scouting agent for {spec.company_name} "
                "without order placement authority"
            ),
            auto_approve_limit_eur=0.0,
            max_order_value_eur=0.0,
            daily_spend_limit_eur=0.0,
            can_place_orders=False,
        ),
    )

    extra_agents_by_key: dict[str, Agent] = {}
    for ag_spec in extra_agent_specs:
        ag_row = db.scalars(
            select(Agent)
            .where(Agent.company_id == p_comp.id, Agent.name == ag_spec.name)
            .order_by(Agent.created_at.asc(), Agent.id.asc())
        ).first()
        if ag_row is None:
            ag_row = Agent(
                id=new_id("agt_"),
                company_id=p_comp.id,
                delegated_by_user_id=p_user.id,
                name=ag_spec.name,
                description=ag_spec.description,
                status="active",
                secret_hash=hash_secret(_generate_agent_secret()),
                auto_approve_limit_eur=ag_spec.auto_approve_limit_eur,
                max_order_value_eur=ag_spec.max_order_value_eur,
                daily_spend_limit_eur=ag_spec.daily_spend_limit_eur,
                allowed_categories=list(ag_spec.allowed_categories),
                max_quantity_per_line=None,
                can_place_orders=ag_spec.can_place_orders,
                expires_at=None,
                created_at=now,
                last_used_at=None,
            )
            db.add(ag_row)
            db.flush()
            record_audit(
                db,
                company_id=p_comp.id,
                actor=p_principal,
                action="agent.created",
                entity_type="agent",
                entity_id=ag_row.id,
                outcome="ok",
                details={
                    "name": ag_row.name,
                    "delegated_by_user_id": p_user.id,
                    "can_place_orders": ag_row.can_place_orders,
                    "source": "demo_seed",
                },
            )
        extra_agents_by_key[ag_spec.key] = ag_row

    promos_added = _seed_demo_promotions(
        db,
        company=p_comp,
        user=p_user,
        now=now,
    )
    _ensure_company_brand_pref(db, company=p_comp, now=now)
    _ensure_company_branches(db, company=p_comp, branch_specs=spec.branches, now=now)

    orders_added = 0
    if with_sample_orders:
        if _seed_sample_historical_order(
            db,
            company=p_comp,
            user=p_user,
            kitchen_agent=extra_agents_by_key["kitchen_agent"],
            now=now,
        ):
            orders_added = 1

    return promos_added, orders_added


def seed_demo(
    db: Session,
    *,
    credentials_path: Path | str | None = DEFAULT_CREDENTIALS_PATH,
    with_sample_orders: bool = False,
    with_extra_personas: bool = False,
) -> dict[str, Any]:
    """Create or update demo company, user, and agents idempotently and rotate agent secrets."""
    now = clock.now()
    company, user, _company_created, _user_created = _ensure_company_and_user(db, now)

    user_principal = Principal(
        kind="user",
        id=user.id,
        name=user.name,
        company_id=company.id,
        delegated_by=None,
    )

    agents_out: dict[str, dict[str, Any]] = {}
    agent_rows: dict[str, Agent] = {}

    for spec in DEMO_AGENTS:
        plain_secret = _generate_agent_secret()
        secret_hashed = hash_secret(plain_secret)
        agent = db.scalars(
            select(Agent)
            .where(Agent.company_id == company.id, Agent.name == spec.name)
            .order_by(Agent.created_at.asc(), Agent.id.asc())
        ).first()

        if agent is None:
            agent = Agent(
                id=new_id("agt_"),
                company_id=company.id,
                delegated_by_user_id=user.id,
                name=spec.name,
                description=spec.description,
                status="active",
                secret_hash=secret_hashed,
                auto_approve_limit_eur=spec.auto_approve_limit_eur,
                max_order_value_eur=spec.max_order_value_eur,
                daily_spend_limit_eur=spec.daily_spend_limit_eur,
                allowed_categories=list(spec.allowed_categories),
                max_quantity_per_line=None,
                can_place_orders=spec.can_place_orders,
                expires_at=None,
                created_at=now,
                last_used_at=None,
            )
            db.add(agent)
            db.flush()
            record_audit(
                db,
                company_id=company.id,
                actor=user_principal,
                action="agent.created",
                entity_type="agent",
                entity_id=agent.id,
                outcome="ok",
                details={
                    "name": agent.name,
                    "delegated_by_user_id": user.id,
                    "can_place_orders": agent.can_place_orders,
                    "source": "demo_seed",
                },
            )
        else:
            agent.delegated_by_user_id = user.id
            agent.description = spec.description
            agent.status = "active"
            agent.secret_hash = secret_hashed
            agent.auto_approve_limit_eur = spec.auto_approve_limit_eur
            agent.max_order_value_eur = spec.max_order_value_eur
            agent.daily_spend_limit_eur = spec.daily_spend_limit_eur
            agent.allowed_categories = list(spec.allowed_categories)
            agent.max_quantity_per_line = None
            agent.can_place_orders = spec.can_place_orders
            agent.expires_at = None
            db.flush()
            record_audit(
                db,
                company_id=company.id,
                actor=user_principal,
                action="agent.secret_rotated",
                entity_type="agent",
                entity_id=agent.id,
                outcome="ok",
                details={"name": agent.name, "source": "demo_seed"},
            )

        agent_rows[spec.key] = agent
        agents_out[spec.key] = {
            "id": agent.id,
            "name": agent.name,
            "agent_secret": plain_secret,
            "can_place_orders": agent.can_place_orders,
            "auto_approve_limit_eur": agent.auto_approve_limit_eur,
            "max_order_value_eur": agent.max_order_value_eur,
            "daily_spend_limit_eur": agent.daily_spend_limit_eur,
            "allowed_categories": list(agent.allowed_categories or []),
        }

    promotions_created = _seed_demo_promotions(
        db,
        company=company,
        user=user,
        now=now,
    )
    _seed_default_aimo_campaigns_and_brand_prefs(
        db,
        company=company,
        user=user,
        now=now,
    )

    sample_orders_created = 0
    if with_sample_orders:
        if _seed_sample_historical_order(
            db,
            company=company,
            user=user,
            kitchen_agent=agent_rows["kitchen_agent"],
            now=now,
        ):
            sample_orders_created += 1

    # Seed extra isolated Demo environments when requested (0 Telegram rows!)
    if with_extra_personas:
        for extra_spec in EXTRA_DEMO_PERSONAS:
            extra_promos, extra_orders = _ensure_extra_persona_environment(
                db,
                spec=extra_spec,
                now=now,
                with_sample_orders=with_sample_orders,
            )
            promotions_created += extra_promos
            sample_orders_created += extra_orders

    db.commit()

    credentials: dict[str, Any] = {
        "company": {
            "id": company.id,
            "name": company.name,
            "business_id": company.business_id,
        },
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "password": DEMO_USER_PASSWORD,
        },
        "agents": agents_out,
        "promotions_created": promotions_created,
        "sample_orders_created": sample_orders_created,
        "seeded_at": clock.format_helsinki_iso(now),
    }

    if credentials_path is not None:
        creds_file = Path(credentials_path)
        creds_file.parent.mkdir(parents=True, exist_ok=True)
        creds_file.write_text(json.dumps(credentials, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(creds_file, 0o600)
        except OSError:
            pass

    return credentials


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed AimoAnnos demo company, user, and agents.")
    parser.add_argument(
        "--credentials",
        type=Path,
        default=DEFAULT_CREDENTIALS_PATH,
        help="Path to write JSON demo credentials (default: backend/.demo_credentials).",
    )
    parser.add_argument(
        "--with-sample-orders",
        action="store_true",
        default=False,
        help="Also insert a historical PLACED sample order for 7-day dashboard KPIs.",
    )
    parser.add_argument(
        "--promotions-only",
        action="store_true",
        default=False,
        help="Only ensure the 2 demo management promotions exist without rotating secrets.",
    )
    args = parser.parse_args()

    init_db()
    with SessionLocal() as db:
        if args.promotions_only:
            company = db.scalar(select(Company).where(Company.name == DEMO_COMPANY_NAME))
            user = db.scalar(select(User).where(User.email == DEMO_USER_EMAIL))
            if user is None:
                user = db.scalar(select(User).where(User.email == LEGACY_DEMO_USER_EMAIL))
                if user is not None:
                    user.email = DEMO_USER_EMAIL
                    db.flush()
            if company is None or user is None:
                raise SystemExit(
                    "Demo company/user not found; run without --promotions-only first."
                )
            now_seed = clock.now()
            created = _seed_demo_promotions(db, company=company, user=user, now=now_seed)
            _seed_default_aimo_campaigns_and_brand_prefs(
                db,
                company=company,
                user=user,
                now=now_seed,
            )
            db.commit()
            print(f"Promotions created for {company.name}: {created}")
            return
        has_catalog_products = db.scalar(select(Product.id).limit(1)) is not None
        creds = seed_demo(
            db,
            credentials_path=args.credentials,
            with_sample_orders=args.with_sample_orders,
            with_extra_personas=True,
        )

    comp = creds["company"]
    usr = creds["user"]
    print(f"Company: {comp['name']} ({comp['id']}, {comp['business_id']})")
    print(f"User:    {usr['name']} <{usr['email']}> / password={usr['password']}")
    print("Agents:")
    for key, info in creds["agents"].items():
        print(
            f"  - [{key}] {info['name']}: "
            f"agent_id={info['id']}  agent_secret={info['agent_secret']}  "
            f"(auto=€{info['auto_approve_limit_eur']:.0f}, "
            f"max=€{info['max_order_value_eur']:.0f}, "
            f"daily=€{info['daily_spend_limit_eur']:.0f}, "
            f"can_place_orders={info['can_place_orders']})"
        )
    if creds.get("sample_orders_created"):
        print(f"Sample historical orders created: {creds['sample_orders_created']}")
    elif args.with_sample_orders and not has_catalog_products:
        print(
            "Note: No catalog products seeded yet — skipped --with-sample-orders "
            "(run scripts/seed_catalog.py first to populate products)."
        )
    print(f"Credentials written to: {args.credentials}")


if __name__ == "__main__":
    main()
