// Types copied from docs/API_CONTRACT.md v1. Additive (non-contract) types are marked "ADDITIVE".
// Once the backend runs, compare with `src/api/schema.gen.ts` (openapi-typescript) — see PLAN.md M9.

// ---------- Shared objects ----------

export type PrincipalKind = "user" | "agent";

export type Principal = {
  kind: PrincipalKind;
  id: string;
  name: string;
  company_id: string;
  email?: string | null;
  /** agents only: the user who created/authorized the agent */
  delegated_by?: { id: string; name: string } | null;
};

export type Company = { id: string; name: string; business_id?: string | null; city?: string | null };

export type ProductUnit = { unit_id: string; size_in_base_units: number };

export type Product = {
  id: string;
  name: string;
  names: { fi?: string; en?: string; sv?: string };
  category: string;
  category_name?: string | null;
  sales_unit: string;
  base_unit: string;
  units: ProductUnit[];
  vendor: string | null;
  brand: string | null;
  country_of_origin: string | null;
  temperature_condition: number | null;
  /** codes from synkka "allergen". Mocks encode "may contain" as "CODE:MAY_CONTAIN". */
  allergens: string[];
  /** "free from" codes */
  non_allergens: string[];
  labels: string[];
  image_url: string | null;
  co2_kg_per_kg: number | null;
  /** 0..1 */
  delivery_reliability: number | null;
};

export type DeliveryStatus = "ON_TIME" | "AT_RISK" | "LATE" | "NOT_DELIVERABLE" | "UNKNOWN";

export type LineFlag =
  | "CUTOFF_PASSED"
  | "LATE"
  | "PARTIAL"
  | "UNIT_MISMATCH"
  | "SUPPLIER_QTY_UNKNOWN"
  | "LOW_RELIABILITY"
  | "NO_PRICE"
  | "NOT_DELIVERABLE"
  | "SALES_BLOCKED"
  | "CO2_ESTIMATED";

export type LogisticModel = "Stock" | "Terminal";
export type AvailableQtyState = "FINITE" | "UNLIMITED" | "TERMINAL_UNKNOWN" | "ZERO";

export type QuoteLine = {
  product_id: string;
  product_name: string;
  quantity: number;
  sales_unit: string;
  /** excl. VAT per sales unit */
  unit_price_eur: number | null;
  /** excl. VAT */
  line_total_eur: number | null;
  /** percent */
  tax_rate: number | null;
  available_date: string | null;
  closing_time: string | null;
  logistic_model: LogisticModel | null;
  available_qty_state: AvailableQtyState | null;
  /** only if FINITE */
  available_amount: number | null;
  delivery_status: DeliveryStatus;
  /** 0..1 */
  on_time_probability: number | null;
  co2_kg: number | null;
  co2_basis: string | null;
  flags: LineFlag[];
};

export type PolicyOutcome = "AUTO" | "APPROVAL_REQUIRED" | "BLOCKED";

export type GovernanceGateId = "SAFETY_LOCK" | "OPERATIONAL_SANITY" | "AUTONOMY_ENVELOPE";

export type RemediationHintOut = {
  action_type: string;
  label_en?: string | null;
  label_fi?: string | null;
  target_product_ids?: string[];
};

export type PolicyRule = {
  rule_id: string;
  outcome: PolicyOutcome;
  message: string;
  gate?: GovernanceGateId | null;
  overridable_via_approval?: boolean | null;
  remediation_hint?: RemediationHintOut | null;
};

export type PolicyDecision = {
  outcome: PolicyOutcome;
  rules: PolicyRule[];
};

export type QuoteStatus = "OPEN" | "USED" | "EXPIRED";

export type Quote = {
  id: string;
  status: QuoteStatus;
  created_at: string;
  expires_at: string;
  requested_delivery_date: string;
  lines: QuoteLine[];
  total_excl_vat_eur: number;
  total_incl_vat_eur: number;
  total_co2_kg: number | null;
  overall_delivery_status: DeliveryStatus;
  policy_preview: PolicyDecision;
  created_by: Principal;
  /** ADDITIVE: diet requirement the quote was checked against, e.g. "M" or "L,G". Echoed by the backend (M8) and the mocks. */
  diet?: string | null;
  /** ADDITIVE (§5): recipe-aware over-ordering sanity warning attached when ordered qty >> expected qty. */
  recipe_sanity_warning?: RecipeSanityWarning | null;
  /** ADDITIVE (Batch C / msg=42): savings below list price when bound by price negotiation. */
  negotiated_savings_eur?: number | null;
};

export type OrderStatus =
  | "PENDING_APPROVAL"
  | "SUBMITTING"
  | "SUBMISSION_UNKNOWN"
  | "PLACED"
  | "REJECTED"
  | "EXPIRED"
  | "CANCELLED"
  | "FAILED";

export const ORDER_STATUSES: OrderStatus[] = [
  "PENDING_APPROVAL",
  "SUBMITTING",
  "SUBMISSION_UNKNOWN",
  "PLACED",
  "REJECTED",
  "EXPIRED",
  "CANCELLED",
  "FAILED",
];

export type OrderEvent = {
  at: string;
  type: string;
  actor: Principal | null;
  message: string;
  request_id: string;
};

export type ReservationStatus = "HELD_5H" | "HOLD_EXPIRED" | "UNRESERVED_DUE_TO_WARNINGS" | "NONE";

export type Order = {
  id: string;
  status: OrderStatus;
  quote_id: string;
  created_at: string;
  requested_delivery_date: string;
  lines: QuoteLine[];
  total_excl_vat_eur: number;
  total_incl_vat_eur: number;
  total_co2_kg: number | null;
  policy: PolicyDecision;
  created_by: Principal;
  decided_by: Principal | null;
  decided_at: string | null;
  decision_note: string | null;
  approval_expires_at: string | null;
  content_hash: string;
  gateway_reference: string | null;
  /** timeline, oldest first */
  events: OrderEvent[];
  /** ADDITIVE: diet requirement the order was checked against. Echoed by the backend (M8) and the mocks. */
  diet?: string | null;
  /** ADDITIVE (§5): recipe-aware over-ordering sanity warning surfaced on ApprovalCard & detail. */
  recipe_sanity_warning?: RecipeSanityWarning | null;
  /** ADDITIVE (Gate #5): 5h soft availability hold on PENDING_APPROVAL orders + hanging-order warning tracking. */
  reservation_status?: ReservationStatus | null;
  reservation_expires_at?: string | null;
  company_warning_count_7d?: number | null;
  reservation_warning_message?: string | null;
  /** ADDITIVE (Batch C / msg=42): savings below list price when bound by price negotiation. */
  negotiated_savings_eur?: number | null;
  /** ADDITIVE (Batch E / §7): order assurance score & auto-execution metadata. */
  assurance_score?: number | null;
  assurance_threshold?: number | null;
  auto_executed?: boolean;
};

export type AgentStatus = "active" | "disabled" | "quarantined";

export type Agent = {
  id: string;
  name: string;
  description: string | null;
  status: AgentStatus;
  auto_approve_limit_eur: number;
  max_order_value_eur: number;
  daily_spend_limit_eur: number;
  /** empty = all */
  allowed_categories: string[];
  /** null = no limit */
  max_quantity_per_line: number | null;
  can_place_orders: boolean;
  delegated_by: { id: string; name: string };
  expires_at: string | null;
  created_at: string;
  last_used_at: string | null;
  spent_today_eur: number;
};

export type AuditEvent = {
  id: string;
  at: string;
  request_id: string;
  actor: Principal | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  outcome: "ok" | "error";
  details: Record<string, unknown>;
};

export type Page<T> = { items: T[]; total: number };

// ---------- Error envelope ----------

export type ErrorCode =
  | "VALIDATION_ERROR"
  | "UNAUTHENTICATED"
  | "FORBIDDEN"
  | "NOT_FOUND"
  | "CONFLICT"
  | "EMAIL_TAKEN"
  | "AGENT_DISABLED"
  | "POLICY_BLOCKED"
  | "QUOTE_EXPIRED"
  | "QUOTE_STALE"
  | "QUOTE_ALREADY_USED"
  | "APPROVAL_CONTENT_CHANGED"
  | "INVALID_STATE"
  | "UNKNOWN_PRODUCT"
  | "UPSTREAM_UNAVAILABLE"
  | "LLM_UNAVAILABLE"
  | "INTERNAL"
  // Synthetic, frontend-only codes (client.ts) for failures without an envelope:
  | "NETWORK_ERROR"
  | "BAD_RESPONSE";

export type ErrorEnvelope = {
  error: { code: string; message: string; details?: Record<string, unknown> };
  request_id: string;
};

// ---------- Endpoint payloads ----------

export type Health = {
  status: "ok" | string;
  db: string;
  aimo: "ok" | "down";
  aimo_mode: "live" | "fixture";
  server_time: string;
  version: string;
};

export type RegisterRequest = {
  company_name: string;
  business_id?: string;
  user_name: string;
  email: string;
  password: string;
};
export type RegisterResponse = { token: string; user: Principal; company: Company };

export type LoginRequest = { email: string; password: string };
export type LoginResponse = { token: string; user: Principal };

/** `agent` is `null` (not absent) for users on the real backend (schema.check.ts). */
export type MeResponse = { principal: Principal; company: Company; agent?: Agent | null };

export type AgentTokenRequest = { agent_id: string; agent_secret: string };
export type AgentTokenResponse = { token: string; expires_at: string; agent: Agent };

export type AgentCreateRequest = {
  name: string;
  description?: string | null;
  auto_approve_limit_eur: number;
  max_order_value_eur: number;
  daily_spend_limit_eur: number;
  allowed_categories?: string[];
  max_quantity_per_line?: number | null;
  can_place_orders?: boolean;
  expires_at?: string | null;
};
export type AgentUpdateRequest = Partial<AgentCreateRequest> & { status?: "active" | "disabled" };
export type AgentWithSecret = { agent: Agent; agent_secret: string };

/** Finnish menu codes. ADDITIVE: sent as `diet=L,G` on GET /api/products. */
export type DietCode = "L" | "VL" | "M" | "G" | "VEG" | "HEART";

export type ProductSearchParams = {
  q?: string;
  category?: string;
  /** comma list, e.g. "lactose,gluten" */
  allergen_free?: string;
  label?: string;
  /** ADDITIVE: comma list of DietCode, e.g. "L,G" */
  diet?: string;
  limit?: number;
  offset?: number;
};

export type Category = { code: string; name: string; product_count: number };

export type ProductCheckRequest = {
  /** 1..50 */
  product_ids: string[];
  quantities?: Record<string, number>;
  requested_delivery_date: string;
};
export type ProductCheckResponse = { items: QuoteLine[] };

export type QuoteCreateRequest = {
  requested_delivery_date: string;
  /** 1..100 */
  lines: { product_id: string; quantity: number }[];
  /** ADDITIVE (backend): comma list of DietCode the whole order must meet, e.g. "M" → R-HARD-DIET BLOCKED for lines that fail it. */
  diet?: string;
  /** ADDITIVE (§5): optional recipe context for R-RECIPE-SANITY yield verification. */
  recipe_id?: string | null;
  target_pax?: number | null;
};

/** `diet` is ADDITIVE (backend): combined with the quote's diet. */
export type OrderCreateRequest = { quote_id: string; note?: string; diet?: string; recipe_id?: string | null; target_pax?: number | null };

export type OrderListParams = {
  status?: OrderStatus;
  /** "true" = only agent-created, "false" = only human-created */
  created_by_agent?: boolean;
  /** ADDITIVE (backend M8): only orders created by this agent (users only). */
  agent_id?: string;
  limit?: number;
  offset?: number;
};

export type ApproveRequest = { content_hash: string; note?: string };
export type NoteRequest = { note?: string };

export type Dashboard = {
  pending_approvals: number;
  orders_today: number;
  spend_today_eur: number;
  spend_7d_eur: number;
  co2_7d_kg: number;
  active_agents: number;
  recent_orders: Order[];
};

export type AuditParams = {
  entity_type?: string;
  entity_id?: string;
  actor_id?: string;
  request_id?: string;
  action?: string;
  outcome?: string;
  limit?: number;
  offset?: number;
};

export type AssistantAction =
  | { type: "search_results"; products: Product[] }
  | { type: "quote"; quote: Quote }
  | { type: "order"; order: Order }
  | { type: "clarification"; question: string; options?: string[] | null }
  | { type: "deals"; deals: DealsResponse }
  | { type: "menu_plan"; menu_plan: MenuPlanResponse }
  | { type: "recipe_sanity_warning"; warning: RecipeSanityWarning }
  | { type: "a2a_negotiation"; summary?: string | null; target_pax?: number | null; turns: A2ANegotiationTurn[]; order?: Order | null };

export type AssistantChatRequest = { conversation_id?: string; message: string };
export type AssistantChatResponse = {
  conversation_id: string;
  reply: string;
  actions: AssistantAction[];
  /** ADDITIVE (Batch E / §7): Order Assurance Score (50..100 threshold), auto-execution, LLM & warning telemetry. */
  assurance_score?: number | null;
  assurance_threshold?: number;
  auto_executed?: boolean;
  llm_mode?: string;
  fallback_used?: boolean;
  processing_warnings?: string[];
};

// ---------- ADDITIVE: substitutes ----------
// POST /api/products/substitutes (not in API_CONTRACT v1; proposed in docs/CONTRACT_CHANGES.md)

export type SubstitutesRequest = {
  product_id: string;
  quantity: number;
  requested_delivery_date: string;
  /** comma list of DietCode the substitute must keep, e.g. "L" */
  diet?: string;
};

export type SubstituteItem = {
  product: Product;
  /** live check of the substitute at the requested quantity/date (server computed) */
  line: QuoteLine;
  /** plain-words trade-offs, e.g. "UHT ambient instead of chilled", "+€0.64 / ST vs original" */
  trade_offs: string[];
  /** why it was suggested, e.g. "Lactose-free (L), full quantity on time" */
  reason: string;
  /** ADDITIVE: Good-Better-Best tier within Valio Aimo assortment */
  tier?: "BEST" | "BETTER" | "GOOD" | null;
};

export type SubstitutesResponse = { items: SubstituteItem[] };

// ---------- ADDITIVE: A2A protocol trace ----------
// GET /api/a2a/tasks?limit&offset (not in API_CONTRACT v1; proposed in docs/CONTRACT_CHANGES.md)

export type A2ATaskState = "working" | "input-required" | "completed" | "failed" | "canceled";

export type A2AHistoryEntry = {
  at: string;
  state: A2ATaskState;
  role: "agent" | "gateway";
  message: string;
  data?: Record<string, unknown> | null;
};

export type A2ATask = {
  id: string;
  state: A2ATaskState;
  principal: Principal;
  created_at: string;
  updated_at: string;
  linked_order_id: string | null;
  history: A2AHistoryEntry[];
};

export type A2ATaskParams = { limit?: number; offset?: number };

// ---------- M9: Order trace, audit verify, governance, promotions, deals, menu planner ----------

export type ReasoningStepOut = {
  step: number;
  stage: string;
  summary: string;
  actor_kind: string;
  at: string;
  details?: Record<string, unknown>;
};

export type OrderTraceStepSource = "assistant" | "a2a" | "audit" | "order_event" | "reasoning";

export type OrderTraceStepOut = {
  step: number;
  source: OrderTraceStepSource;
  stage: string;
  summary: string;
  actor_kind?: string | null;
  actor_id?: string | null;
  actor_name?: string | null;
  at: string;
  request_id?: string | null;
  details?: Record<string, unknown>;
};

export type OrderTraceResponse = {
  order_id: string;
  quote_id: string;
  status: OrderStatus;
  policy_decision: PolicyDecision;
  fired_rules?: PolicyRule[];
  steps?: OrderTraceStepOut[];
  chain_verified: boolean;
  source_conversation_id?: string | null;
  source_a2a_task_id?: string | null;
  /** ADDITIVE (Batch E / §7): embedded assistant chat turn metadata captured from quote/order creation. */
  chat_trace?: Record<string, unknown> | null;
};

export type AuditVerifyResponse = {
  valid: boolean;
  checked_events: number;
  broken_event_id?: string | null;
};

export type GovernanceInvariantOut = {
  rule_id: string;
  outcome: string;
  description: string;
  gate?: GovernanceGateId | null;
  overridable_via_approval?: boolean | null;
};

export type GovernanceToolInfoOut = {
  name: string;
  description: string;
};

export type GovernanceSummaryResponse = {
  company_id: string;
  company_name: string;
  policies?: Record<string, unknown>;
  agent_roles?: Agent[];
  layer1_invariants?: GovernanceInvariantOut[];
  assistant_tools?: GovernanceToolInfoOut[];
  mcp_tools?: GovernanceToolInfoOut[];
  active_promotions_count: number;
  audit_chain: AuditVerifyResponse;
};

export type PromotionOut = {
  id: string;
  company_id: string;
  title: string;
  description?: string | null;
  brand_filter?: string | null;
  category_prefix?: string | null;
  product_ids?: string[];
  boost_weight: number;
  prefer_on_sale: boolean;
  active: boolean;
  created_by_user_id: string;
  created_at: string;
};

export type PromotionCreateRequest = {
  title: string;
  description?: string | null;
  brand_filter?: string | null;
  category_prefix?: string | null;
  product_ids?: string[];
  boost_weight: number;
  prefer_on_sale: boolean;
  active: boolean;
};

export type PromotionPatchRequest = {
  title?: string | null;
  description?: string | null;
  brand_filter?: string | null;
  category_prefix?: string | null;
  product_ids?: string[] | null;
  boost_weight?: number | null;
  prefer_on_sale?: boolean | null;
  active?: boolean | null;
};

export type PromotionDeleteResponse = {
  deleted: boolean;
  id: string;
};

export type ProductDealsParams = {
  q?: string | null;
  category?: string | null;
  diet?: string | null;
  valio_only?: boolean;
  on_sale_only?: boolean;
  requested_delivery_date?: string | null;
  limit?: number;
};

export type DealItemOut = {
  product: Product;
  quote_line: QuoteLine;
  list_price_ex_vat?: number | null;
  discount_pct: number;
  savings_per_unit: number;
  on_sale: boolean;
  promoted: boolean;
  promotion_reasons?: string[];
};

export type DealsResponse = {
  items: DealItemOut[];
  total: number;
  requested_delivery_date: string;
  promotions_applied?: string[];
};

export type MealType = "breakfast" | "lunch" | "dinner";

export type MenuRecipeSummaryOut = {
  id: string;
  name: string;
  meal_type: MealType;
  diet_tags?: string[];
  chef_note: string;
};

export type MenuIngredientSelectionOut = {
  recipe_id: string;
  recipe_name: string;
  ingredient_label: string;
  product: Product;
  quote_line: QuoteLine;
  list_price_ex_vat?: number | null;
  discount_pct: number;
  savings_eur: number;
  promoted: boolean;
  promotion_reasons?: string[];
  selection_reason: string;
};

export type MenuPlanRequest = {
  pax: number;
  meal_type?: MealType | null;
  requested_delivery_date?: string | null;
  diet?: string | null;
  prefer_on_sale: boolean;
  include_valio_recipes: boolean;
  max_budget_eur?: number | null;
};

export type MenuPlanResponse = {
  plan_id: string;
  pax: number;
  meal_type?: MealType | null;
  requested_delivery_date: string;
  diet?: string | null;
  recipes?: MenuRecipeSummaryOut[];
  items?: MenuIngredientSelectionOut[];
  quote_draft_lines?: { product_id: string; quantity: number }[];
  estimated_total_excl_vat_eur: number;
  estimated_total_incl_vat_eur: number;
  estimated_list_total_excl_vat_eur: number;
  estimated_savings_eur: number;
  estimated_co2_kg?: number | null;
  budget_exceeded: boolean;
  policy_preview: PolicyDecision;
  promotions_applied?: string[];
  warnings?: string[];
  reasoning_steps?: ReasoningStepOut[];
};

export type SimulatorScenarioId =
  | "unit_trap"
  | "diet_breach"
  | "gateway_timeout_recovery"
  | "partial_and_cutoff"
  | "tampered_approval_hash";

export type SimulatorScenario = {
  id: SimulatorScenarioId;
  title: string;
  summary: string;
  expected_rules: string[];
  target_product_ids: string[];
  expected_outcome: string;
};

export type SimulatorScenariosResponse = {
  scenarios: SimulatorScenario[];
};

export type SimulatorRunRequest = {
  scenario_id: SimulatorScenarioId;
};

export type SimulatorRunResponse = {
  scenario_id: SimulatorScenarioId;
  title: string;
  passed: boolean;
  quote_id?: string | null;
  order_id?: string | null;
  http_status: number;
  error_code?: string | null;
  policy?: PolicyDecision | null;
  order_status_before_reconcile?: string | null;
  order_status_after?: string | null;
  gateway_reference?: string | null;
  substitutes_preview?: SubstituteItem[];
  audit_chain: AuditVerifyResponse;
  request_id: string;
};

export type OptimizeObjective = "BALANCED" | "SAVE_MONEY" | "MAX_RELIABILITY" | "LOW_CARBON";

export type QuoteOptimizeRequest = {
  requested_delivery_date: string;
  lines: { product_id: string; quantity: number }[];
  diet?: string | null;
  objective: OptimizeObjective;
  allow_plant_based_swaps: boolean;
};

export type OptimizeSummary = {
  total_excl_vat_eur: number;
  min_on_time_probability?: number | null;
  estimated_co2_kg?: number | null;
  co2_covered_lines: number;
  total_lines: number;
  overall_delivery_status: DeliveryStatus;
  policy_preview: PolicyDecision;
};

export type OptimizeDeltas = {
  total_excl_vat_delta_eur: number;
  min_reliability_delta?: number | null;
  comparable_co2_delta_kg?: number | null;
  comparable_co2_lines: number;
};

export type LineOptimizationSwap = {
  line_index: number;
  original_line: QuoteLine;
  recommended_product: Product;
  recommended_line: QuoteLine;
  suggested_quantity: number;
  line_total_delta_eur: number;
  reliability_delta?: number | null;
  co2_delta_kg?: number | null;
  co2_basis_note?: string | null;
  tradeoffs: string[];
  reason: string;
};

export type QuoteOptimizeResponse = {
  objective: OptimizeObjective;
  requested_delivery_date: string;
  diet?: string | null;
  before_summary: OptimizeSummary;
  after_summary: OptimizeSummary;
  deltas: OptimizeDeltas;
  swaps: LineOptimizationSwap[];
  unchanged_lines: number[];
};

// ---------- Priority #0: Explicit Website Clone Telegram Opt-In + Request-Field Router ----------

export type CompanyBranchOut = {
  id: string;
  name: string;
  short_name: string;
  address?: string | null;
};

export type TelegramOptInOut = {
  user_id: string;
  company_id: string;
  telegram_username: string | null;
  telegram_chat_id: string | null;
  enabled: boolean;
  bot_username: string;
  consented_at: string | null;
  updated_at: string | null;
  linked_via_qr?: boolean;
  branches?: CompanyBranchOut[];
  active_branch_id?: string | null;
  active_branch_name?: string | null;
};

export type TelegramOptInUpdateRequest = {
  telegram_id: string;
  enabled?: boolean;
  consent_confirmed: boolean;
};

export type TelegramLinkTokenOut = {
  token: string;
  bot_username: string;
  deep_link_url: string;
  tg_app_url: string;
  expires_at: string;
  qr_svg: string;
};

export type TelegramBranchUpdateRequest = {
  branch_id: string;
};

export type RoutingTargetService = "core-api" | "agentic-service";

export type RoutingInspectRequest = {
  method?: string;
  path: string;
  payload: Record<string, unknown>;
  headers?: Record<string, string>;
};

export type RoutingInspectResponse = {
  target_service: RoutingTargetService;
  matched_fields: string[];
  structured_fields: string[];
  reason: string;
  downstream_flow: string[];
};

// ---------- Priority #0 Batch 2 (§3, §5, §6, §7) ----------

export type DemoPersonaGroup = "judge" | "founder" | "demo";

export type DemoPersona = {
  email: string;
  name: string;
  company_name: string;
  /** ADDITIVE (Batch J / §13): persona grouping on /login ("judge" | "founder" | "demo"). */
  group?: DemoPersonaGroup;
  /** ADDITIVE (Batch J / §13): role badge label shown on /login. */
  role_label?: string | null;
};

export type DemoPersonasResponse = {
  items: DemoPersona[];
};

export type RecipeSanityOffendingLine = {
  product_id: string;
  product_name: string;
  ordered_qty: number;
  unit: string;
  expected_qty: number;
  excess_ratio: number;
  excess_cost_eur: number;
};

export type RecipeSanityWarning = {
  triggered: boolean;
  rule_id: string;
  recipe_id?: string | null;
  recipe_name: string;
  target_pax: number;
  message: string;
  offending_lines: RecipeSanityOffendingLine[];
};

export type A2ANegotiationTurn = {
  speaker: string;
  message: string;
  status?: string | null;
};

export type QuarantinedAgentAlertOut = {
  agent_id: string;
  agent_name: string;
  status: string;
  trigger_reason: string;
  rule_code?: string | null;
  quarantined_at: string;
  purchasing_characteristics: Record<string, unknown>;
  recent_request_ids?: string[];
};

export type AgentAnomaliesResponse = {
  items: QuarantinedAgentAlertOut[];
};

export type AgentQuarantineReviewRequest = {
  action: "restore" | "revoke";
  note?: string;
};

export type AgentQuarantineReviewResponse = {
  agent: Agent;
  action: "restore" | "revoke";
  reviewed_at: string;
};

// ---------- Batch C (msg=40 Admin Invisible Aimo Campaigns & Brand Preferences + msg=42 Price Negotiation) ----------

export type AimoCampaignOut = {
  id: string;
  title: string;
  brand_filter: string | null;
  category_prefix: string | null;
  product_ids: string[];
  boost_weight: number;
  active: boolean;
  created_at: string;
};

export type AimoCampaignCreateRequest = {
  title: string;
  brand_filter?: string | null;
  category_prefix?: string | null;
  product_ids?: string[];
  boost_weight: number;
  active: boolean;
};

export type AimoCampaignPatchRequest = {
  title?: string | null;
  brand_filter?: string | null;
  category_prefix?: string | null;
  product_ids?: string[] | null;
  boost_weight?: number | null;
  active?: boolean | null;
};

export type AimoCampaignDeleteResponse = {
  id: string;
  deleted: boolean;
};

export type BrandPreferenceOut = {
  id: string;
  company_id: string;
  category_prefix: string;
  category_label: string;
  preferred_brands: string[];
  active: boolean;
  updated_at: string;
};

export type BrandPreferenceCreateRequest = {
  category_prefix: string;
  category_label: string;
  preferred_brands: string[];
  active: boolean;
};

export type BrandPreferencePatchRequest = {
  category_prefix?: string | null;
  category_label?: string | null;
  preferred_brands?: string[] | null;
  active?: boolean | null;
};

export type BrandPreferenceDeleteResponse = {
  id: string;
  deleted: boolean;
};

export type NegotiationSettingsOut = {
  company_id: string;
  allow_price_negotiation: boolean;
  max_discount_override_pct: number | null;
  updated_at: string | null;
};

export type NegotiationSettingsUpdateRequest = {
  allow_price_negotiation: boolean;
  max_discount_override_pct?: number | null;
};

export type CompanyNegotiationAdminOut = {
  company_id: string;
  company_name: string;
  allow_price_negotiation: boolean;
  max_auto_discount_pct: number;
  max_discount_override_pct?: number | null;
  updated_at: string | null;
};

export type CompanyNegotiationAdminListResponse = {
  items: CompanyNegotiationAdminOut[];
};

export type NegotiationTurnActor = "buyer" | "aimo_sales_agent";

export type NegotiationTurnAction = "PROPOSE" | "COMPATIBILITY_CHECK" | "COUNTER_OFFER" | "ACCEPT" | "FLOOR_HOLD";

export type NegotiationTurnOut = {
  round: number;
  actor: NegotiationTurnActor;
  action: NegotiationTurnAction;
  proposed_discount_pct: number | null;
  proposed_total_eur: number | null;
  message: string;
};

export type NegotiationOutcomeStatus = "ACCEPTED" | "COUNTER_OFFER" | "FLOOR_REACHED";

export type QuoteNegotiateRequest = {
  requested_delivery_date: string;
  lines: { product_id: string; quantity: number }[];
  diet?: string | null;
  target_discount_pct?: number | null;
  target_total_eur?: number | null;
  justification?: string | null;
  recipe_id?: string | null;
  target_pax?: number | null;
};

export type QuoteNegotiateResponse = {
  status: NegotiationOutcomeStatus;
  rounds: NegotiationTurnOut[];
  quote: Quote;
  list_total_eur: number;
  negotiated_total_eur: number;
  savings_eur: number;
  savings_pct: number;
  compatibility_notes: string[];
};

export type AssistantSettingsOut = {
  company_id: string;
  order_assurance_threshold: number;
  min_threshold?: number;
  max_threshold?: number;
  updated_at: string | null;
};

export type AssistantSettingsUpdateRequest = {
  order_assurance_threshold: number;
};

export type A2ASupportedMatter = {
  id: "menu_planning" | "order_execution" | "preliminary_tendering" | "exception_handling" | string;
  title: string;
  summary: string;
  enabled: boolean;
  tools_or_skills: string[];
};

export type A2AAgentCard = {
  name: string;
  description: string;
  url: string;
  version: string;
  protocolVersion?: string;
  skills: { id: string; name: string; description: string; tags?: string[]; examples?: string[] }[];
  supported_matters?: A2ASupportedMatter[];
};

export type A2ASessionCapabilitiesResult = {
  session_greeting: string;
  company_name?: string;
  protocol: string;
  supported_matters: A2ASupportedMatter[];
};

export type A2ASessionConnectResponse = {
  jsonrpc: string;
  id: string | number | null;
  result?: A2ASessionCapabilitiesResult;
};

