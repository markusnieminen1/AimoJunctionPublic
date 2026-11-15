// TanStack Query hooks for every endpoint. Query options: `{ enabled?, silent? }` (silent = no auto error toast).
// Mutations: `{ silent? }`. All mutating order calls take an explicit Idempotency-Key (see useIdempotencyKey).
import { useCallback, useReducer } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { isEndpointMissing, newIdempotencyKey, toApiError } from "./client";
import { endpoints } from "./endpoints";
import type {
  A2ATaskParams,
  AgentCreateRequest,
  AgentQuarantineReviewRequest,
  AgentUpdateRequest,
  AimoCampaignCreateRequest,
  AimoCampaignPatchRequest,
  AuditParams,
  BrandPreferenceCreateRequest,
  BrandPreferencePatchRequest,
  MenuPlanRequest,
  NegotiationSettingsUpdateRequest,
  Order,
  OrderListParams,
  ProductCheckRequest,
  ProductDealsParams,
  ProductSearchParams,
  PromotionCreateRequest,
  PromotionPatchRequest,
  QuoteCreateRequest,
  QuoteNegotiateRequest,
  QuoteOptimizeRequest,
  RoutingInspectRequest,
  SimulatorRunRequest,
  SubstitutesRequest,
  TelegramBranchUpdateRequest,
  TelegramOptInUpdateRequest,
  AssistantChatRequest,
  AssistantSettingsUpdateRequest,
  Quote,
} from "./types";

export type QueryOpts = { enabled?: boolean; silent?: boolean; refetchInterval?: number | false };
export type MutationOpts = { silent?: boolean };

const meta = (o?: { silent?: boolean }) => (o?.silent ? { silent: true } : undefined);

/** Query keys. Invalidate by prefix, e.g. `qc.invalidateQueries({ queryKey: qk.ordersAll })`. */
export const qk = {
  health: ["health"] as const,
  me: ["me"] as const,
  agentsAll: ["agents"] as const,
  agents: () => ["agents", "list"] as const,
  agentAnomalies: () => ["agents", "anomalies"] as const,
  demoPersonas: ["auth", "demoPersonas"] as const,
  agent: (id: string) => ["agents", "detail", id] as const,
  agentActivity: (id: string) => ["agents", "activity", id] as const,
  products: (p: ProductSearchParams) => ["products", "search", p] as const,
  productDeals: (p: ProductDealsParams) => ["products", "deals", p] as const,
  product: (id: string) => ["products", "detail", id] as const,
  categories: ["categories"] as const,
  promotions: ["promotions"] as const,
  quote: (id: string) => ["quotes", id] as const,
  ordersAll: ["orders"] as const,
  orders: (p: OrderListParams) => ["orders", "list", p] as const,
  order: (id: string) => ["orders", "detail", id] as const,
  orderTrace: (id: string) => ["orders", "trace", id] as const,
  dashboard: ["dashboard"] as const,
  audit: (p: AuditParams) => ["audit", p] as const,
  auditVerify: ["audit", "verify"] as const,
  governanceSummary: ["governance", "summary"] as const,
  a2a: (p: A2ATaskParams) => ["a2a", p] as const,
  a2aAgentCard: ["a2a", "agentCard"] as const,
  a2aSession: ["a2a", "session"] as const,
  simulatorScenarios: ["dev", "simulator", "scenarios"] as const,
  telegramOptIn: ["auth", "telegram"] as const,
  aimoCampaigns: ["admin", "aimoCampaigns"] as const,
  companyNegotiations: ["admin", "companyNegotiations"] as const,
  brandPreferences: ["brandPreferences"] as const,
  negotiationSettings: ["account", "negotiationSettings"] as const,
  assistantSettings: ["account", "assistantSettings"] as const,
};

const PENDING_POLL_MS = 10_000;
const SUBMIT_POLL_MS = 3_000;

/** Statuses that are still settling; poll the order every 3 s while in them. */
export const SETTLING_STATUSES: Order["status"][] = ["SUBMITTING", "SUBMISSION_UNKNOWN"];

/** Invalidate everything an order state change can affect. */
export function invalidateOrderViews(qc: QueryClient, order?: Order) {
  if (order) qc.setQueryData(qk.order(order.id), order);
  void qc.invalidateQueries({ queryKey: qk.ordersAll });
  void qc.invalidateQueries({ queryKey: qk.dashboard });
  void qc.invalidateQueries({ queryKey: qk.agentsAll });
  void qc.invalidateQueries({ queryKey: ["audit"] });
  void qc.invalidateQueries({ queryKey: qk.governanceSummary });
  void qc.invalidateQueries({ queryKey: ["a2a"] });
}

// Keys live outside React so a remount (navigating away and back, the same quote shown in the cart and in a chat card)
// reuses the key of the same logical action. Cleared on login/logout.
const idempotencyKeys = new Map<string, string>();

/** The Idempotency-Key for `scope`, created on first use. */
export function idempotencyKeyFor(scope: string): string {
  let key = idempotencyKeys.get(scope);
  if (!key) {
    key = newIdempotencyKey();
    idempotencyKeys.set(scope, key);
  }
  return key;
}

/** Forget every remembered key (session change). */
export function clearIdempotencyKeys(): void {
  idempotencyKeys.clear();
}

/**
 * Stable Idempotency-Key for one logical action. `scope` must describe the request content (e.g. quote id + note):
 * the same scope always yields the same key (double clicks, retries, remounts), a different scope a different key.
 * `rotate()` forces a new key for the current scope.
 */
export function useIdempotencyKey(scope: string): { key: string; rotate: () => void } {
  const [, bump] = useReducer((n: number) => n + 1, 0);
  const key = idempotencyKeyFor(scope);
  const rotate = useCallback(() => {
    idempotencyKeys.set(scope, newIdempotencyKey());
    bump();
  }, [scope]);
  return { key, rotate };
}

// ---------- Health / me ----------

export function useHealth(o?: QueryOpts) {
  return useQuery({ queryKey: qk.health, queryFn: () => endpoints.health(), enabled: o?.enabled, meta: meta(o), staleTime: 30_000 });
}

export function useMe(o?: QueryOpts) {
  return useQuery({ queryKey: qk.me, queryFn: () => endpoints.me(), enabled: o?.enabled, meta: meta(o) });
}

// ---------- Agents ----------

export function useAgents(o?: QueryOpts) {
  return useQuery({ queryKey: qk.agents(), queryFn: () => endpoints.listAgents(), enabled: o?.enabled, meta: meta(o) });
}

export function useAgent(id: string | undefined, o?: QueryOpts) {
  return useQuery({
    queryKey: qk.agent(id ?? ""),
    queryFn: () => endpoints.getAgent(id!),
    enabled: !!id && o?.enabled !== false,
    meta: meta(o),
  });
}

export function useAgentActivity(id: string | undefined, o?: QueryOpts) {
  return useQuery({
    queryKey: qk.agentActivity(id ?? ""),
    queryFn: () => endpoints.agentActivity(id!),
    enabled: !!id && o?.enabled !== false,
    meta: meta(o),
  });
}

/** Returns `{ agent, agent_secret }`; show the secret once, never store it. */
export function useCreateAgent(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: AgentCreateRequest) => endpoints.createAgent(b),
    meta: meta(o),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.agentsAll });
      void qc.invalidateQueries({ queryKey: qk.dashboard });
    },
  });
}

/** PATCH any subset of fields, incl. `status: "disabled"` (kill switch). */
export function useUpdateAgent(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: AgentUpdateRequest }) => endpoints.updateAgent(id, patch),
    meta: meta(o),
    onSuccess: (agent) => {
      qc.setQueryData(qk.agent(agent.id), agent);
      void qc.invalidateQueries({ queryKey: qk.agentsAll });
      void qc.invalidateQueries({ queryKey: qk.dashboard });
    },
  });
}

export function useRotateAgentSecret(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => endpoints.rotateAgentSecret(id),
    meta: meta(o),
    onSuccess: (r) => {
      qc.setQueryData(qk.agent(r.agent.id), r.agent);
      void qc.invalidateQueries({ queryKey: qk.agentsAll });
    },
  });
}

// ---------- Products ----------

export function useProducts(params: ProductSearchParams, o?: QueryOpts) {
  return useQuery({
    queryKey: qk.products(params),
    queryFn: () => endpoints.searchProducts(params),
    enabled: o?.enabled,
    meta: meta(o),
    placeholderData: keepPreviousData,
  });
}

export function useProduct(id: string | undefined, o?: QueryOpts) {
  return useQuery({
    queryKey: qk.product(id ?? ""),
    queryFn: () => endpoints.getProduct(id!),
    enabled: !!id && o?.enabled !== false,
    meta: meta(o),
  });
}

export function useCategories(o?: QueryOpts) {
  return useQuery({ queryKey: qk.categories, queryFn: () => endpoints.categories(), enabled: o?.enabled, meta: meta(o), staleTime: 5 * 60_000 });
}

/** Live price + availability (no quote stored). */
export function useProductCheck(o?: MutationOpts) {
  return useMutation({ mutationFn: (b: ProductCheckRequest) => endpoints.checkProducts(b), meta: meta(o) });
}

/** ADDITIVE: diet-safe substitutes for a risky line. */
export function useSubstitutes(o?: MutationOpts) {
  return useMutation({ mutationFn: (b: SubstitutesRequest) => endpoints.substitutes(b), meta: meta(o) });
}

// ---------- Quotes ----------

export function useCreateQuote(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: QuoteCreateRequest) => endpoints.createQuote(b),
    meta: meta(o),
    onSuccess: (q: Quote) => qc.setQueryData(qk.quote(q.id), q),
  });
}

export function useOptimizeQuote(o?: MutationOpts) {
  return useMutation({
    mutationFn: (b: QuoteOptimizeRequest) => endpoints.optimizeQuote(b),
    meta: meta(o),
  });
}

/** `refetchIntervalMs` polls while the quote is still OPEN (e.g. to learn whether a lost order request used it). */
export function useQuote(id: string | undefined, o?: QueryOpts & { refetchIntervalMs?: number }) {
  return useQuery({
    queryKey: qk.quote(id ?? ""),
    queryFn: () => endpoints.getQuote(id!),
    enabled: !!id && o?.enabled !== false,
    meta: meta(o),
    refetchInterval: (q) => (o?.refetchIntervalMs && (!q.state.data || q.state.data.status === "OPEN") ? o.refetchIntervalMs : false),
  });
}

// ---------- Orders ----------

export function useOrders(params: OrderListParams = {}, o?: QueryOpts & { refetchIntervalMs?: number }) {
  return useQuery({
    queryKey: qk.orders(params),
    queryFn: () => endpoints.listOrders(params),
    enabled: o?.enabled,
    meta: meta(o),
    placeholderData: keepPreviousData,
    refetchInterval: o?.refetchIntervalMs,
  });
}

/** Single order; polls every 3 s while SUBMITTING / SUBMISSION_UNKNOWN. */
export function useOrder(id: string | undefined, o?: QueryOpts) {
  return useQuery({
    queryKey: qk.order(id ?? ""),
    queryFn: () => endpoints.getOrder(id!),
    enabled: !!id && o?.enabled !== false,
    meta: meta(o),
    refetchInterval: (q) => (q.state.data && SETTLING_STATUSES.includes(q.state.data.status) ? SUBMIT_POLL_MS : false),
  });
}

/** Approval inbox: PENDING_APPROVAL orders, polled every 10 s. */
export function useApprovals(o?: QueryOpts) {
  return useQuery({
    queryKey: qk.orders({ status: "PENDING_APPROVAL", limit: 50 }),
    queryFn: () => endpoints.listOrders({ status: "PENDING_APPROVAL", limit: 50 }),
    enabled: o?.enabled,
    meta: meta(o),
    refetchInterval: PENDING_POLL_MS,
  });
}

/** Number of pending approvals for nav badges, polled every 10 s. Pass enabled=false for agents/anonymous. */
export function usePendingApprovalsCount(o?: QueryOpts) {
  return useQuery({
    queryKey: qk.orders({ status: "PENDING_APPROVAL", limit: 1 }),
    queryFn: () => endpoints.listOrders({ status: "PENDING_APPROVAL", limit: 1 }),
    enabled: o?.enabled,
    meta: { silent: true },
    refetchInterval: PENDING_POLL_MS,
    select: (p) => p.total,
  });
}

/** POST /api/orders. Pass the key from useIdempotencyKey(quote.id) so double clicks/retries reuse it. */
export function usePlaceOrder(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ quote_id, note, idempotencyKey }: { quote_id: string; note?: string; idempotencyKey: string }) =>
      endpoints.createOrder({ quote_id, note }, idempotencyKey),
    meta: meta(o),
    onSuccess: (order) => {
      invalidateOrderViews(qc, order);
      void qc.invalidateQueries({ queryKey: qk.quote(order.quote_id) });
    },
    onError: (_e, v) => void qc.invalidateQueries({ queryKey: qk.quote(v.quote_id) }),
  });
}

/** Approve with the content_hash the approver saw. 409 APPROVAL_CONTENT_CHANGED / QUOTE_STALE → refetch + re-ask. */
export function useApproveOrder(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, content_hash, note, idempotencyKey }: { id: string; content_hash: string; note?: string; idempotencyKey: string }) =>
      endpoints.approveOrder(id, { content_hash, note }, idempotencyKey),
    meta: meta(o),
    onSuccess: (order) => invalidateOrderViews(qc, order),
    onError: () => invalidateOrderViews(qc),
  });
}

export function useRejectOrder(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, note }: { id: string; note?: string }) => endpoints.rejectOrder(id, { note }),
    meta: meta(o),
    onSuccess: (order) => invalidateOrderViews(qc, order),
  });
}

export function useCancelOrder(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, note }: { id: string; note?: string }) => endpoints.cancelOrder(id, { note }),
    meta: meta(o),
    onSuccess: (order) => invalidateOrderViews(qc, order),
  });
}

// ---------- Dashboard / audit / A2A ----------

export function useDashboard(o?: QueryOpts) {
  return useQuery({ queryKey: qk.dashboard, queryFn: () => endpoints.dashboard(), enabled: o?.enabled, meta: meta(o), refetchInterval: 15_000 });
}

export function useAudit(params: AuditParams = {}, o?: QueryOpts) {
  return useQuery({
    queryKey: qk.audit(params),
    queryFn: () => endpoints.audit(params),
    enabled: o?.enabled,
    meta: meta(o),
    placeholderData: keepPreviousData,
  });
}

/** ADDITIVE: A2A protocol trace, polled every 10 s. */
export function useA2ATasks(params: A2ATaskParams = {}, o?: QueryOpts) {
  return useQuery({
    queryKey: qk.a2a(params),
    queryFn: () => endpoints.a2aTasks(params),
    enabled: o?.enabled,
    meta: meta(o),
    // A server without the endpoint (404, no envelope) is not polled again.
    refetchInterval: (q) => (isEndpointMissing(q.state.error) ? false : PENDING_POLL_MS),
  });
}

export function useA2AAgentCard(o?: QueryOpts) {
  return useQuery({
    queryKey: qk.a2aAgentCard,
    queryFn: () => endpoints.a2aAgentCard(),
    enabled: o?.enabled,
    meta: meta(o),
    staleTime: 60_000,
    retry: false,
  });
}

export function useA2ASessionConnect(o?: QueryOpts) {
  return useQuery({
    queryKey: qk.a2aSession,
    queryFn: () => endpoints.a2aSessionConnect(),
    enabled: o?.enabled,
    meta: meta(o),
    staleTime: 60_000,
    retry: false,
  });
}

// ---------- Assistant ----------

export function useAssistantChat(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: AssistantChatRequest) => endpoints.assistantChat(b),
    meta: meta(o),
    onSuccess: (r) => {
      // Defensive: an unknown or malformed action must never turn a good reply into an error.
      if (Array.isArray(r.actions) && r.actions.some((a) => (a as { type?: unknown } | null)?.type === "order")) invalidateOrderViews(qc);
    },
  });
}

// ---------- M9: Order trace, governance, audit verify, promotions, deals, menu planner ----------

export function useOrderTrace(id: string | undefined, o?: QueryOpts) {
  return useQuery({
    queryKey: qk.orderTrace(id ?? ""),
    queryFn: () => endpoints.orderTrace(id!),
    enabled: !!id && o?.enabled !== false,
    meta: meta(o),
  });
}

export function useAuditVerify(o?: QueryOpts) {
  return useQuery({
    queryKey: qk.auditVerify,
    queryFn: () => endpoints.auditVerify(),
    enabled: o?.enabled,
    meta: meta(o),
  });
}

export function useGovernanceSummary(o?: QueryOpts) {
  return useQuery({
    queryKey: qk.governanceSummary,
    queryFn: () => endpoints.governanceSummary(),
    enabled: o?.enabled,
    meta: meta(o),
  });
}

export function usePromotions(o?: QueryOpts) {
  return useQuery({
    queryKey: qk.promotions,
    queryFn: () => endpoints.listPromotions(),
    enabled: o?.enabled,
    meta: meta(o),
  });
}

export function useCreatePromotion(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: PromotionCreateRequest) => endpoints.createPromotion(b),
    meta: meta(o),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.promotions });
      void qc.invalidateQueries({ queryKey: qk.governanceSummary });
      void qc.invalidateQueries({ queryKey: ["products", "deals"] });
    },
  });
}

export function useUpdatePromotion(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: PromotionPatchRequest }) => endpoints.updatePromotion(id, patch),
    meta: meta(o),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.promotions });
      void qc.invalidateQueries({ queryKey: qk.governanceSummary });
      void qc.invalidateQueries({ queryKey: ["products", "deals"] });
    },
  });
}

export function useDeletePromotion(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => endpoints.deletePromotion(id),
    meta: meta(o),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.promotions });
      void qc.invalidateQueries({ queryKey: qk.governanceSummary });
      void qc.invalidateQueries({ queryKey: ["products", "deals"] });
    },
  });
}

export function useProductDeals(params: ProductDealsParams = {}, o?: QueryOpts) {
  return useQuery({
    queryKey: qk.productDeals(params),
    queryFn: () => endpoints.productDeals(params),
    enabled: o?.enabled,
    meta: meta(o),
    placeholderData: keepPreviousData,
  });
}

export function usePlanMenu(o?: MutationOpts) {
  return useMutation({
    mutationFn: (b: MenuPlanRequest) => endpoints.planMenu(b),
    meta: meta(o),
  });
}

export function useSimulatorScenarios(o?: QueryOpts) {
  return useQuery({
    queryKey: qk.simulatorScenarios,
    queryFn: () => endpoints.simulatorScenarios(),
    enabled: o?.enabled,
    meta: { silent: o?.silent ?? true },
    retry: (n, err) => toApiError(err).status !== 404 && !isEndpointMissing(err) && n < 2,
  });
}

export function useRunSimulatorScenario(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: SimulatorRunRequest) => endpoints.runSimulatorScenario(b),
    meta: meta(o),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["audit"] });
      void qc.invalidateQueries({ queryKey: qk.auditVerify });
      void qc.invalidateQueries({ queryKey: qk.governanceSummary });
      void qc.invalidateQueries({ queryKey: qk.ordersAll });
      void qc.invalidateQueries({ queryKey: qk.dashboard });
    },
  });
}

export function useTelegramOptIn(o?: QueryOpts) {
  return useQuery({
    queryKey: qk.telegramOptIn,
    queryFn: () => endpoints.getTelegramOptIn(),
    enabled: o?.enabled,
    refetchInterval: o?.refetchInterval,
    meta: { silent: o?.silent ?? true },
    retry: (n, err) => {
      const st = toApiError(err).status;
      return st !== 404 && st !== 403 && !isEndpointMissing(err) && n < 2;
    },
  });
}

export function useUpdateTelegramOptIn(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: TelegramOptInUpdateRequest) => endpoints.updateTelegramOptIn(b),
    meta: meta(o),
    onSuccess: (data) => {
      qc.setQueryData(qk.telegramOptIn, data);
    },
  });
}

export function useDeleteTelegramOptIn(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => endpoints.deleteTelegramOptIn(),
    meta: meta(o),
    onSuccess: (data) => {
      qc.setQueryData(qk.telegramOptIn, data);
    },
  });
}

export function useCreateTelegramLinkToken(o?: MutationOpts) {
  return useMutation({
    mutationFn: () => endpoints.createTelegramLinkToken(),
    meta: meta(o),
  });
}

export function useUpdateTelegramBranch(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: TelegramBranchUpdateRequest) => endpoints.updateTelegramBranch(b),
    meta: meta(o),
    onSuccess: (data) => {
      qc.setQueryData(qk.telegramOptIn, data);
    },
  });
}

export function useInspectRouting(o?: MutationOpts) {
  return useMutation({
    mutationFn: (b: RoutingInspectRequest) => endpoints.inspectRouting(b),
    meta: meta(o),
  });
}

export function useAgentAnomalies(o?: QueryOpts) {
  return useQuery({
    queryKey: qk.agentAnomalies(),
    queryFn: () => endpoints.agentAnomalies(),
    enabled: o?.enabled,
    meta: { silent: o?.silent ?? true },
    retry: (n, err) => {
      const st = toApiError(err).status;
      return st !== 404 && st !== 403 && !isEndpointMissing(err) && n < 2;
    },
  });
}

export function useReviewAgentAnomaly(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: AgentQuarantineReviewRequest }) =>
      endpoints.reviewAgentAnomaly(id, body),
    meta: meta(o),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.agentsAll });
    },
  });
}

export function useDemoPersonas(o?: QueryOpts) {
  return useQuery({
    queryKey: qk.demoPersonas,
    queryFn: () => endpoints.demoPersonas(),
    enabled: o?.enabled,
    meta: { silent: o?.silent ?? true },
    retry: false,
  });
}

// ---------- Batch C (msg=40 Admin Invisible Campaigns & Brand Preferences + msg=42 Negotiation) ----------

export function useAimoCampaigns(o?: QueryOpts) {
  return useQuery({
    queryKey: qk.aimoCampaigns,
    queryFn: () => endpoints.listAimoCampaigns(),
    enabled: o?.enabled,
    meta: { silent: o?.silent ?? true },
    retry: (n, err) => {
      const st = toApiError(err).status;
      return st !== 404 && st !== 403 && !isEndpointMissing(err) && n < 2;
    },
  });
}

export function useCreateAimoCampaign(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: AimoCampaignCreateRequest) => endpoints.createAimoCampaign(b),
    meta: meta(o),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.aimoCampaigns });
      void qc.invalidateQueries({ queryKey: ["products"] });
    },
  });
}

export function useUpdateAimoCampaign(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: AimoCampaignPatchRequest }) => endpoints.updateAimoCampaign(id, patch),
    meta: meta(o),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.aimoCampaigns });
      void qc.invalidateQueries({ queryKey: ["products"] });
    },
  });
}

export function useDeleteAimoCampaign(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => endpoints.deleteAimoCampaign(id),
    meta: meta(o),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.aimoCampaigns });
      void qc.invalidateQueries({ queryKey: ["products"] });
    },
  });
}

export function useBrandPreferences(o?: QueryOpts) {
  return useQuery({
    queryKey: qk.brandPreferences,
    queryFn: () => endpoints.listBrandPreferences(),
    enabled: o?.enabled,
    meta: { silent: o?.silent ?? true },
    retry: (n, err) => {
      const st = toApiError(err).status;
      return st !== 404 && st !== 403 && !isEndpointMissing(err) && n < 2;
    },
  });
}

export function useCreateBrandPreference(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: BrandPreferenceCreateRequest) => endpoints.createBrandPreference(b),
    meta: meta(o),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.brandPreferences });
      void qc.invalidateQueries({ queryKey: ["products"] });
    },
  });
}

export function useUpdateBrandPreference(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: BrandPreferencePatchRequest }) => endpoints.updateBrandPreference(id, patch),
    meta: meta(o),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.brandPreferences });
      void qc.invalidateQueries({ queryKey: ["products"] });
    },
  });
}

export function useDeleteBrandPreference(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => endpoints.deleteBrandPreference(id),
    meta: meta(o),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.brandPreferences });
      void qc.invalidateQueries({ queryKey: ["products"] });
    },
  });
}

export function useNegotiationSettings(o?: QueryOpts) {
  return useQuery({
    queryKey: qk.negotiationSettings,
    queryFn: () => endpoints.getNegotiationSettings(),
    enabled: o?.enabled,
    meta: { silent: o?.silent ?? true },
    retry: (n, err) => {
      const st = toApiError(err).status;
      return st !== 404 && st !== 403 && !isEndpointMissing(err) && n < 2;
    },
  });
}

export function useUpdateNegotiationSettings(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: NegotiationSettingsUpdateRequest) => endpoints.updateNegotiationSettings(b),
    meta: meta(o),
    onSuccess: (data) => {
      qc.setQueryData(qk.negotiationSettings, data);
      void qc.invalidateQueries({ queryKey: qk.companyNegotiations });
      void qc.invalidateQueries({ queryKey: qk.a2aSession });
      void qc.invalidateQueries({ queryKey: qk.a2aAgentCard });
    },
  });
}

export function useCompanyNegotiations(o?: QueryOpts) {
  return useQuery({
    queryKey: qk.companyNegotiations,
    queryFn: () => endpoints.listCompanyNegotiations(),
    enabled: o?.enabled,
    meta: { silent: o?.silent ?? true },
    retry: (n, err) => {
      const st = toApiError(err).status;
      return st !== 404 && st !== 403 && !isEndpointMissing(err) && n < 2;
    },
  });
}

export function useUpdateCompanyNegotiation(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { companyId: string; body: NegotiationSettingsUpdateRequest }) =>
      endpoints.updateCompanyNegotiation(v.companyId, v.body),
    meta: meta(o),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.companyNegotiations });
      void qc.invalidateQueries({ queryKey: qk.negotiationSettings });
      void qc.invalidateQueries({ queryKey: qk.a2aSession });
      void qc.invalidateQueries({ queryKey: qk.a2aAgentCard });
      void qc.invalidateQueries({ queryKey: ["audit"] });
    },
  });
}

export function useNegotiateQuote(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: QuoteNegotiateRequest) => endpoints.negotiateQuote(b),
    meta: meta(o),
    onSuccess: (res) => {
      qc.setQueryData(qk.quote(res.quote.id), res.quote);
      void qc.invalidateQueries({ queryKey: ["audit"] });
    },
  });
}

export function useAssistantSettings(o?: QueryOpts) {
  return useQuery({
    queryKey: qk.assistantSettings,
    queryFn: () => endpoints.getAssistantSettings(),
    enabled: o?.enabled,
    meta: { silent: o?.silent ?? true },
    retry: (n, err) => {
      const st = toApiError(err).status;
      return st !== 404 && st !== 403 && !isEndpointMissing(err) && n < 2;
    },
  });
}

export function useUpdateAssistantSettings(o?: MutationOpts) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: AssistantSettingsUpdateRequest) => endpoints.updateAssistantSettings(b),
    meta: meta(o),
    onSuccess: (data) => {
      qc.setQueryData(qk.assistantSettings, data);
      void qc.invalidateQueries({ queryKey: ["audit"] });
    },
  });
}

