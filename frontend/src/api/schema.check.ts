// Compile-time contract drift check: the hand-written types in `types.ts` (what the UI reads and sends) against
// `schema.gen.ts` (generated from the backend's OpenAPI spec: `npm run gen:schema`). Type-only, never bundled.
// If the backend renames a field, narrows/widens an enum, makes a response field nullable, adds a required request field
// or drops a route, `npm run typecheck` fails on the matching line below.
//
// Directions:
// - responses: backend → UI. What the server sends must be assignable to what the UI reads.
// - requests:  UI → backend. What the UI sends must be assignable to what the server accepts.
// - enums: equal both ways (a new status must reach the badge maps, a removed one is dead UI).
// Legitimate differences (PROGRESS.md "Contract mismatches") are asserted on the adapted shape, with a comment.
import type { components, paths } from "./schema.gen";
import type * as T from "./types";

type S = components["schemas"];

type Assignable<A, B> = [A] extends [B] ? true : false;
type Equal<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
type Expect<X extends true> = X;

/**
 * Pydantic response models declare fields with defaults as optional (`x?: string | null`), but FastAPI always
 * serializes them (as `null` / `[]`), see contract mismatch 6. Model that: every response field is present.
 */
type Serialized<X> = X extends (infer U)[]
  ? Serialized<U>[]
  : X extends object
    ? { [K in keyof X]-?: Serialized<X[K]> }
    : X;

type Op<P extends keyof paths, M extends "get" | "post" | "put" | "patch" | "delete"> = NonNullable<paths[P][M]>;
type Json<P extends keyof paths, M extends "get" | "post" | "put" | "patch" | "delete"> =
  Op<P, M> extends { responses: infer R }
    ? R extends { 200: { content: { "application/json": infer B } } }
      ? B
      : R extends { 201: { content: { "application/json": infer B } } }
        ? B
        : never
    : never;
// `requestBody?` = the body itself is optional (reject / cancel take `{ note? }` or nothing).
type Body<P extends keyof paths, M extends "post" | "put" | "patch"> =
  Op<P, M> extends { requestBody?: infer RB } ? (NonNullable<RB> extends { content: { "application/json": infer B } } ? B : never) : never;
type Query<P extends keyof paths> = Op<P, "get"> extends { parameters: { query?: infer Q } } ? NonNullable<Q> : never;
type Res<P extends keyof paths, M extends "get" | "post" | "put" | "patch" | "delete"> = Serialized<Json<P, M>>;

// ---------- Routes the UI calls exist on the backend ----------
export type RoutesExist = Expect<
  Assignable<
    | "/api/health"
    | "/api/auth/register"
    | "/api/auth/login"
    | "/api/auth/demo-personas"
    | "/api/auth/telegram"
    | "/api/auth/telegram/link-token"
    | "/api/auth/telegram/branch"
    | "/api/me"
    | "/api/agent-auth/token"
    | "/api/agents"
    | "/api/agents/anomalies"
    | "/api/agents/{agent_id}"
    | "/api/agents/{agent_id}/review"
    | "/api/agents/{agent_id}/rotate-secret"
    | "/api/agents/{agent_id}/activity"
    | "/api/routing/inspect"
    | "/api/governance/routing"
    | "/api/governance/simulate-policy"
    | "/api/products"
    | "/api/products/deals"
    | "/api/products/check"
    | "/api/products/substitutes"
    | "/api/products/{product_id}"
    | "/api/categories"
    | "/api/promotions"
    | "/api/promotions/{promotion_id}"
    | "/api/admin/aimo-campaigns"
    | "/api/admin/aimo-campaigns/{campaign_id}"
    | "/api/admin/company-negotiations"
    | "/api/admin/company-negotiations/{company_id}"
    | "/api/brand-preferences"
    | "/api/brand-preferences/{preference_id}"
    | "/api/account/negotiation-settings"
    | "/api/account/assistant-settings"
    | "/api/menu/plan"
    | "/api/quotes"
    | "/api/quotes/negotiate"
    | "/api/quotes/optimize"
    | "/api/quotes/{quote_id}"
    | "/api/orders"
    | "/api/orders/{order_id}"
    | "/api/orders/{order_id}/trace"
    | "/api/orders/{order_id}/approve"
    | "/api/orders/{order_id}/reject"
    | "/api/orders/{order_id}/cancel"
    | "/api/dashboard"
    | "/api/audit"
    | "/api/audit/verify"
    | "/api/dev/simulator/scenarios"
    | "/api/dev/simulator/run"
    | "/api/governance/summary"
    | "/api/assistant/chat"
    | "/api/a2a"
    | "/api/a2a/tasks"
    | "/api/.well-known/agent-card.json",
    keyof paths
  >
>;

// ---------- Enums (equal both ways) ----------
export type Enums = [
  Expect<Equal<T.OrderStatus, S["OrderOut"]["status"]>>,
  Expect<Equal<T.QuoteStatus, S["QuoteOut"]["status"]>>,
  Expect<Equal<T.DeliveryStatus, S["QuoteLine"]["delivery_status"]>>,
  Expect<Equal<T.LineFlag, NonNullable<S["QuoteLine"]["flags"]>[number]>>,
  Expect<Equal<T.PolicyOutcome, S["PolicyDecision"]["outcome"]>>,
  Expect<Equal<T.AgentStatus, S["AgentOut"]["status"]>>,
  Expect<Equal<T.PrincipalKind, S["Principal"]["kind"]>>,
  Expect<Equal<T.LogisticModel, NonNullable<S["QuoteLine"]["logistic_model"]>>>,
  Expect<Equal<T.AvailableQtyState, NonNullable<S["QuoteLine"]["available_qty_state"]>>>,
  Expect<Equal<T.AuditEvent["outcome"], S["AuditEventOut"]["outcome"]>>,
  Expect<Equal<T.A2ATaskState, S["A2ATaskTraceOut"]["state"]>>,
  Expect<Equal<T.OrderTraceStepSource, S["OrderTraceStepOut"]["source"]>>,
  Expect<Equal<T.MealType, S["MenuRecipeSummaryOut"]["meal_type"]>>,
  Expect<Equal<T.ReservationStatus, NonNullable<S["OrderOut"]["reservation_status"]>>>,
  Expect<Equal<T.NegotiationTurnActor, S["NegotiationTurnOut"]["actor"]>>,
  Expect<Equal<T.NegotiationTurnAction, S["NegotiationTurnOut"]["action"]>>,
  Expect<Equal<T.NegotiationOutcomeStatus, S["QuoteNegotiateResponse"]["status"]>>,
];

// ---------- Responses: backend → UI ----------
export type Responses = [
  Expect<Assignable<Serialized<S["Principal"]>, T.Principal>>,
  Expect<Assignable<Serialized<S["ProductOut"]>, T.Product>>,
  Expect<Assignable<Serialized<S["QuoteLine"]>, T.QuoteLine>>,
  Expect<Assignable<Serialized<S["QuoteOut"]>, T.Quote>>,
  Expect<Assignable<Serialized<S["OrderOut"]>, T.Order>>,
  Expect<Assignable<Serialized<S["AgentOut"]>, T.Agent>>,
  Expect<Assignable<Serialized<S["AuditEventOut"]>, T.AuditEvent>>,
  Expect<Assignable<Serialized<S["A2ATraceEntry"]>, T.A2AHistoryEntry>>,
  Expect<Assignable<Serialized<S["A2ATaskTraceOut"]>, T.A2ATask>>,
  Expect<Assignable<Serialized<S["AssistantChatResponse"]>, T.AssistantChatResponse>>,
  Expect<Assignable<Res<"/api/health", "get">, T.Health>>,
  Expect<Assignable<Res<"/api/auth/register", "post">, T.RegisterResponse>>,
  Expect<Assignable<Res<"/api/auth/login", "post">, T.LoginResponse>>,
  Expect<Assignable<Res<"/api/me", "get">, T.MeResponse>>,
  Expect<Assignable<Res<"/api/agent-auth/token", "post">, T.AgentTokenResponse>>,
  Expect<Assignable<Res<"/api/agents", "get">, T.Page<T.Agent>>>,
  Expect<Assignable<Res<"/api/agents", "post">, T.AgentWithSecret>>,
  Expect<Assignable<Res<"/api/agents/{agent_id}", "get">, T.Agent>>,
  Expect<Assignable<Res<"/api/agents/{agent_id}", "patch">, T.Agent>>,
  Expect<Assignable<Res<"/api/agents/{agent_id}/rotate-secret", "post">, T.AgentWithSecret>>,
  Expect<Assignable<Res<"/api/agents/{agent_id}/activity", "get">, T.Page<T.AuditEvent>>>,
  Expect<Assignable<Res<"/api/products", "get">, T.Page<T.Product>>>,
  Expect<Assignable<Res<"/api/products/{product_id}", "get">, T.Product>>,
  Expect<Assignable<Res<"/api/products/check", "post">, T.ProductCheckResponse>>,
  Expect<Assignable<Res<"/api/categories", "get">, { items: T.Category[] }>>,
  Expect<Assignable<Res<"/api/quotes", "post">, T.Quote>>,
  Expect<Assignable<Res<"/api/quotes/{quote_id}", "get">, T.Quote>>,
  Expect<Assignable<Res<"/api/orders", "get">, T.Page<T.Order>>>,
  Expect<Assignable<Res<"/api/orders", "post">, T.Order>>,
  Expect<Assignable<Res<"/api/orders/{order_id}", "get">, T.Order>>,
  Expect<Assignable<Res<"/api/orders/{order_id}/approve", "post">, T.Order>>,
  Expect<Assignable<Res<"/api/orders/{order_id}/reject", "post">, T.Order>>,
  Expect<Assignable<Res<"/api/orders/{order_id}/cancel", "post">, T.Order>>,
  Expect<Assignable<Res<"/api/dashboard", "get">, T.Dashboard>>,
  Expect<Assignable<Res<"/api/audit", "get">, T.Page<T.AuditEvent>>>,
  Expect<Assignable<Res<"/api/audit/verify", "get">, T.AuditVerifyResponse>>,
  Expect<Assignable<Res<"/api/governance/summary", "get">, T.GovernanceSummaryResponse>>,
  Expect<Assignable<Res<"/api/orders/{order_id}/trace", "get">, T.OrderTraceResponse>>,
  Expect<Assignable<Res<"/api/promotions", "get">, T.Page<T.PromotionOut>>>,
  Expect<Assignable<Res<"/api/promotions", "post">, T.PromotionOut>>,
  Expect<Assignable<Res<"/api/promotions/{promotion_id}", "patch">, T.PromotionOut>>,
  Expect<Assignable<Res<"/api/promotions/{promotion_id}", "delete">, T.PromotionDeleteResponse>>,
  Expect<Assignable<Res<"/api/products/deals", "get">, T.DealsResponse>>,
  Expect<Assignable<Res<"/api/menu/plan", "post">, T.MenuPlanResponse>>,
  Expect<Assignable<Res<"/api/a2a/tasks", "get">, T.Page<T.A2ATask>>>,
  Expect<Assignable<Res<"/api/assistant/chat", "post">, T.AssistantChatResponse>>,
  Expect<Assignable<Res<"/api/auth/demo-personas", "get">, T.DemoPersonasResponse>>,
  Expect<Assignable<Res<"/api/auth/telegram", "get">, T.TelegramOptInOut>>,
  Expect<Assignable<Res<"/api/auth/telegram", "put">, T.TelegramOptInOut>>,
  Expect<Assignable<Res<"/api/auth/telegram", "delete">, T.TelegramOptInOut>>,
  Expect<Assignable<Res<"/api/auth/telegram/link-token", "post">, T.TelegramLinkTokenOut>>,
  Expect<Assignable<Res<"/api/auth/telegram/branch", "put">, T.TelegramOptInOut>>,
  Expect<Assignable<Res<"/api/agents/anomalies", "get">, T.AgentAnomaliesResponse>>,
  Expect<Assignable<Res<"/api/agents/{agent_id}/review", "post">, T.AgentQuarantineReviewResponse>>,
  Expect<Assignable<Res<"/api/routing/inspect", "post">, T.RoutingInspectResponse>>,
  Expect<Assignable<Res<"/api/quotes/optimize", "post">, T.QuoteOptimizeResponse>>,
  Expect<Assignable<Res<"/api/quotes/negotiate", "post">, T.QuoteNegotiateResponse>>,
  Expect<Assignable<Res<"/api/admin/aimo-campaigns", "get">, T.Page<T.AimoCampaignOut>>>,
  Expect<Assignable<Res<"/api/admin/aimo-campaigns", "post">, T.AimoCampaignOut>>,
  Expect<Assignable<Res<"/api/admin/aimo-campaigns/{campaign_id}", "patch">, T.AimoCampaignOut>>,
  Expect<Assignable<Res<"/api/admin/aimo-campaigns/{campaign_id}", "delete">, T.AimoCampaignDeleteResponse>>,
  Expect<Assignable<Res<"/api/admin/company-negotiations", "get">, T.CompanyNegotiationAdminListResponse>>,
  Expect<Assignable<Res<"/api/admin/company-negotiations/{company_id}", "put">, T.CompanyNegotiationAdminOut>>,
  Expect<Assignable<Res<"/api/brand-preferences", "get">, T.Page<T.BrandPreferenceOut>>>,
  Expect<Assignable<Res<"/api/brand-preferences", "post">, T.BrandPreferenceOut>>,
  Expect<Assignable<Res<"/api/brand-preferences/{preference_id}", "patch">, T.BrandPreferenceOut>>,
  Expect<Assignable<Res<"/api/brand-preferences/{preference_id}", "delete">, T.BrandPreferenceDeleteResponse>>,
  Expect<Assignable<Res<"/api/account/negotiation-settings", "get">, T.NegotiationSettingsOut>>,
  Expect<Assignable<Res<"/api/account/negotiation-settings", "put">, T.NegotiationSettingsOut>>,
  Expect<Assignable<Res<"/api/account/assistant-settings", "get">, T.AssistantSettingsOut>>,
  Expect<Assignable<Res<"/api/account/assistant-settings", "put">, T.AssistantSettingsOut>>,
  Expect<Assignable<Res<"/api/.well-known/agent-card.json", "get">, T.A2AAgentCard>>,
  Expect<Assignable<Res<"/api/dev/simulator/scenarios", "get">, T.SimulatorScenariosResponse>>,
  Expect<
    Assignable<
      Res<"/api/dev/simulator/run", "post">,
      Omit<T.SimulatorRunResponse, "substitutes_preview"> & {
        substitutes_preview?: {
          product: T.SubstituteItem["product"];
          quote_line: T.SubstituteItem["line"];
          tradeoffs: string[];
          reason: string;
          tier?: T.SubstituteItem["tier"];
        }[];
      }
    >
  >,
  // Mismatch 2: the backend's substitute items are `{ product, quote_line, tradeoffs, reason, tier? }`; endpoints.ts
  // normalizeSubstitutes() reads exactly these names (plus the proposal's `line` / `trade_offs`). Assert what it reads.
  Expect<
    Assignable<
      Res<"/api/products/substitutes", "post">,
      { items: { product: T.SubstituteItem["product"]; quote_line: T.SubstituteItem["line"]; tradeoffs: string[]; reason: string; tier?: T.SubstituteItem["tier"] }[] }
    >
  >,
];

// ---------- Requests: UI → backend ----------
export type Requests = [
  Expect<Assignable<T.RegisterRequest, Body<"/api/auth/register", "post">>>,
  Expect<Assignable<T.LoginRequest, Body<"/api/auth/login", "post">>>,
  Expect<Assignable<T.AgentTokenRequest, Body<"/api/agent-auth/token", "post">>>,
  // Mismatch 6: `can_place_orders` is optional in the contract but required by the backend. The agent form always
  // sends it (AgentForm formToCreate), so assert that adapted shape.
  Expect<Assignable<T.AgentCreateRequest & { can_place_orders: boolean }, Body<"/api/agents", "post">>>,
  Expect<Assignable<T.AgentUpdateRequest, Body<"/api/agents/{agent_id}", "patch">>>,
  Expect<Assignable<T.ProductCheckRequest, Body<"/api/products/check", "post">>>,
  Expect<Assignable<T.SubstitutesRequest, Body<"/api/products/substitutes", "post">>>,
  Expect<Assignable<T.QuoteCreateRequest, Body<"/api/quotes", "post">>>,
  Expect<Assignable<T.OrderCreateRequest, Body<"/api/orders", "post">>>,
  Expect<Assignable<T.ApproveRequest, Body<"/api/orders/{order_id}/approve", "post">>>,
  Expect<Assignable<T.NoteRequest, Body<"/api/orders/{order_id}/reject", "post">>>,
  Expect<Assignable<T.NoteRequest, Body<"/api/orders/{order_id}/cancel", "post">>>,
  Expect<Assignable<T.AssistantChatRequest, Body<"/api/assistant/chat", "post">>>,
  Expect<Assignable<T.PromotionCreateRequest, Body<"/api/promotions", "post">>>,
  Expect<Assignable<T.PromotionPatchRequest, Body<"/api/promotions/{promotion_id}", "patch">>>,
  Expect<Assignable<T.MenuPlanRequest, Body<"/api/menu/plan", "post">>>,
  Expect<Assignable<T.AgentQuarantineReviewRequest, Body<"/api/agents/{agent_id}/review", "post">>>,
  Expect<Assignable<T.RoutingInspectRequest & { method: string }, Body<"/api/routing/inspect", "post">>>,
  Expect<Assignable<T.TelegramOptInUpdateRequest & { enabled: boolean }, Body<"/api/auth/telegram", "put">>>,
  Expect<Assignable<T.TelegramBranchUpdateRequest, Body<"/api/auth/telegram/branch", "put">>>,
  Expect<Assignable<T.QuoteOptimizeRequest, Body<"/api/quotes/optimize", "post">>>,
  Expect<Assignable<T.QuoteNegotiateRequest, Body<"/api/quotes/negotiate", "post">>>,
  Expect<Assignable<T.AimoCampaignCreateRequest, Body<"/api/admin/aimo-campaigns", "post">>>,
  Expect<Assignable<T.AimoCampaignPatchRequest, Body<"/api/admin/aimo-campaigns/{campaign_id}", "patch">>>,
  Expect<Assignable<T.BrandPreferenceCreateRequest, Body<"/api/brand-preferences", "post">>>,
  Expect<Assignable<T.BrandPreferencePatchRequest, Body<"/api/brand-preferences/{preference_id}", "patch">>>,
  Expect<Assignable<T.NegotiationSettingsUpdateRequest, Body<"/api/account/negotiation-settings", "put">>>,
  Expect<Assignable<T.NegotiationSettingsUpdateRequest, Body<"/api/admin/company-negotiations/{company_id}", "put">>>,
  Expect<Assignable<T.AssistantSettingsUpdateRequest, Body<"/api/account/assistant-settings", "put">>>,
  Expect<Assignable<T.SimulatorRunRequest, Body<"/api/dev/simulator/run", "post">>>,
  // Query strings: every param the UI sends is known to the backend with a compatible type.
  Expect<Assignable<T.ProductSearchParams, Query<"/api/products">>>,
  Expect<Assignable<T.ProductDealsParams, Query<"/api/products/deals">>>,
  Expect<Assignable<T.OrderListParams, Query<"/api/orders">>>,
  Expect<Assignable<T.AuditParams, Query<"/api/audit">>>,
  Expect<Assignable<T.A2ATaskParams, Query<"/api/a2a/tasks">>>,
];
