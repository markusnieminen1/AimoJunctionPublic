"""SQLAlchemy 2.0 ORM models compatible with both MariaDB (utf8mb4) and SQLite."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from app.db import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    business_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(191), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    delegated_by_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    secret_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    auto_approve_limit_eur: Mapped[float] = mapped_column(Float, nullable=False)
    max_order_value_eur: Mapped[float] = mapped_column(Float, nullable=False)
    daily_spend_limit_eur: Mapped[float] = mapped_column(Float, nullable=False)
    allowed_categories: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    max_quantity_per_line: Mapped[float | None] = mapped_column(Float, nullable=True)
    can_place_orders: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Category(Base):
    __tablename__ = "categories"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name_en: Mapped[str] = mapped_column(String(255), nullable=False)
    name_fi: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_group: Mapped[str | None] = mapped_column(String(255), nullable=True)
    food: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    confidence: Mapped[str | None] = mapped_column(String(32), nullable=True)
    product_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    names: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    category_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sales_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    base_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    units: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    vendor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country_of_origin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    temperature_condition: Mapped[int | None] = mapped_column(Integer, nullable=True)
    allergens: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    non_allergens: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    labels: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    co2_kg_per_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    delivery_reliability: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Domain enrichment columns
    name_fi: Mapped[str | None] = mapped_column(String(512), nullable=True)
    name_en: Mapped[str | None] = mapped_column(String(512), nullable=True)
    name_sv: Mapped[str | None] = mapped_column(String(512), nullable=True)
    temperature_class: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    allowed_lot_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    variable_unit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    deposits: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    substitutions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    unit_conversions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    allergen_states: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    verified_claims: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    claims: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    nutrients: Mapped[dict[str, list[Any]]] = mapped_column(JSON, nullable=False, default=dict)
    key_ingredients_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_text: Mapped[str] = mapped_column(Text, nullable=False, default="")


class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_delivery_date: Mapped[str] = mapped_column(String(16), nullable=False)
    total_excl_vat_eur: Mapped[float] = mapped_column(Float, nullable=False)
    total_incl_vat_eur: Mapped[float] = mapped_column(Float, nullable=False)
    total_co2_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    overall_delivery_status: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_preview: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by_name: Mapped[str] = mapped_column(String(255), nullable=False)
    delegated_by_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delegated_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    lines: Mapped[list["QuoteLineModel"]] = relationship(
        back_populates="quote",
        cascade="all, delete-orphan",
        order_by="QuoteLineModel.line_index",
    )


class QuoteLineModel(Base):
    __tablename__ = "quote_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quote_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_index: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str] = mapped_column(String(512), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    sales_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    unit_price_eur: Mapped[float | None] = mapped_column(Float, nullable=True)
    line_total_eur: Mapped[float | None] = mapped_column(Float, nullable=True)
    tax_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    available_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    closing_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    logistic_model: Mapped[str | None] = mapped_column(String(32), nullable=True)
    available_qty_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    available_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(32), nullable=False)
    on_time_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    co2_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    co2_basis: Mapped[str | None] = mapped_column(String(512), nullable=True)
    flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    quote: Mapped[Quote] = relationship(back_populates="lines")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quote_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("quotes.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_delivery_date: Mapped[str] = mapped_column(String(16), nullable=False)
    total_excl_vat_eur: Mapped[float] = mapped_column(Float, nullable=False)
    total_incl_vat_eur: Mapped[float] = mapped_column(Float, nullable=False)
    total_co2_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by_name: Mapped[str] = mapped_column(String(255), nullable=False)
    delegated_by_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delegated_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_by_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decided_by_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    gateway_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reconcile_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gateway_attempts: Mapped[int] = synonym("reconcile_attempts")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    lines: Mapped[list["OrderLineModel"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderLineModel.line_index",
    )
    events: Mapped[list["OrderEventModel"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderEventModel.at",
    )


class OrderLineModel(Base):
    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_index: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str] = mapped_column(String(512), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    sales_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    unit_price_eur: Mapped[float | None] = mapped_column(Float, nullable=True)
    line_total_eur: Mapped[float | None] = mapped_column(Float, nullable=True)
    tax_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    available_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    closing_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    logistic_model: Mapped[str | None] = mapped_column(String(32), nullable=True)
    available_qty_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    available_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(32), nullable=False)
    on_time_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    co2_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    co2_basis: Mapped[str | None] = mapped_column(String(512), nullable=True)
    flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    order: Mapped[Order] = relationship(back_populates="lines")


class OrderEventModel(Base):
    __tablename__ = "order_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)

    order: Mapped[Order] = relationship(back_populates="events")


class AuditEventModel(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    actor: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    prev_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hash: Mapped[str | None] = mapped_column(String(128), nullable=True)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("principal_key", "endpoint", "idem_key", name="uq_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    principal_key: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    idem_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GatewaySubmission(Base):
    __tablename__ = "gateway_submissions"

    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    gateway_reference: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACCEPTED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AssistantConversation(Base):
    __tablename__ = "assistant_conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    principal_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class A2ATask(Base):
    __tablename__ = "a2a_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    principal_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="agent")
    principal_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    artifacts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    linked_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PromotionRule(Base):
    __tablename__ = "promotion_rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand_filter: Mapped[str | None] = mapped_column(String(128), nullable=True)
    category_prefix: Mapped[str | None] = mapped_column(String(32), nullable=True)
    product_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    boost_weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.5)
    prefer_on_sale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_by_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserTelegramSetting(Base):
    __tablename__ = "user_telegram_settings"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    company_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InventoryHold(Base):
    """Aimo Platform 5h soft availability reservation for PENDING_APPROVAL FINITE stock lines."""

    __tablename__ = "inventory_holds"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    product_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    requested_delivery_date: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    held_quantity: Mapped[float] = mapped_column(Float, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CompanyHoldWarning(Base):
    """Hanging-order warning recorded when a PENDING_APPROVAL hold expires undecided."""

    __tablename__ = "company_hold_warnings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class AimoPlatformCampaign(Base):
    """Platform-internal Aimo ranking campaign (strictly invisible to buyers/agents)."""

    __tablename__ = "aimo_platform_campaigns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    brand_filter: Mapped[str | None] = mapped_column(String(128), nullable=True)
    category_prefix: Mapped[str | None] = mapped_column(String(32), nullable=True)
    product_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    boost_weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.5)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_by_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CompanyBrandPreference(Base):
    """Company-scoped ordered preferred brands per category prefix."""

    __tablename__ = "company_brand_preferences"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_prefix: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    category_label: Mapped[str] = mapped_column(String(128), nullable=False)
    preferred_brands: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CompanyNegotiationSetting(Base):
    """Company-scoped human toggle for preliminary A2A price negotiation."""

    __tablename__ = "company_negotiation_settings"

    company_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    allow_price_negotiation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_discount_override_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CompanyBranch(Base):
    """Kitchen / hotel branch location belonging to a company (e.g. Hilton Strand)."""

    __tablename__ = "company_branches"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    short_name: Mapped[str] = mapped_column(String(128), nullable=False)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TelegramLinkToken(Base):
    """One-time 15m SHA-256 hashed QR / deep-link pairing token for a human user."""

    __tablename__ = "telegram_link_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TelegramCustomerSession(Base):
    """Per-user customer Telegram buyer session with active branch & conversation state."""

    __tablename__ = "telegram_customer_sessions"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    company_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    telegram_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active_branch_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("company_branches.id", ondelete="SET NULL"),
        nullable=True,
    )
    active_conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pending_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CompanyAssistantSetting(Base):
    """Per-company Order Assurance threshold (50..100, default 80) for assistant auto-execution."""

    __tablename__ = "company_assistant_settings"

    company_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    order_assurance_threshold: Mapped[int] = mapped_column(
        Integer, nullable=False, default=80
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
