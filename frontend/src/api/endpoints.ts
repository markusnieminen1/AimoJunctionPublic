// One typed function per endpoint (contract + additive). Hooks in hooks.ts wrap these.
import { api, type RequestOptions } from "./client";
import type {
  A2AAgentCard,
  A2ASessionConnectResponse,
  A2ATask,
  A2ATaskParams,
  Agent,
  AgentAnomaliesResponse,
  AgentCreateRequest,
  AgentQuarantineReviewRequest,
  AgentQuarantineReviewResponse,
  AgentTokenRequest,
  AgentTokenResponse,
  AgentUpdateRequest,
  AgentWithSecret,
  AimoCampaignCreateRequest,
  AimoCampaignDeleteResponse,
  AimoCampaignOut,
  AimoCampaignPatchRequest,
  ApproveRequest,
  AssistantChatRequest,
  AssistantChatResponse,
  AssistantSettingsOut,
  AssistantSettingsUpdateRequest,
  AuditEvent,
  AuditParams,
  AuditVerifyResponse,
  BrandPreferenceCreateRequest,
  BrandPreferenceDeleteResponse,
  BrandPreferenceOut,
  BrandPreferencePatchRequest,
  Category,
  CompanyNegotiationAdminListResponse,
  CompanyNegotiationAdminOut,
  Dashboard,
  DealsResponse,
  DemoPersonasResponse,
  GovernanceSummaryResponse,
  Health,
  LoginRequest,
  LoginResponse,
  MenuPlanRequest,
  MenuPlanResponse,
  MeResponse,
  NegotiationSettingsOut,
  NegotiationSettingsUpdateRequest,
  NoteRequest,
  Order,
  OrderCreateRequest,
  OrderListParams,
  OrderTraceResponse,
  Page,
  Product,
  ProductCheckRequest,
  ProductCheckResponse,
  ProductDealsParams,
  ProductSearchParams,
  PromotionCreateRequest,
  PromotionDeleteResponse,
  PromotionOut,
  PromotionPatchRequest,
  Quote,
  QuoteCreateRequest,
  QuoteNegotiateRequest,
  QuoteNegotiateResponse,
  QuoteOptimizeRequest,
  QuoteOptimizeResponse,
  RegisterRequest,
  RegisterResponse,
  SimulatorRunRequest,
  SimulatorRunResponse,
  SimulatorScenariosResponse,
  SubstitutesRequest,
  SubstitutesResponse,
  SubstituteItem,
  TelegramBranchUpdateRequest,
  TelegramLinkTokenOut,
  TelegramOptInOut,
  TelegramOptInUpdateRequest,
  RoutingInspectRequest,
  RoutingInspectResponse,
} from "./types";

/**
 * M9 backend adaptation: `POST /api/products/substitutes` returns
 * `{ requested_product_id, diet, items: { product, quote_line, tradeoffs, reason, price_delta_eur }[] }`,
 * while the frontend (and the CONTRACT_CHANGES proposal / MSW mocks) use `{ items: { product, line, trade_offs, reason }[] }`.
 * Accept both spellings; drop items without a product or line instead of crashing.
 */
export function normalizeSubstitutes(raw: unknown): SubstitutesResponse {
  const items = raw && typeof raw === "object" && Array.isArray((raw as { items?: unknown }).items) ? (raw as { items: unknown[] }).items : [];
  const out: SubstituteItem[] = [];
  for (const it of items) {
    if (!it || typeof it !== "object") continue;
    const r = it as Record<string, unknown>;
    const line = (r.line ?? r.quote_line) as SubstituteItem["line"] | undefined;
    const product = r.product as SubstituteItem["product"] | undefined;
    if (!line || !product) continue;
    const trade = r.trade_offs ?? r.tradeoffs;
    const tier = r.tier === "BEST" || r.tier === "BETTER" || r.tier === "GOOD" ? r.tier : undefined;
    out.push({
      product,
      line,
      trade_offs: Array.isArray(trade) ? trade.filter((x): x is string => typeof x === "string") : [],
      reason: typeof r.reason === "string" ? r.reason : "",
      ...(tier ? { tier } : {}),
    });
  }
  return { items: out };
}

type Opts = Omit<RequestOptions, "body" | "query">;
const enc = encodeURIComponent;

export const endpoints = {
  health: (o?: Opts) => api.get<Health>("/api/health", { ...o, anonymous: true }),

  // Auth
  register: (b: RegisterRequest) => api.post<RegisterResponse>("/api/auth/register", b, { anonymous: true }),
  login: (b: LoginRequest) => api.post<LoginResponse>("/api/auth/login", b, { anonymous: true }),
  me: (o?: Opts) => api.get<MeResponse>("/api/me", o),
  agentToken: (b: AgentTokenRequest) => api.post<AgentTokenResponse>("/api/agent-auth/token", b, { anonymous: true }),
  getTelegramOptIn: (o?: Opts) => api.get<TelegramOptInOut>("/api/auth/telegram", o),
  updateTelegramOptIn: (b: TelegramOptInUpdateRequest, o?: Opts) => api.put<TelegramOptInOut>("/api/auth/telegram", b, o),
  deleteTelegramOptIn: (o?: Opts) => api.delete<TelegramOptInOut>("/api/auth/telegram", o),
  createTelegramLinkToken: (o?: Opts) => api.post<TelegramLinkTokenOut>("/api/auth/telegram/link-token", {}, o),
  updateTelegramBranch: (b: TelegramBranchUpdateRequest, o?: Opts) => api.put<TelegramOptInOut>("/api/auth/telegram/branch", b, o),
  demoPersonas: (o?: Opts) => api.get<DemoPersonasResponse>("/api/auth/demo-personas", { ...o, anonymous: true }),

  // Agents
  listAgents: (o?: Opts) => api.get<Page<Agent>>("/api/agents", o),
  agentAnomalies: (o?: Opts) => api.get<AgentAnomaliesResponse>("/api/agents/anomalies", o),
  reviewAgentAnomaly: (id: string, b: AgentQuarantineReviewRequest, o?: Opts) =>
    api.post<AgentQuarantineReviewResponse>(`/api/agents/${enc(id)}/review`, b, o),
  createAgent: (b: AgentCreateRequest, o?: Opts) => api.post<AgentWithSecret>("/api/agents", b, o),
  getAgent: (id: string, o?: Opts) => api.get<Agent>(`/api/agents/${enc(id)}`, o),
  updateAgent: (id: string, b: AgentUpdateRequest, o?: Opts) => api.patch<Agent>(`/api/agents/${enc(id)}`, b, o),
  rotateAgentSecret: (id: string, o?: Opts) => api.post<AgentWithSecret>(`/api/agents/${enc(id)}/rotate-secret`, {}, o),
  agentActivity: (id: string, o?: Opts) => api.get<Page<AuditEvent>>(`/api/agents/${enc(id)}/activity`, o),

  // Products
  searchProducts: (p: ProductSearchParams = {}, o?: Opts) => api.get<Page<Product>>("/api/products", { ...o, query: p }),
  productDeals: (p: ProductDealsParams = {}, o?: Opts) => api.get<DealsResponse>("/api/products/deals", { ...o, query: p }),
  getProduct: (id: string, o?: Opts) => api.get<Product>(`/api/products/${enc(id)}`, o),
  categories: (o?: Opts) => api.get<{ items: Category[] }>("/api/categories", o),
  checkProducts: (b: ProductCheckRequest, o?: Opts) => api.post<ProductCheckResponse>("/api/products/check", b, o),
  /** ADDITIVE endpoint (response normalized: the real backend names the fields `quote_line` / `tradeoffs`). */
  substitutes: (b: SubstitutesRequest, o?: Opts) =>
    api.post<unknown>("/api/products/substitutes", b, o).then(normalizeSubstitutes),

  // Promotions & Menu Planner (M9)
  listPromotions: (o?: Opts) => api.get<Page<PromotionOut>>("/api/promotions", o),
  createPromotion: (b: PromotionCreateRequest, o?: Opts) => api.post<PromotionOut>("/api/promotions", b, o),
  updatePromotion: (id: string, b: PromotionPatchRequest, o?: Opts) => api.patch<PromotionOut>(`/api/promotions/${enc(id)}`, b, o),
  deletePromotion: (id: string, o?: Opts) => api.delete<PromotionDeleteResponse>(`/api/promotions/${enc(id)}`, o),
  planMenu: (b: MenuPlanRequest, o?: Opts) => api.post<MenuPlanResponse>("/api/menu/plan", b, o),

  // Quotes
  createQuote: (b: QuoteCreateRequest, o?: Opts) => api.post<Quote>("/api/quotes", b, o),
  getQuote: (id: string, o?: Opts) => api.get<Quote>(`/api/quotes/${enc(id)}`, o),
  /** ADDITIVE P2 Pre-Flight Cart Optimizer */
  optimizeQuote: (b: QuoteOptimizeRequest, o?: Opts) => api.post<QuoteOptimizeResponse>("/api/quotes/optimize", b, o),
  /** ADDITIVE Batch C (msg=42) Account-Gated Preliminary Quote Price Negotiation */
  negotiateQuote: (b: QuoteNegotiateRequest, o?: Opts) => api.post<QuoteNegotiateResponse>("/api/quotes/negotiate", b, o),

  // Batch C Admin & Brand Preferences + Negotiation Settings (msg=40, msg=42)
  listAimoCampaigns: (o?: Opts) => api.get<Page<AimoCampaignOut>>("/api/admin/aimo-campaigns", o),
  createAimoCampaign: (b: AimoCampaignCreateRequest, o?: Opts) => api.post<AimoCampaignOut>("/api/admin/aimo-campaigns", b, o),
  updateAimoCampaign: (id: string, b: AimoCampaignPatchRequest, o?: Opts) => api.patch<AimoCampaignOut>(`/api/admin/aimo-campaigns/${enc(id)}`, b, o),
  deleteAimoCampaign: (id: string, o?: Opts) => api.delete<AimoCampaignDeleteResponse>(`/api/admin/aimo-campaigns/${enc(id)}`, o),

  listBrandPreferences: (o?: Opts) => api.get<Page<BrandPreferenceOut>>("/api/brand-preferences", o),
  createBrandPreference: (b: BrandPreferenceCreateRequest, o?: Opts) => api.post<BrandPreferenceOut>("/api/brand-preferences", b, o),
  updateBrandPreference: (id: string, b: BrandPreferencePatchRequest, o?: Opts) =>
    api.patch<BrandPreferenceOut>(`/api/brand-preferences/${enc(id)}`, b, o),
  deleteBrandPreference: (id: string, o?: Opts) => api.delete<BrandPreferenceDeleteResponse>(`/api/brand-preferences/${enc(id)}`, o),

  getNegotiationSettings: (o?: Opts) => api.get<NegotiationSettingsOut>("/api/account/negotiation-settings", o),
  updateNegotiationSettings: (b: NegotiationSettingsUpdateRequest, o?: Opts) =>
    api.put<NegotiationSettingsOut>("/api/account/negotiation-settings", b, o),

  listCompanyNegotiations: (o?: Opts) =>
    api.get<CompanyNegotiationAdminListResponse>("/api/admin/company-negotiations", o),
  updateCompanyNegotiation: (companyId: string, b: NegotiationSettingsUpdateRequest, o?: Opts) =>
    api.put<CompanyNegotiationAdminOut>(`/api/admin/company-negotiations/${enc(companyId)}`, b, o),

  getAssistantSettings: (o?: Opts) => api.get<AssistantSettingsOut>("/api/account/assistant-settings", o),
  updateAssistantSettings: (b: AssistantSettingsUpdateRequest, o?: Opts) =>
    api.put<AssistantSettingsOut>("/api/account/assistant-settings", b, o),

  // Orders (idempotencyKey required for createOrder; recommended for approve)
  createOrder: (b: OrderCreateRequest, idempotencyKey: string, o?: Opts) =>
    api.post<Order>("/api/orders", b, { ...o, idempotencyKey }),
  listOrders: (p: OrderListParams = {}, o?: Opts) => api.get<Page<Order>>("/api/orders", { ...o, query: p }),
  getOrder: (id: string, o?: Opts) => api.get<Order>(`/api/orders/${enc(id)}`, o),
  orderTrace: (id: string, o?: Opts) => api.get<OrderTraceResponse>(`/api/orders/${enc(id)}/trace`, o),
  approveOrder: (id: string, b: ApproveRequest, idempotencyKey: string, o?: Opts) =>
    api.post<Order>(`/api/orders/${enc(id)}/approve`, b, { ...o, idempotencyKey }),
  rejectOrder: (id: string, b: NoteRequest = {}, o?: Opts) => api.post<Order>(`/api/orders/${enc(id)}/reject`, b, o),
  cancelOrder: (id: string, b: NoteRequest = {}, o?: Opts) => api.post<Order>(`/api/orders/${enc(id)}/cancel`, b, o),

  // Dashboard, audit, governance, A2A
  dashboard: (o?: Opts) => api.get<Dashboard>("/api/dashboard", o),
  audit: (p: AuditParams = {}, o?: Opts) => api.get<Page<AuditEvent>>("/api/audit", { ...o, query: p }),
  auditVerify: (o?: Opts) => api.get<AuditVerifyResponse>("/api/audit/verify", o),
  governanceSummary: (o?: Opts) => api.get<GovernanceSummaryResponse>("/api/governance/summary", o),
  /** ADDITIVE endpoint */
  a2aTasks: (p: A2ATaskParams = {}, o?: Opts) => api.get<Page<A2ATask>>("/api/a2a/tasks", { ...o, query: p }),
  a2aAgentCard: (o?: Opts) => api.get<A2AAgentCard>("/api/.well-known/agent-card.json", { ...o, anonymous: true }),
  a2aSessionConnect: (o?: Opts) =>
    api.post<A2ASessionConnectResponse>(
      "/api/a2a",
      { jsonrpc: "2.0", id: "web-ui-handshake", method: "session/connect", params: {} },
      o,
    ),

  /** ADDITIVE dev-mode Flight Simulator endpoints (404 when APP_ENV != "dev") */
  simulatorScenarios: (o?: Opts) => api.get<SimulatorScenariosResponse>("/api/dev/simulator/scenarios", o),
  runSimulatorScenario: (b: SimulatorRunRequest, o?: Opts) =>
    api.post<SimulatorRunResponse>("/api/dev/simulator/run", b, o).then((res) => ({
      ...res,
      substitutes_preview: Array.isArray(res.substitutes_preview)
        ? normalizeSubstitutes({ items: res.substitutes_preview }).items
        : [],
    })),

  /** Request-Field Router inspector (Core API vs. Agentic Service) */
  inspectRouting: (b: RoutingInspectRequest, o?: Opts) => api.post<RoutingInspectResponse>("/api/routing/inspect", b, o),

  // Assistant
  assistantChat: (b: AssistantChatRequest, o?: Opts) => api.post<AssistantChatResponse>("/api/assistant/chat", b, o),
};
