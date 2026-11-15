"""Pydantic v2 API schemas matching docs/API_CONTRACT.md."""

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, Field, PlainSerializer, field_validator

from app import clock


def _serialize_helsinki_dt(dt: datetime) -> str:
    return clock.format_helsinki_iso(dt)


def _round_money(value: float) -> float:
    return round(float(value), 2)


def _clean_non_empty(value: str, field_label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_label} must not be blank")
    return cleaned


def _normalize_email(value: str) -> str:
    cleaned = value.strip().lower()
    if "@" not in cleaned or cleaned.startswith("@") or cleaned.endswith("@"):
        raise ValueError("Invalid email address")
    return cleaned


HelsinkiDatetime = Annotated[
    datetime,
    AfterValidator(clock.to_helsinki),
    PlainSerializer(_serialize_helsinki_dt, when_used="json"),
]

Money = Annotated[
    float,
    AfterValidator(_round_money),
]

NonNegativeMoney = Annotated[
    float,
    Field(ge=0),
    AfterValidator(_round_money),
]


class PrincipalRef(BaseModel):
    """Lightweight reference to a delegating user."""

    id: str
    name: str


class Principal(BaseModel):
    """Authenticated caller identity (human user or delegated agent)."""

    kind: Literal["user", "agent"]
    id: str
    name: str
    company_id: str
    delegated_by: PrincipalRef | None = None


class CompanyOut(BaseModel):
    """Public company summary."""

    id: str
    name: str


class RegisterRequest(BaseModel):
    """Payload to register a new company and its initial user."""

    company_name: str = Field(min_length=1, max_length=255)
    business_id: str | None = Field(default=None, max_length=64)
    user_name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=191)
    password: str = Field(min_length=1, max_length=255)

    @field_validator("company_name")
    @classmethod
    def _validate_company_name(cls, v: str) -> str:
        return _clean_non_empty(v, "company_name")

    @field_validator("user_name")
    @classmethod
    def _validate_user_name(cls, v: str) -> str:
        return _clean_non_empty(v, "user_name")

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        return _normalize_email(v)

    @field_validator("business_id")
    @classmethod
    def _validate_business_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        return cleaned or None


class RegisterResponse(BaseModel):
    """Response returned on successful registration."""

    token: str
    user: Principal
    company: CompanyOut


class LoginRequest(BaseModel):
    """Human login credentials."""

    email: str = Field(min_length=3, max_length=191)
    password: str = Field(min_length=1, max_length=255)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        return _normalize_email(v)


class LoginResponse(BaseModel):
    """Response returned on successful login."""

    token: str
    user: Principal


class AgentOut(BaseModel):
    """Agent representation returned by the API."""

    id: str
    name: str
    description: str | None = None
    status: Literal["active", "disabled", "quarantined"]
    auto_approve_limit_eur: Money
    max_order_value_eur: Money
    daily_spend_limit_eur: Money
    allowed_categories: list[str]
    max_quantity_per_line: float | None = None
    can_place_orders: bool
    delegated_by: PrincipalRef
    expires_at: HelsinkiDatetime | None = None
    created_at: HelsinkiDatetime
    last_used_at: HelsinkiDatetime | None = None
    spent_today_eur: Money


class MeResponse(BaseModel):
    """Current caller identity, company, and optional agent record."""

    principal: Principal
    company: CompanyOut
    agent: AgentOut | None = None


class AgentAuthTokenRequest(BaseModel):
    """Client-credentials exchange payload for agent session tokens."""

    agent_id: str = Field(min_length=1, max_length=64)
    agent_secret: str = Field(min_length=1, max_length=255)


class AgentAuthTokenResponse(BaseModel):
    """Short-lived agent session JWT response."""

    token: str
    expires_at: HelsinkiDatetime
    agent: AgentOut


class AgentCreateRequest(BaseModel):
    """Payload to create a delegated purchasing agent."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    auto_approve_limit_eur: NonNegativeMoney
    max_order_value_eur: NonNegativeMoney
    daily_spend_limit_eur: NonNegativeMoney
    allowed_categories: list[str] = Field(default_factory=list)
    max_quantity_per_line: float | None = Field(default=None, gt=0)
    can_place_orders: bool = True
    expires_at: HelsinkiDatetime | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        return _clean_non_empty(v, "name")


class AgentPatchRequest(BaseModel):
    """Partial update payload for an existing agent."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: Literal["active", "disabled"] | None = None
    auto_approve_limit_eur: NonNegativeMoney | None = None
    max_order_value_eur: NonNegativeMoney | None = None
    daily_spend_limit_eur: NonNegativeMoney | None = None
    allowed_categories: list[str] | None = None
    max_quantity_per_line: float | None = Field(default=None, gt=0)
    can_place_orders: bool | None = None
    expires_at: HelsinkiDatetime | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _clean_non_empty(v, "name")


class AgentCreateResponse(BaseModel):
    """Agent creation and secret rotation response (secret shown once)."""

    agent: AgentOut
    agent_secret: str


class AgentListResponse(BaseModel):
    """Paginated list of agents."""

    items: list[AgentOut]
    total: int


class AuditEventOut(BaseModel):
    """Audit trail event."""

    id: str
    at: HelsinkiDatetime
    request_id: str
    actor: Principal | None = None
    action: str
    entity_type: str
    entity_id: str | None = None
    outcome: Literal["ok", "error"]
    details: dict[str, Any] = Field(default_factory=dict)


class AuditListResponse(BaseModel):
    """Paginated list of audit events."""

    items: list[AuditEventOut]
    total: int


class AuditVerifyResponse(BaseModel):
    """Result of verifying a company's audit hash chain."""

    valid: bool
    checked_events: int
    broken_event_id: str | None = None


DeliveryStatus = Literal["ON_TIME", "AT_RISK", "LATE", "NOT_DELIVERABLE", "UNKNOWN"]
LineFlag = Literal[
    "CUTOFF_PASSED",
    "LATE",
    "PARTIAL",
    "UNIT_MISMATCH",
    "SUPPLIER_QTY_UNKNOWN",
    "LOW_RELIABILITY",
    "NO_PRICE",
    "NOT_DELIVERABLE",
    "SALES_BLOCKED",
    "CO2_ESTIMATED",
]

_YYYY_MM_DD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_delivery_date_str(value: str) -> str:
    cleaned = value.strip()
    if not _YYYY_MM_DD_RE.fullmatch(cleaned):
        raise ValueError("requested_delivery_date must be YYYY-MM-DD")
    try:
        date.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError("requested_delivery_date is not a valid calendar date") from exc
    return cleaned


class ProductUnitOut(BaseModel):
    """Unit conversion entry for a product."""

    unit_id: str
    size_in_base_units: float


class ProductOut(BaseModel):
    """Catalog product representation matching API_CONTRACT.md."""

    id: str
    name: str
    names: dict[str, str] = Field(default_factory=dict)
    category: str
    category_name: str | None = None
    sales_unit: str
    base_unit: str
    units: list[ProductUnitOut] = Field(default_factory=list)
    vendor: str | None = None
    brand: str | None = None
    country_of_origin: str | None = None
    temperature_condition: int | None = None
    allergens: list[str] = Field(default_factory=list)
    non_allergens: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    image_url: str | None = None
    co2_kg_per_kg: float | None = None
    delivery_reliability: float | None = None


class ProductListResponse(BaseModel):
    """Paginated list of catalog products."""

    items: list[ProductOut]
    total: int


class CategoryOut(BaseModel):
    """Product category summary."""

    code: str
    name: str
    product_count: int


class CategoryListResponse(BaseModel):
    """List of product categories."""

    items: list[CategoryOut]


class QuoteLine(BaseModel):
    """Normalised live product check / quote line matching API_CONTRACT.md."""

    product_id: str
    product_name: str
    quantity: float
    sales_unit: str
    unit_price_eur: float | None = None
    line_total_eur: Money | None = None
    tax_rate: float | None = None
    available_date: str | None = None
    closing_time: str | None = None
    logistic_model: Literal["Stock", "Terminal"] | None = None
    available_qty_state: Literal["FINITE", "UNLIMITED", "TERMINAL_UNKNOWN", "ZERO"] | None = None
    available_amount: float | None = None
    delivery_status: DeliveryStatus
    on_time_probability: float | None = None
    co2_kg: float | None = None
    co2_basis: str | None = None
    flags: list[LineFlag] = Field(default_factory=list)


class ProductCheckRequest(BaseModel):
    """Request body for POST /api/products/check."""

    product_ids: list[str] = Field(min_length=1, max_length=50)
    quantities: dict[str, float] | None = None
    requested_delivery_date: str

    @field_validator("product_ids")
    @classmethod
    def _validate_product_ids(cls, value: list[str]) -> list[str]:
        return [_clean_non_empty(pid, "product_id") for pid in value]

    @field_validator("quantities")
    @classmethod
    def _validate_quantities(cls, value: dict[str, float] | None) -> dict[str, float] | None:
        if value is None:
            return None
        cleaned: dict[str, float] = {}
        for pid, qty in value.items():
            clean_pid = _clean_non_empty(pid, "quantity product_id")
            if qty <= 0:
                raise ValueError("quantities values must be greater than 0")
            cleaned[clean_pid] = float(qty)
        return cleaned

    @field_validator("requested_delivery_date")
    @classmethod
    def _validate_delivery_date(cls, value: str) -> str:
        return _parse_delivery_date_str(value)


class ProductCheckResponse(BaseModel):
    """Response body for POST /api/products/check."""

    items: list[QuoteLine]


class SubstitutesRequest(BaseModel):
    """Request body for POST /api/products/substitutes."""

    product_id: str = Field(min_length=1, max_length=64)
    quantity: float = Field(gt=0)
    requested_delivery_date: str
    diet: str | None = None

    @field_validator("product_id")
    @classmethod
    def _validate_product_id(cls, value: str) -> str:
        return _clean_non_empty(value, "product_id")

    @field_validator("requested_delivery_date")
    @classmethod
    def _validate_delivery_date(cls, value: str) -> str:
        return _parse_delivery_date_str(value)


SubstituteTier = Literal["BEST", "BETTER", "GOOD"]


class SubstituteCandidate(BaseModel):
    """A feasible diet-safe substitute with live quote line and trade-off notes."""

    product: ProductOut
    quote_line: QuoteLine
    tradeoffs: list[str] = Field(default_factory=list)
    reason: str
    price_delta_eur: float | None = None
    tier: SubstituteTier | None = None


class SubstitutesResponse(BaseModel):
    """Response body for POST /api/products/substitutes."""

    requested_product_id: str
    diet: str | None = None
    items: list[SubstituteCandidate]


PolicyOutcome = Literal["AUTO", "APPROVAL_REQUIRED", "BLOCKED"]
GovernanceGateId = Literal["SAFETY_LOCK", "OPERATIONAL_SANITY", "AUTONOMY_ENVELOPE"]


class RemediationHintOut(BaseModel):
    """Structured one-tap remediation hint for a fired purchasing policy rule."""

    action_type: str
    label_en: str | None = None
    label_fi: str | None = None
    target_product_ids: list[str] = Field(default_factory=list)


class PolicyRuleOut(BaseModel):
    """Single fired purchasing policy rule."""

    rule_id: str
    outcome: PolicyOutcome
    message: str
    gate: GovernanceGateId | None = None
    overridable_via_approval: bool | None = None
    remediation_hint: RemediationHintOut | None = None


class PolicyDecision(BaseModel):
    """Aggregated deterministic purchasing policy decision."""

    outcome: PolicyOutcome
    rules: list[PolicyRuleOut]


class RecipeSanityOffendingLine(BaseModel):
    """Line-level yield excess detail for R-RECIPE-SANITY."""

    product_id: str
    product_name: str
    ordered_qty: float
    unit: str
    expected_qty: float
    excess_ratio: float
    excess_cost_eur: Money


class RecipeSanityWarning(BaseModel):
    """Recipe-aware over-ordering warning attached to QuoteOut and OrderOut."""

    triggered: bool = True
    rule_id: str = "R-RECIPE-SANITY"
    recipe_id: str | None = None
    recipe_name: str
    target_pax: int
    message: str
    offending_lines: list[RecipeSanityOffendingLine] = Field(default_factory=list)


class QuoteLineInput(BaseModel):
    """Single product + quantity item when requesting a quote."""

    product_id: str = Field(min_length=1, max_length=64)
    quantity: float = Field(gt=0)

    @field_validator("product_id")
    @classmethod
    def _validate_product_id(cls, value: str) -> str:
        return _clean_non_empty(value, "product_id")


class QuoteCreateRequest(BaseModel):
    """Request body for POST /api/quotes."""

    requested_delivery_date: str
    lines: list[QuoteLineInput] = Field(min_length=1, max_length=100)
    diet: str | None = None
    recipe_id: str | None = Field(default=None, max_length=128)
    target_pax: int | None = Field(default=None, ge=1, le=50000)

    @field_validator("requested_delivery_date")
    @classmethod
    def _validate_delivery_date(cls, value: str) -> str:
        return _parse_delivery_date_str(value)


class QuoteOut(BaseModel):
    """Locked quote representation matching API_CONTRACT.md Quote."""

    id: str
    status: Literal["OPEN", "USED", "EXPIRED"]
    created_at: HelsinkiDatetime
    expires_at: HelsinkiDatetime
    requested_delivery_date: str
    lines: list[QuoteLine]
    total_excl_vat_eur: Money
    total_incl_vat_eur: Money
    total_co2_kg: float | None = None
    overall_delivery_status: DeliveryStatus
    policy_preview: PolicyDecision
    created_by: Principal
    diet: str | None = None
    recipe_sanity_warning: RecipeSanityWarning | None = None
    negotiated_savings_eur: Money | None = None


OrderStatus = Literal[
    "PENDING_APPROVAL",
    "SUBMITTING",
    "SUBMISSION_UNKNOWN",
    "PLACED",
    "REJECTED",
    "EXPIRED",
    "CANCELLED",
    "FAILED",
]


class OrderEventOut(BaseModel):
    """Timeline entry on an order."""

    at: HelsinkiDatetime
    type: str
    actor: Principal | None = None
    message: str
    request_id: str


class OrderCreateRequest(BaseModel):
    """Request body for POST /api/orders."""

    quote_id: str = Field(min_length=1, max_length=64)
    note: str | None = None
    diet: str | None = None
    recipe_id: str | None = Field(default=None, max_length=128)
    target_pax: int | None = Field(default=None, ge=1, le=50000)

    @field_validator("quote_id")
    @classmethod
    def _validate_quote_id(cls, value: str) -> str:
        return _clean_non_empty(value, "quote_id")


class OrderApproveRequest(BaseModel):
    """Request body for POST /api/orders/{id}/approve."""

    content_hash: str = Field(min_length=1, max_length=128)
    note: str | None = None

    @field_validator("content_hash")
    @classmethod
    def _validate_content_hash(cls, value: str) -> str:
        return _clean_non_empty(value, "content_hash")


class OrderDecisionRequest(BaseModel):
    """Request body for POST /api/orders/{id}/reject and /cancel."""

    note: str | None = None


ReservationStatus = Literal[
    "HELD_5H",
    "HOLD_EXPIRED",
    "UNRESERVED_DUE_TO_WARNINGS",
    "NONE",
]


class OrderOut(BaseModel):
    """Order representation matching API_CONTRACT.md Order."""

    id: str
    status: OrderStatus
    quote_id: str
    created_at: HelsinkiDatetime
    requested_delivery_date: str
    lines: list[QuoteLine]
    total_excl_vat_eur: Money
    total_incl_vat_eur: Money
    total_co2_kg: float | None = None
    policy: PolicyDecision
    created_by: Principal
    decided_by: Principal | None = None
    decided_at: HelsinkiDatetime | None = None
    decision_note: str | None = None
    approval_expires_at: HelsinkiDatetime | None = None
    content_hash: str
    gateway_reference: str | None = None
    events: list[OrderEventOut]
    diet: str | None = None
    recipe_sanity_warning: RecipeSanityWarning | None = None
    reservation_status: ReservationStatus | None = None
    reservation_expires_at: HelsinkiDatetime | None = None
    company_warning_count_7d: int | None = None
    reservation_warning_message: str | None = None
    negotiated_savings_eur: Money | None = None
    assurance_score: int | None = None
    assurance_threshold: int | None = None
    auto_executed: bool = False


class OrderListResponse(BaseModel):
    """Paginated list of orders."""

    items: list[OrderOut]
    total: int


class DashboardResponse(BaseModel):
    """User dashboard KPI summary matching API_CONTRACT.md."""

    pending_approvals: int
    orders_today: int
    spend_today_eur: Money
    spend_7d_eur: Money
    co2_7d_kg: float
    active_agents: int
    recent_orders: list[OrderOut]


GatewaySimulateMode = Literal["none", "timeout_after_accept", "error"]


class GatewayModeRequest(BaseModel):
    """Request body for dev-only POST /api/dev/gateway-mode."""

    mode: GatewaySimulateMode


class GatewayModeResponse(BaseModel):
    """Response body for dev-only POST /api/dev/gateway-mode."""

    mode: GatewaySimulateMode


class AssistantChatRequest(BaseModel):
    """Request body for POST /api/assistant/chat."""

    conversation_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=8000)

    @field_validator("conversation_id")
    @classmethod
    def _validate_conversation_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("message")
    @classmethod
    def _validate_message(cls, value: str) -> str:
        return _clean_non_empty(value, "message")


class AssistantSearchResultsAction(BaseModel):
    """Assistant action carrying catalog search results."""

    type: Literal["search_results"] = "search_results"
    products: list[ProductOut]


class AssistantQuoteAction(BaseModel):
    """Assistant action carrying a locked quote."""

    type: Literal["quote"] = "quote"
    quote: QuoteOut


class AssistantOrderAction(BaseModel):
    """Assistant action carrying a placed or pending-approval order."""

    type: Literal["order"] = "order"
    order: OrderOut


class AssistantClarificationAction(BaseModel):
    """Assistant action requesting disambiguation with structured options."""

    type: Literal["clarification"] = "clarification"
    question: str
    options: list[str] | None = None


class ReasoningStepOut(BaseModel):
    """Structured reasoning step recorded in audit_events.details."""

    step: int
    stage: str
    summary: str
    actor_kind: str
    at: str
    details: dict[str, Any] = Field(default_factory=dict)


class OrderTraceStepOut(BaseModel):
    """Unified timeline entry for GET /api/orders/{order_id}/trace."""

    step: int
    source: Literal["assistant", "a2a", "audit", "order_event", "reasoning"]
    stage: str
    summary: str
    actor_kind: str | None = None
    actor_id: str | None = None
    actor_name: str | None = None
    at: HelsinkiDatetime
    request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class OrderTraceResponse(BaseModel):
    """Unified 'How we got here' trace for an order."""

    order_id: str
    quote_id: str
    status: OrderStatus
    policy_decision: PolicyDecision
    fired_rules: list[PolicyRuleOut] = Field(default_factory=list)
    steps: list[OrderTraceStepOut] = Field(default_factory=list)
    chain_verified: bool
    source_conversation_id: str | None = None
    source_a2a_task_id: str | None = None
    chat_trace: dict[str, Any] | None = None


class GovernanceInvariantOut(BaseModel):
    """Active Layer 1 invariant metadata."""

    rule_id: str
    outcome: str
    description: str
    gate: GovernanceGateId | None = None
    overridable_via_approval: bool | None = None


class GovernanceToolInfoOut(BaseModel):
    """Registered composable AI/MCP tool metadata."""

    name: str
    description: str


class GovernanceSummaryResponse(BaseModel):
    """Company governance summary: policies, agent roles, invariants, tools, promotions, audit."""

    company_id: str
    company_name: str
    policies: dict[str, Any] = Field(default_factory=dict)
    agent_roles: list[AgentOut] = Field(default_factory=list)
    layer1_invariants: list[GovernanceInvariantOut] = Field(default_factory=list)
    assistant_tools: list[GovernanceToolInfoOut] = Field(default_factory=list)
    mcp_tools: list[GovernanceToolInfoOut] = Field(default_factory=list)
    active_promotions_count: int
    audit_chain: AuditVerifyResponse


class PromotionCreateRequest(BaseModel):
    """Request body for POST /api/promotions (human-only)."""

    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    brand_filter: str | None = Field(default=None, max_length=128)
    category_prefix: str | None = Field(default=None, max_length=32)
    product_ids: list[str] = Field(default_factory=list)
    boost_weight: float = Field(default=1.5, ge=0.0, le=100.0)
    prefer_on_sale: bool = False
    active: bool = True

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        return _clean_non_empty(value, "title")

    @field_validator("brand_filter", "category_prefix")
    @classmethod
    def _clean_optional_str(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("product_ids")
    @classmethod
    def _clean_product_ids(cls, value: list[str]) -> list[str]:
        return [pid.strip() for pid in value if pid and pid.strip()]


class PromotionPatchRequest(BaseModel):
    """Partial update payload for PATCH /api/promotions/{promotion_id} (human-only)."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    brand_filter: str | None = Field(default=None, max_length=128)
    category_prefix: str | None = Field(default=None, max_length=32)
    product_ids: list[str] | None = None
    boost_weight: float | None = Field(default=None, ge=0.0, le=100.0)
    prefer_on_sale: bool | None = None
    active: bool | None = None

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_non_empty(value, "title")

    @field_validator("brand_filter", "category_prefix")
    @classmethod
    def _clean_optional_str(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("product_ids")
    @classmethod
    def _clean_product_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [pid.strip() for pid in value if pid and pid.strip()]


class PromotionOut(BaseModel):
    """Management promotion / recommendation rule representation."""

    id: str
    company_id: str
    title: str
    description: str | None = None
    brand_filter: str | None = None
    category_prefix: str | None = None
    product_ids: list[str] = Field(default_factory=list)
    boost_weight: float
    prefer_on_sale: bool
    active: bool
    created_by_user_id: str
    created_at: HelsinkiDatetime


class PromotionListResponse(BaseModel):
    """List of management promotion rules."""

    items: list[PromotionOut]
    total: int


class PromotionDeleteResponse(BaseModel):
    """Deletion confirmation for DELETE /api/promotions/{promotion_id}."""

    deleted: bool = True
    id: str


class DealItemOut(BaseModel):
    """On-sale / promoted catalog item returned by GET /api/products/deals."""

    product: ProductOut
    quote_line: QuoteLine
    list_price_ex_vat: float | None = None
    discount_pct: float = 0.0
    savings_per_unit: float = 0.0
    on_sale: bool = False
    promoted: bool = False
    promotion_reasons: list[str] = Field(default_factory=list)


class DealsResponse(BaseModel):
    """Response body for GET /api/products/deals."""

    items: list[DealItemOut]
    total: int
    requested_delivery_date: str
    promotions_applied: list[str] = Field(default_factory=list)


class MenuPlanRequest(BaseModel):
    """Request body for POST /api/menu/plan."""

    pax: int = Field(ge=1, le=5000)
    meal_type: Literal["breakfast", "lunch", "dinner"] | None = None
    requested_delivery_date: str | None = None
    diet: str | None = None
    prefer_on_sale: bool = True
    include_valio_recipes: bool = True
    max_budget_eur: float | None = Field(default=None, gt=0)

    @field_validator("requested_delivery_date")
    @classmethod
    def _validate_delivery_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        return _parse_delivery_date_str(cleaned)


class MenuRecipeSummaryOut(BaseModel):
    """Matched recipe metadata in a planned menu."""

    id: str
    name: str
    meal_type: Literal["breakfast", "lunch", "dinner"]
    diet_tags: list[str] = Field(default_factory=list)
    chef_note: str


class MenuIngredientSelectionOut(BaseModel):
    """Resolved ingredient line in a planned menu."""

    recipe_id: str
    recipe_name: str
    ingredient_label: str
    product: ProductOut
    quote_line: QuoteLine
    list_price_ex_vat: float | None = None
    discount_pct: float = 0.0
    savings_eur: Money = 0.0
    promoted: bool = False
    promotion_reasons: list[str] = Field(default_factory=list)
    selection_reason: str


class MenuPlanResponse(BaseModel):
    """Response body for POST /api/menu/plan."""

    plan_id: str
    pax: int
    meal_type: Literal["breakfast", "lunch", "dinner"] | None = None
    requested_delivery_date: str
    diet: str | None = None
    recipes: list[MenuRecipeSummaryOut] = Field(default_factory=list)
    items: list[MenuIngredientSelectionOut] = Field(default_factory=list)
    quote_draft_lines: list[QuoteLineInput] = Field(default_factory=list)
    estimated_total_excl_vat_eur: Money = 0.0
    estimated_total_incl_vat_eur: Money = 0.0
    estimated_list_total_excl_vat_eur: Money = 0.0
    estimated_savings_eur: Money = 0.0
    estimated_co2_kg: float | None = None
    budget_exceeded: bool = False
    policy_preview: PolicyDecision
    promotions_applied: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    reasoning_steps: list[ReasoningStepOut] = Field(default_factory=list)


class AssistantDealsAction(BaseModel):
    """Assistant action carrying on-sale & promoted product deals."""

    type: Literal["deals"] = "deals"
    deals: DealsResponse


class AssistantMenuPlanAction(BaseModel):
    """Assistant action carrying a planned restaurant menu."""

    type: Literal["menu_plan"] = "menu_plan"
    menu_plan: MenuPlanResponse


class A2ANegotiationTurn(BaseModel):
    """Single turn in an A2A agent-to-gateway negotiation summary."""

    speaker: str
    message: str
    status: str | None = None


class AssistantRecipeSanityWarningAction(BaseModel):
    """Assistant action carrying an R-RECIPE-SANITY over-ordering warning."""

    type: Literal["recipe_sanity_warning"] = "recipe_sanity_warning"
    warning: RecipeSanityWarning


class AssistantA2ANegotiationAction(BaseModel):
    """Assistant action carrying an A2A negotiation transcript and resulting order."""

    type: Literal["a2a_negotiation"] = "a2a_negotiation"
    summary: str | None = None
    target_pax: int | None = None
    turns: list[A2ANegotiationTurn] = Field(default_factory=list)
    order: OrderOut | None = None


AssistantAction = Annotated[
    AssistantSearchResultsAction
    | AssistantQuoteAction
    | AssistantOrderAction
    | AssistantClarificationAction
    | AssistantDealsAction
    | AssistantMenuPlanAction
    | AssistantRecipeSanityWarningAction
    | AssistantA2ANegotiationAction,
    Field(discriminator="type"),
]


class AssistantChatResponse(BaseModel):
    """Response body for POST /api/assistant/chat."""

    conversation_id: str
    reply: str
    actions: list[AssistantAction] = Field(default_factory=list)
    assurance_score: int | None = None
    assurance_threshold: int = 80
    auto_executed: bool = False
    llm_mode: str = "fake"
    fallback_used: bool = False
    processing_warnings: list[str] = Field(default_factory=list)


A2ATaskState = Literal["working", "input-required", "completed", "failed", "canceled"]


class A2ATraceEntry(BaseModel):
    """Single turn or state-transition entry in an A2A task history."""

    at: HelsinkiDatetime
    state: A2ATaskState
    role: Literal["agent", "gateway"]
    message: str
    data: dict[str, Any] | None = None


class A2ATaskTraceOut(BaseModel):
    """A2A task trace item for GET /api/a2a/tasks matching docs/CONTRACT_CHANGES.md."""

    id: str
    state: A2ATaskState
    principal: Principal
    created_at: HelsinkiDatetime
    updated_at: HelsinkiDatetime
    linked_order_id: str | None = None
    history: list[A2ATraceEntry] = Field(default_factory=list)


class A2ATaskListResponse(BaseModel):
    """Paginated list of A2A task traces."""

    items: list[A2ATaskTraceOut]
    total: int


class A2AAgentSkill(BaseModel):
    """Skill descriptor in the A2A Agent Card."""

    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)


class A2AAgentCapabilities(BaseModel):
    """Supported A2A protocol capabilities."""

    streaming: bool = False
    pushNotifications: bool = False
    stateTransitionHistory: bool = True


class A2ASecurityScheme(BaseModel):
    """Security scheme descriptor in the A2A Agent Card."""

    type: str
    scheme: str
    bearerFormat: str | None = None
    description: str | None = None


class A2ASupportedMatter(BaseModel):
    """High-level procurement matter supported by an AimoAnnos A2A session."""

    id: str
    title: str
    summary: str
    enabled: bool = True
    tools_or_skills: list[str] = Field(default_factory=list)


class A2AAgentCard(BaseModel):
    """Public A2A Agent Card for /.well-known/agent-card.json."""

    name: str
    description: str
    url: str
    version: str
    protocolVersion: str = "0.2.5"
    capabilities: A2AAgentCapabilities
    defaultInputModes: list[str] = Field(default_factory=lambda: ["text/plain", "application/json"])
    defaultOutputModes: list[str] = Field(
        default_factory=lambda: ["application/json", "text/plain"]
    )
    skills: list[A2AAgentSkill]
    supported_matters: list[A2ASupportedMatter] = Field(default_factory=list)
    securitySchemes: dict[str, A2ASecurityScheme]
    security: list[dict[str, list[str]]]


class A2ARpcRequest(BaseModel):
    """JSON-RPC 2.0 request envelope for POST /api/a2a."""

    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class MCPRpcRequest(BaseModel):
    """JSON-RPC 2.0 request envelope for POST /api/mcp (streamable HTTP)."""

    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class CompanyBranchOut(BaseModel):
    """Company kitchen / hotel branch descriptor."""

    id: str
    name: str
    short_name: str
    address: str | None = None


class TelegramOptInOut(BaseModel):
    """Explicit website-clone Telegram opt-in setting for a human user."""

    user_id: str
    company_id: str
    telegram_username: str | None = None
    telegram_chat_id: str | None = None
    enabled: bool = False
    bot_username: str = "your_aimo_bot"
    consented_at: HelsinkiDatetime | None = None
    updated_at: HelsinkiDatetime | None = None
    linked_via_qr: bool = False
    branches: list[CompanyBranchOut] = Field(default_factory=list)
    active_branch_id: str | None = None
    active_branch_name: str | None = None


class TelegramOptInUpdateRequest(BaseModel):
    """Request body for PUT /api/auth/telegram (human user only)."""

    telegram_id: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    consent_confirmed: bool = True

    @field_validator("telegram_id")
    @classmethod
    def _validate_telegram_id(cls, value: str) -> str:
        return _clean_non_empty(value, "telegram_id")


class TelegramRecipientsResponse(BaseModel):
    """Company-scoped opted-in Telegram recipients for scripts/telegram_bridge.py."""

    items: list[TelegramOptInOut] = Field(default_factory=list)


class TelegramVerifyChatRequest(BaseModel):
    """Request body for POST /api/auth/telegram/verify-chat."""

    telegram_user_id: str = ""
    telegram_username: str = ""
    telegram_chat_id: str = Field(min_length=1, max_length=64)


class DemoPersona(BaseModel):
    """Seeded dummy F&B persona returned by dev-only GET /api/auth/demo-personas."""

    email: str
    name: str
    company_name: str
    group: Literal["judge", "founder", "demo"] = "demo"
    role_label: str | None = None


class DemoPersonasResponse(BaseModel):
    """Response body for dev-only GET /api/auth/demo-personas."""

    items: list[DemoPersona] = Field(default_factory=list)


RoutingTargetService = Literal["core-api", "agentic-service"]


class RoutingInspectRequest(BaseModel):
    """Request body for POST /api/routing/inspect."""

    method: str = "POST"
    path: str = Field(min_length=1, max_length=256)
    payload: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)


class RoutingInspectResponse(BaseModel):
    """Deterministic Request-Field Inspector classification result."""

    target_service: RoutingTargetService
    matched_fields: list[str] = Field(default_factory=list)
    structured_fields: list[str] = Field(default_factory=list)
    reason: str
    downstream_flow: list[str] = Field(default_factory=list)


class GovernanceRoutingResponse(BaseModel):
    """Routing boundary descriptor for GET /api/governance/routing."""

    architecture: str
    core_api_description: str
    agentic_service_description: str
    agentic_service_mode: Literal["in-process-asgi", "external-http"]
    agentic_service_url: str | None = None
    structured_fields: list[str]
    agentic_fields: list[str]
    agentic_path_prefixes: list[str]
    loop_guard_header: str
    response_route_header: str


class QuarantinedAgentAlertOut(BaseModel):
    """Quarantined/anomalous agent alert awaiting human review."""

    agent_id: str
    agent_name: str
    status: str
    trigger_reason: str
    rule_code: str | None = None
    quarantined_at: HelsinkiDatetime
    purchasing_characteristics: dict[str, Any] = Field(default_factory=dict)
    recent_request_ids: list[str] = Field(default_factory=list)


class AgentAnomaliesResponse(BaseModel):
    """Response body for GET /api/agents/anomalies."""

    items: list[QuarantinedAgentAlertOut] = Field(default_factory=list)


class AgentQuarantineReviewRequest(BaseModel):
    """Request body for POST /api/agents/{agent_id}/review."""

    action: Literal["restore", "revoke"]
    note: str | None = Field(default=None, max_length=1000)


class AgentQuarantineReviewResponse(BaseModel):
    """Response body for POST /api/agents/{agent_id}/review."""

    agent: AgentOut
    action: Literal["restore", "revoke"]
    reviewed_at: HelsinkiDatetime


class PolicyWhatIfRequest(BaseModel):
    """Request body for POST /api/governance/simulate-policy."""

    agent_id: str = Field(min_length=1, max_length=64)
    auto_approve_limit_eur: float = Field(ge=0)
    max_order_value_eur: float = Field(ge=0)
    daily_spend_limit_eur: float = Field(ge=0)
    max_quantity_per_line: float | None = Field(default=None, gt=0)
    allowed_categories: list[str] | None = None
    sample_size: int = Field(default=20, ge=1, le=20)


class OrderPolicyDiffOut(BaseModel):
    """Per-order outcome comparison in a shadow policy simulation."""

    order_id: str
    total_excl_vat_eur: Money
    before_outcome: PolicyOutcome
    after_outcome: PolicyOutcome
    before_rules: list[str] = Field(default_factory=list)
    after_rules: list[str] = Field(default_factory=list)
    changed: bool = False


class PolicyWhatIfResponse(BaseModel):
    """Response body for POST /api/governance/simulate-policy."""

    evaluated_orders: int
    unchanged_count: int
    newly_auto_approved: int
    newly_requiring_approval: int
    newly_blocked: int
    safety_blocks_preserved: int
    diffs: list[OrderPolicyDiffOut] = Field(default_factory=list)


# ---------- Batch B1: P1 Agent Chaos & Policy Flight Simulator ----------

SimulatorScenarioId = Literal[
    "unit_trap",
    "diet_breach",
    "gateway_timeout_recovery",
    "partial_and_cutoff",
    "tampered_approval_hash",
]


class SimulatorScenarioOut(BaseModel):
    """Metadata describing a canonical Flight Simulator fault/policy scenario."""

    id: SimulatorScenarioId
    title: str
    summary: str
    expected_rules: list[str] = Field(default_factory=list)
    target_product_ids: list[str] = Field(default_factory=list)
    expected_outcome: str


class SimulatorScenariosResponse(BaseModel):
    """Response body for GET /api/dev/simulator/scenarios."""

    scenarios: list[SimulatorScenarioOut] = Field(default_factory=list)


class SimulatorRunRequest(BaseModel):
    """Request body for POST /api/dev/simulator/run."""

    scenario_id: SimulatorScenarioId


class SimulatorRunResponse(BaseModel):
    """Response body for POST /api/dev/simulator/run."""

    scenario_id: SimulatorScenarioId
    title: str
    passed: bool
    quote_id: str | None = None
    order_id: str | None = None
    http_status: int
    error_code: str | None = None
    policy: PolicyDecision | None = None
    order_status_before_reconcile: str | None = None
    order_status_after: str | None = None
    gateway_reference: str | None = None
    substitutes_preview: list[SubstituteCandidate] = Field(default_factory=list)
    audit_chain: AuditVerifyResponse
    request_id: str


# ---------- Batch B2: P2 Pre-Flight Cart Optimizer ----------

OptimizeObjective = Literal["BALANCED", "SAVE_MONEY", "MAX_RELIABILITY", "LOW_CARBON"]


class QuoteOptimizeRequest(BaseModel):
    """Request body for POST /api/quotes/optimize."""

    requested_delivery_date: str
    lines: list[QuoteLineInput] = Field(min_length=1, max_length=25)
    diet: str | None = None
    objective: OptimizeObjective = "BALANCED"
    allow_plant_based_swaps: bool = False

    @field_validator("requested_delivery_date")
    @classmethod
    def _validate_delivery_date(cls, value: str) -> str:
        return _parse_delivery_date_str(value)


class OptimizeSummary(BaseModel):
    """Before/after aggregate metrics for a quote optimization preview."""

    total_excl_vat_eur: Money
    min_on_time_probability: float | None = None
    estimated_co2_kg: float | None = None
    co2_covered_lines: int
    total_lines: int
    overall_delivery_status: DeliveryStatus
    policy_preview: PolicyDecision


class OptimizeDeltas(BaseModel):
    """Honest pairwise deltas between original and optimized cart lines."""

    total_excl_vat_delta_eur: Money
    min_reliability_delta: float | None = None
    comparable_co2_delta_kg: float | None = None
    comparable_co2_lines: int


class LineOptimizationSwap(BaseModel):
    """Single recommended line swap with normalized quantity, deltas, and trade-offs."""

    line_index: int
    original_line: QuoteLine
    recommended_product: ProductOut
    recommended_line: QuoteLine
    suggested_quantity: float
    line_total_delta_eur: Money
    reliability_delta: float | None = None
    co2_delta_kg: float | None = None
    co2_basis_note: str | None = None
    tradeoffs: list[str] = Field(default_factory=list)
    reason: str


class QuoteOptimizeResponse(BaseModel):
    """Response body for POST /api/quotes/optimize."""

    objective: OptimizeObjective
    requested_delivery_date: str
    diet: str | None = None
    before_summary: OptimizeSummary
    after_summary: OptimizeSummary
    deltas: OptimizeDeltas
    swaps: list[LineOptimizationSwap] = Field(default_factory=list)
    unchanged_lines: list[int] = Field(default_factory=list)


# ---------- Batch C: msg=40 Invisible Aimo Campaigns & Brand Preferences ----------


class AimoCampaignOut(BaseModel):
    """Admin-only platform campaign representation (invisible to buyers/agents)."""

    id: str
    title: str
    brand_filter: str | None = None
    category_prefix: str | None = None
    product_ids: list[str] = Field(default_factory=list)
    boost_weight: float
    active: bool
    created_at: HelsinkiDatetime


class AimoCampaignListResponse(BaseModel):
    """List of Aimo platform campaigns for admin panel."""

    items: list[AimoCampaignOut] = Field(default_factory=list)
    total: int


class AimoCampaignCreateRequest(BaseModel):
    """Request body for POST /api/admin/aimo-campaigns (human user only)."""

    title: str = Field(min_length=1, max_length=255)
    brand_filter: str | None = Field(default=None, max_length=128)
    category_prefix: str | None = Field(default=None, max_length=32)
    product_ids: list[str] = Field(default_factory=list, max_length=100)
    boost_weight: float = Field(default=1.5, ge=0.1, le=10.0)
    active: bool = True

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        return _clean_non_empty(value, "title")


class AimoCampaignPatchRequest(BaseModel):
    """Request body for PATCH /api/admin/aimo-campaigns/{id} (human user only)."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    brand_filter: str | None = Field(default=None, max_length=128)
    category_prefix: str | None = Field(default=None, max_length=32)
    product_ids: list[str] | None = Field(default=None, max_length=100)
    boost_weight: float | None = Field(default=None, ge=0.1, le=10.0)
    active: bool | None = None


class AimoCampaignDeleteResponse(BaseModel):
    """Delete acknowledgment for DELETE /api/admin/aimo-campaigns/{id}."""

    id: str
    deleted: bool = True


class BrandPreferenceOut(BaseModel):
    """Company-scoped ordered brand preference per category prefix."""

    id: str
    company_id: str
    category_prefix: str
    category_label: str
    preferred_brands: list[str] = Field(default_factory=list)
    active: bool
    updated_at: HelsinkiDatetime


class BrandPreferenceListResponse(BaseModel):
    """List of company brand preferences."""

    items: list[BrandPreferenceOut] = Field(default_factory=list)
    total: int


class BrandPreferenceCreateRequest(BaseModel):
    """Request body for POST /api/brand-preferences (human user only)."""

    category_prefix: str = Field(min_length=1, max_length=32)
    category_label: str = Field(min_length=1, max_length=128)
    preferred_brands: list[str] = Field(min_length=1, max_length=20)
    active: bool = True

    @field_validator("category_prefix")
    @classmethod
    def _validate_prefix(cls, value: str) -> str:
        return _clean_non_empty(value, "category_prefix")

    @field_validator("category_label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        return _clean_non_empty(value, "category_label")

    @field_validator("preferred_brands")
    @classmethod
    def _validate_brands(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value:
            b = raw.strip()
            if b and b.lower() not in seen:
                seen.add(b.lower())
                cleaned.append(b)
        if not cleaned:
            raise ValueError("preferred_brands must contain at least one non-empty brand")
        return cleaned


class BrandPreferencePatchRequest(BaseModel):
    """Request body for PATCH /api/brand-preferences/{id} (human user only)."""

    category_prefix: str | None = Field(default=None, min_length=1, max_length=32)
    category_label: str | None = Field(default=None, min_length=1, max_length=128)
    preferred_brands: list[str] | None = Field(default=None, min_length=1, max_length=20)
    active: bool | None = None

    @field_validator("preferred_brands")
    @classmethod
    def _validate_brands_opt(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value:
            b = raw.strip()
            if b and b.lower() not in seen:
                seen.add(b.lower())
                cleaned.append(b)
        if not cleaned:
            raise ValueError("preferred_brands must contain at least one non-empty brand")
        return cleaned


class BrandPreferenceDeleteResponse(BaseModel):
    """Delete acknowledgment for DELETE /api/brand-preferences/{id}."""

    id: str
    deleted: bool = True


# ---------- Batch C: msg=42 Account-Gated A2A Price Negotiation ----------


class NegotiationSettingsOut(BaseModel):
    """Company negotiation policy settings returned by /api/account/negotiation-settings."""

    company_id: str
    allow_price_negotiation: bool = False
    max_auto_discount_pct: float = 8.0
    max_discount_override_pct: float | None = None
    updated_at: HelsinkiDatetime | None = None


class NegotiationSettingsUpdateRequest(BaseModel):
    """Request body for PUT /api/account/negotiation-settings & admin company-negotiations."""

    allow_price_negotiation: bool
    max_discount_override_pct: float | None = Field(default=None, ge=0.0, le=15.0)
    max_auto_discount_pct: float | None = Field(default=None, ge=0.0, le=15.0)


class CompanyNegotiationAdminOut(BaseModel):
    """Per-company negotiation setting item returned by /api/admin/company-negotiations."""

    company_id: str
    company_name: str
    allow_price_negotiation: bool = False
    max_auto_discount_pct: float = 8.0
    max_discount_override_pct: float | None = None
    updated_at: HelsinkiDatetime | None = None


class CompanyNegotiationAdminListResponse(BaseModel):
    """Response body for GET /api/admin/company-negotiations."""

    items: list[CompanyNegotiationAdminOut] = Field(default_factory=list)


NegotiationStatus = Literal["ACCEPTED", "COUNTER_OFFER", "FLOOR_REACHED"]
NegotiationTurnActor = Literal["buyer", "aimo_sales_agent"]
NegotiationTurnAction = Literal[
    "PROPOSE",
    "COMPATIBILITY_CHECK",
    "COUNTER_OFFER",
    "ACCEPT",
    "FLOOR_HOLD",
]


class NegotiationTurnOut(BaseModel):
    """Single structured A2A negotiation round without internal supplier cost/margin data."""

    round: int
    actor: NegotiationTurnActor
    action: NegotiationTurnAction
    proposed_discount_pct: float | None = None
    proposed_total_eur: Money | None = None
    message: str


class QuoteNegotiateRequest(BaseModel):
    """Request body for POST /api/quotes/negotiate."""

    requested_delivery_date: str
    lines: list[QuoteLineInput] = Field(min_length=1, max_length=25)
    diet: str | None = None
    target_discount_pct: float | None = Field(default=None, ge=0.0, le=99.0)
    target_total_eur: float | None = Field(default=None, gt=0)
    justification: str | None = Field(default=None, max_length=2000)
    recipe_id: str | None = Field(default=None, max_length=128)
    target_pax: int | None = Field(default=None, ge=1, le=50000)

    @field_validator("requested_delivery_date")
    @classmethod
    def _validate_delivery_date(cls, value: str) -> str:
        return _parse_delivery_date_str(value)


class QuoteNegotiateResponse(BaseModel):
    """Response body for POST /api/quotes/negotiate."""

    status: NegotiationStatus
    rounds: list[NegotiationTurnOut] = Field(default_factory=list)
    quote: QuoteOut
    list_total_eur: Money
    negotiated_total_eur: Money
    savings_eur: Money
    savings_pct: float
    compatibility_notes: list[str] = Field(default_factory=list)


# ---------- Batch D: Per-User Customer Telegram, QR Pairing & Multi-Branch ----------


class TelegramLinkTokenOut(BaseModel):
    """Response body for POST /api/auth/telegram/link-token."""

    token: str
    bot_username: str
    deep_link_url: str
    tg_app_url: str
    expires_at: HelsinkiDatetime
    qr_svg: str


class TelegramBranchUpdateRequest(BaseModel):
    """Request body for PUT /api/auth/telegram/branch."""

    branch_id: str = Field(min_length=1, max_length=64)


class TelegramLinkClaimRequest(BaseModel):
    """Bridge request body for POST /api/telegram/link-claim."""

    token: str = Field(min_length=1, max_length=128)
    telegram_user_id: str = Field(min_length=1, max_length=64)
    telegram_chat_id: str = Field(min_length=1, max_length=64)
    telegram_username: str | None = Field(default=None, max_length=64)


class TelegramLinkClaimOut(BaseModel):
    """Response body for POST /api/telegram/link-claim."""

    user_id: str
    user_name: str
    user_email: str
    company_id: str
    company_name: str
    branches: list[CompanyBranchOut] = Field(default_factory=list)
    active_branch: CompanyBranchOut | None = None
    needs_branch_selection: bool = False


class TelegramCustomerChatRequest(BaseModel):
    """Bridge request body for POST /api/telegram/customer-chat."""

    telegram_user_id: str = Field(min_length=1, max_length=64)
    telegram_chat_id: str = Field(min_length=1, max_length=64)
    telegram_username: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=4000)


TelegramCustomerChatStatus = Literal["ok", "branch_required"]


class TelegramCustomerChatOut(BaseModel):
    """Response body for POST /api/telegram/customer-chat."""

    status: TelegramCustomerChatStatus = "ok"
    reply_text: str
    conversation_id: str | None = None
    branches: list[CompanyBranchOut] = Field(default_factory=list)
    active_branch: CompanyBranchOut | None = None
    quote: QuoteOut | None = None
    order: OrderOut | None = None
    menu_plan: MenuPlanResponse | None = None
    deals: DealsResponse | None = None
    products: list[ProductOut] = Field(default_factory=list)
    a2a_turns: list[A2ANegotiationTurn] = Field(default_factory=list)
    recipe_sanity_warning: RecipeSanityWarning | None = None
    clarification_options: list[str] = Field(default_factory=list)
    assurance_score: int | None = None
    assurance_threshold: int = 80
    auto_executed: bool = False
    llm_mode: str = "fake"
    fallback_used: bool = False
    processing_warnings: list[str] = Field(default_factory=list)


class TelegramSelectBranchRequest(BaseModel):
    """Bridge request body for POST /api/telegram/select-branch."""

    telegram_user_id: str = Field(min_length=1, max_length=64)
    telegram_chat_id: str = Field(min_length=1, max_length=64)
    branch_id: str = Field(min_length=1, max_length=64)


class TelegramSelectBranchOut(BaseModel):
    """Response body for POST /api/telegram/select-branch."""

    active_branch: CompanyBranchOut
    branches: list[CompanyBranchOut] = Field(default_factory=list)
    auto_reply: TelegramCustomerChatOut | None = None


class TelegramPlaceQuoteRequest(BaseModel):
    """Bridge request body for POST /api/telegram/place-quote."""

    telegram_user_id: str = Field(min_length=1, max_length=64)
    telegram_chat_id: str = Field(min_length=1, max_length=64)
    quote_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=2000)


class TelegramPlaceQuoteOut(BaseModel):
    """Response body for POST /api/telegram/place-quote."""

    order: OrderOut
    active_branch: CompanyBranchOut | None = None
    assurance_score: int | None = None
    assurance_threshold: int = 80
    auto_executed: bool = False


class TelegramUnlinkRequest(BaseModel):
    """Bridge request body for POST /api/telegram/unlink."""

    telegram_user_id: str = Field(min_length=1, max_length=64)
    telegram_chat_id: str = Field(min_length=1, max_length=64)


class TelegramUnlinkOut(BaseModel):
    """Response body for POST /api/telegram/unlink."""

    unlinked: bool = False
    user_id: str | None = None


# ---------- Batch E: Order Assurance Settings & Telegram Cancel Order ----------


class AssistantSettingsOut(BaseModel):
    """Response body for GET/PUT /api/account/assistant-settings."""

    company_id: str
    order_assurance_threshold: int = 80
    min_threshold: int = 50
    max_threshold: int = 100
    updated_at: HelsinkiDatetime | None = None


class AssistantSettingsUpdateRequest(BaseModel):
    """Request body for PUT /api/account/assistant-settings."""

    order_assurance_threshold: int = Field(ge=50, le=100)


class TelegramCancelOrderRequest(BaseModel):
    """Bridge request body for POST /api/telegram/cancel-order."""

    telegram_user_id: str = Field(min_length=1, max_length=64)
    telegram_chat_id: str = Field(min_length=1, max_length=64)
    order_id: str = Field(min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=1000)


class TelegramCancelOrderOut(BaseModel):
    """Response body for POST /api/telegram/cancel-order."""

    order: OrderOut
    active_branch: CompanyBranchOut | None = None


TelegramOrderDecisionKind = Literal["approve", "reject"]


class TelegramDecideOrderRequest(BaseModel):
    """Bridge request body for POST /api/telegram/decide-order (human 1-tap approve/reject)."""

    telegram_user_id: str = Field(min_length=1, max_length=64)
    telegram_chat_id: str = Field(min_length=1, max_length=64)
    order_id: str = Field(min_length=1, max_length=64)
    decision: TelegramOrderDecisionKind
    content_hash: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=1000)
    idempotency_key: str | None = Field(default=None, max_length=128)


class TelegramDecideOrderOut(BaseModel):
    """Response body for POST /api/telegram/decide-order."""

    order: OrderOut
    decision: TelegramOrderDecisionKind
    active_branch: CompanyBranchOut | None = None


class TelegramConnectedSessionOut(BaseModel):
    """Connected private Telegram user session across any tenant company."""

    user_id: str
    user_name: str
    user_email: str
    company_id: str
    company_name: str
    telegram_chat_id: str
    telegram_user_id: str | None = None
    telegram_username: str | None = None
    linked_via_qr: bool = False
    active_branch: CompanyBranchOut | None = None
    branches: list[CompanyBranchOut] = Field(default_factory=list)


class TelegramConnectedSessionsResponse(BaseModel):
    """Response body for GET /api/telegram/connected-sessions."""

    items: list[TelegramConnectedSessionOut] = Field(default_factory=list)


TelegramOrderNotificationEventType = Literal[
    "ORDER_CONFIRMED",
    "ORDER_PENDING_APPROVAL",
    "ORDER_CANCELLED",
    "ORDER_REJECTED",
]


class TelegramOrderNotificationOut(BaseModel):
    """Multi-company order confirmation / approval event for a connected Telegram user."""

    notification_id: str
    event_type: TelegramOrderNotificationEventType
    event_at: HelsinkiDatetime
    telegram_chat_id: str
    telegram_user_id: str | None = None
    user_id: str
    user_name: str
    company_id: str
    company_name: str
    active_branch: CompanyBranchOut | None = None
    origin_channel: str = "web"
    placed_in_same_telegram_chat: bool = False
    can_undo_in_telegram: bool = False
    summary_text: str
    order: OrderOut


class TelegramOrderNotificationsResponse(BaseModel):
    """Response body for GET /api/telegram/order-notifications."""

    items: list[TelegramOrderNotificationOut] = Field(default_factory=list)
    server_time: HelsinkiDatetime


