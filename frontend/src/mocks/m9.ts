// M9 mock builders for /api/products/deals, /api/menu/plan, /api/orders/:id/trace, /api/governance/summary, and assistant M9 actions.
import type {
  DealItemOut,
  DealsResponse,
  DietCode,
  GovernanceSummaryResponse,
  MealType,
  MenuIngredientSelectionOut,
  MenuPlanRequest,
  MenuPlanResponse,
  OrderTraceResponse,
  OrderTraceStepOut,
  Principal,
  Product,
  ReasoningStepOut,
} from "../api/types";
import { productDiets } from "../domain/diets";
import { defaultDeliveryDate } from "../lib/dates";
import { isoAt } from "./clock";
import { PRODUCTS, SUPPLY, productById } from "./data";
import { MockHttpError, audit, buildLine, db, evaluatePolicy, findOrder, parseDietField, toAgent } from "./db";

const r2 = (n: number) => Math.round(n * 100) / 100;

function isValioProduct(p: Product): boolean {
  const hay = `${p.brand ?? ""} ${p.vendor ?? ""} ${p.name}`.toLowerCase();
  return hay.includes("valio") || hay.includes("nordic dairy");
}

function matchingPromotions(p: Product, onSale: boolean, companyId: string): string[] {
  const reasons: string[] = [];
  const brandLower = (p.brand ?? "").toLowerCase();
  for (const promo of db.promotions) {
    if (promo.company_id !== companyId || !promo.active) continue;
    const hasCriterion = Boolean(promo.brand_filter || promo.category_prefix || promo.product_ids?.length || promo.prefer_on_sale);
    if (!hasCriterion) continue;
    if (promo.brand_filter && !brandLower.includes(promo.brand_filter.toLowerCase())) continue;
    if (promo.category_prefix && !p.category.startsWith(promo.category_prefix)) continue;
    if (promo.product_ids?.length && !promo.product_ids.includes(p.id)) continue;
    if (promo.prefer_on_sale && !onSale) continue;
    reasons.push(promo.title);
  }
  return reasons;
}

export function buildDealsResponse(
  principal: Principal,
  opts: { q?: string; category?: string; diet?: string; valio_only?: boolean; on_sale_only?: boolean; requested_delivery_date?: string; limit?: number },
  now: number,
): DealsResponse {
  const date = opts.requested_delivery_date || defaultDeliveryDate(now);
  const dietCodes = parseDietField(opts.diet);
  const q = (opts.q ?? "").trim().toLowerCase();
  const limit = Math.min(50, Math.max(1, opts.limit ?? 20));
  const items: DealItemOut[] = [];
  const promoTitles = new Set<string>();

  for (const p of PRODUCTS) {
    if (q) {
      const hay = [p.id, p.name, p.brand, p.vendor, p.category_name].filter(Boolean).join(" ").toLowerCase();
      if (!q.split(/\s+/).every((w) => hay.includes(w))) continue;
    }
    if (opts.category && !p.category.startsWith(opts.category)) continue;
    if (dietCodes.length && !dietCodes.every((d) => productDiets(p).includes(d))) continue;
    if (opts.valio_only && !isValioProduct(p)) continue;

    const supply = SUPPLY[p.id];
    const line = buildLine(p, 1, date, now);
    if (line.unit_price_eur == null) continue;
    const listPrice = supply?.listPrice ?? null;
    const onSale = listPrice != null && listPrice > line.unit_price_eur;
    const savingsPerUnit = onSale ? r2(listPrice - line.unit_price_eur) : 0;
    const discountPct = onSale && listPrice > 0 ? r2((savingsPerUnit / listPrice) * 100) : 0;
    const reasons = matchingPromotions(p, onSale, principal.company_id);
    const promoted = reasons.length > 0;

    if (opts.on_sale_only && !onSale) continue;
    if (!onSale && !promoted) continue;
    for (const r of reasons) promoTitles.add(r);

    items.push({
      product: p,
      quote_line: line,
      list_price_ex_vat: listPrice,
      discount_pct: discountPct,
      savings_per_unit: savingsPerUnit,
      on_sale: onSale,
      promoted,
      promotion_reasons: reasons,
    });
  }

  items.sort((a, b) => Number(b.promoted) - Number(a.promoted) || b.discount_pct - a.discount_pct);
  return {
    items: items.slice(0, limit),
    total: items.length,
    requested_delivery_date: date,
    promotions_applied: [...promoTitles],
  };
}

type RecipeTemplate = {
  id: string;
  name: string;
  meal_type: MealType;
  diet_tags: DietCode[];
  chef_note: string;
  ingredients: { label: string; productId: string; perPaxRatio: number }[];
};

const RECIPES: RecipeTemplate[] = [
  {
    id: "rcp_nordic_porridge",
    name: "Nordic Creamy Morning Porridge & Milk Bar",
    meal_type: "breakfast",
    diet_tags: ["L"],
    chef_note: "Uses lactose-free milk and culinary cream for smooth buffet service.",
    ingredients: [
      { label: "Lactose-free milk", productId: "100001", perPaxRatio: 0.25 },
      { label: "Cooking cream", productId: "100006", perPaxRatio: 0.05 },
    ],
  },
  {
    id: "rcp_nordic_lunch_soup",
    name: "Nordic Velvety Dairy Soup & Artisan Bread",
    meal_type: "lunch",
    diet_tags: ["L", "G"],
    chef_note: "Balanced lunch course pairing lactose-free dairy with gluten-free bread.",
    ingredients: [
      { label: "Lactose-free milk", productId: "100001", perPaxRatio: 0.2 },
      { label: "Culinary dairy base", productId: "100006", perPaxRatio: 0.05 },
      { label: "Gluten-free loaf", productId: "100007", perPaxRatio: 0.1 },
    ],
  },
  {
    id: "rcp_nordic_dinner_gratin",
    name: "Nordic Bistro Cream Gratin",
    meal_type: "dinner",
    diet_tags: ["L"],
    chef_note: "Rich bistro dinner main built around discounted culinary cream and milk.",
    ingredients: [
      { label: "Cooking cream", productId: "100006", perPaxRatio: 0.08 },
      { label: "UHT lactose-free milk", productId: "100002", perPaxRatio: 0.15 },
    ],
  },
];

export function buildMenuPlanResponse(principal: Principal, req: MenuPlanRequest, requestId: string, now: number): MenuPlanResponse {
  const pax = Math.max(1, Math.round(req.pax || 40));
  const mealType: MealType = req.meal_type ?? "lunch";
  const date = req.requested_delivery_date || defaultDeliveryDate(now);
  const dietCodes = parseDietField(req.diet);

  const byMeal = RECIPES.filter((r) => r.meal_type === mealType);
  const dietMatched = (byMeal.length ? byMeal : RECIPES).filter((r) => dietCodes.every((d) => r.diet_tags.includes(d)));
  const useValio = req.include_valio_recipes !== false;
  let activeRecipes: RecipeTemplate[] = [];
  if (useValio) {
    activeRecipes = dietMatched.length ? dietMatched : byMeal.length ? byMeal : [RECIPES[1]!];
  }
  if (activeRecipes.length === 0) {
    const safePool = PRODUCTS.filter(
      (cand) =>
        (!dietCodes.length || dietCodes.every((d) => productDiets(cand).includes(d))) &&
        (SUPPLY[cand.id]?.price ?? null) != null,
    ).slice(0, 3);
    if (safePool.length > 0) {
      activeRecipes = [
        {
          id: "chef_curated_selection",
          name: "Chef's Diet-Safe Nordic Selection",
          meal_type: mealType,
          diet_tags: dietCodes,
          chef_note: "Curated diet-safe selection from available catalog items.",
          ingredients: safePool.map((p) => ({ label: p.name, productId: p.id, perPaxRatio: 0.1 })),
        },
      ];
    }
  }

  const items: MenuIngredientSelectionOut[] = [];
  const promoTitles = new Set<string>();
  const warnings: string[] = [];

  for (const r of activeRecipes) {
    for (const ing of r.ingredients) {
      let prod = productById(ing.productId);
      if (prod && dietCodes.length && !dietCodes.every((d) => productDiets(prod!).includes(d))) {
        const alt = PRODUCTS.find((cand) => dietCodes.every((d) => productDiets(cand).includes(d)) && (SUPPLY[cand.id]?.price ?? null) != null);
        if (alt) prod = alt;
        else warnings.push(`${prod.name} does not match requested diet ${dietCodes.join(",")}`);
      }
      if (!prod) continue;
      const qty = Math.max(1, Math.ceil(pax * ing.perPaxRatio));
      const line = buildLine(prod, qty, date, now);
      const supply = SUPPLY[prod.id];
      const listPrice = supply?.listPrice ?? null;
      const unitPrice = line.unit_price_eur ?? 0;
      const onSale = listPrice != null && listPrice > unitPrice;
      const discountPct = onSale && listPrice > 0 ? r2(((listPrice - unitPrice) / listPrice) * 100) : 0;
      const savingsEur = onSale ? r2((listPrice - unitPrice) * qty) : 0;
      const reasons = matchingPromotions(prod, onSale, principal.company_id);
      reasons.forEach((t) => promoTitles.add(t));
      items.push({
        recipe_id: r.id,
        recipe_name: r.name,
        ingredient_label: ing.label,
        product: prod,
        quote_line: line,
        list_price_ex_vat: listPrice,
        discount_pct: discountPct,
        savings_eur: savingsEur,
        promoted: reasons.length > 0,
        promotion_reasons: reasons,
        selection_reason: onSale
          ? `On sale (-${discountPct}% vs list price)${reasons.length ? ` · ${reasons[0]}` : ""}`
          : reasons.length
            ? `Promoted: ${reasons[0]}`
            : "Confirmed diet-safe & deliverable on requested date",
      });
    }
  }

  const lines = items.map((i) => i.quote_line);
  const estExcl = r2(lines.reduce((s, l) => s + (l.line_total_eur ?? 0), 0));
  const estIncl = r2(lines.reduce((s, l) => s + (l.line_total_eur ?? 0) * (1 + (l.tax_rate ?? 13.5) / 100), 0));
  const estListExcl = r2(items.reduce((s, i) => s + (i.list_price_ex_vat ?? i.quote_line.unit_price_eur ?? 0) * i.quote_line.quantity, 0));
  const estSavings = r2(Math.max(0, estListExcl - estExcl));
  const co2Lines = lines.filter((l) => l.co2_kg != null);
  const co2Sum = co2Lines.reduce((s, l) => s + (l.co2_kg ?? 0), 0);
  if (co2Lines.length > 0 && co2Lines.length < lines.length) {
    warnings.push(`CO2 estimate covers ${co2Lines.length} of ${lines.length} ingredients.`);
  }
  const policyPreview = evaluatePolicy(principal, lines, estExcl, now, undefined, dietCodes);
  const budgetExceeded = req.max_budget_eur != null && estExcl > req.max_budget_eur;
  if (budgetExceeded) warnings.push(`Estimated total exceeds max budget (${req.max_budget_eur} EUR).`);

  const reasoningSteps: ReasoningStepOut[] = [
    {
      step: 1,
      stage: "recipe_match",
      summary: `Matched ${activeRecipes.length} Valio foodservice ${mealType} recipe(s) for ${pax} pax`,
      actor_kind: "assistant",
      at: isoAt(now),
      details: { meal_type: mealType, pax, diet: dietCodes.join(",") || null },
    },
    {
      step: 2,
      stage: "safety_screen",
      summary: dietCodes.length
        ? `Verified all ${items.length} ingredient lines against ${dietCodes.join(", ")} allergen & diet rules`
        : `Checked allergen & delivery status for ${items.length} ingredient lines`,
      actor_kind: "system",
      at: isoAt(now),
      details: { checked_lines: items.length },
    },
    {
      step: 3,
      stage: "promotion_recommendation",
      summary: promoTitles.size ? `Applied ${promoTitles.size} active management promotion(s); saving ${estSavings.toFixed(2)} EUR vs list` : "Ranked ingredients by on-sale savings and delivery reliability",
      actor_kind: "system",
      at: isoAt(now),
      details: { promotions: [...promoTitles], savings_eur: estSavings },
    },
    {
      step: 4,
      stage: "policy_evaluation",
      summary: `Layer 1 policy preview: ${policyPreview.outcome}`,
      actor_kind: "system",
      at: isoAt(now),
      details: { outcome: policyPreview.outcome, rule_ids: policyPreview.rules.map((r) => r.rule_id) },
    },
  ];

  const planId = `menu_${Math.abs(now).toString(36)}`;
  audit(principal.company_id, principal, "menu.plan", "menu_plan", planId, requestId, { pax, meal_type: mealType, diet: dietCodes.join(",") || null, reasoning_steps: reasoningSteps }, "ok", now);

  return {
    plan_id: planId,
    pax,
    meal_type: mealType,
    requested_delivery_date: date,
    diet: dietCodes.length ? dietCodes.join(",") : null,
    recipes: activeRecipes.map((r) => ({ id: r.id, name: r.name, meal_type: r.meal_type, diet_tags: r.diet_tags, chef_note: r.chef_note })),
    items,
    quote_draft_lines: items.map((i) => ({ product_id: i.product.id, quantity: i.quote_line.quantity })),
    estimated_total_excl_vat_eur: estExcl,
    estimated_total_incl_vat_eur: estIncl,
    estimated_list_total_excl_vat_eur: estListExcl,
    estimated_savings_eur: estSavings,
    estimated_co2_kg: co2Sum > 0 ? Math.round(co2Sum * 10) / 10 : null,
    budget_exceeded: budgetExceeded,
    policy_preview: policyPreview,
    promotions_applied: [...promoTitles],
    warnings,
    reasoning_steps: reasoningSteps,
  };
}

export function buildOrderTraceResponse(principal: Principal, orderId: string, now: number): OrderTraceResponse {
  const rec = findOrder(principal, orderId, now);
  const o = rec.order;
  const steps: OrderTraceStepOut[] = [];
  let seq = 1;

  const task = db.a2a.find((t) => t.company_id === principal.company_id && t.linked_order_id === o.id);
  if (task) {
    for (const h of task.history) {
      steps.push({
        step: seq++,
        source: "a2a",
        stage: h.role === "agent" ? "user_prompt" : typeof h.data?.reason === "string" ? h.data.reason.toLowerCase() : "decision",
        summary: h.message,
        actor_kind: h.role === "agent" ? "agent" : "system",
        actor_id: h.role === "agent" ? task.principal.id : null,
        actor_name: h.role === "agent" ? task.principal.name : "A2A Gateway",
        at: h.at,
        request_id: null,
        details: h.data ?? {},
      });
    }
  }

  const relatedAudits = db.audit
    .filter((a) => a.company_id === principal.company_id && (a.entity_id === o.id || a.entity_id === o.quote_id))
    .slice()
    .reverse();

  for (const a of relatedAudits) {
    const rsteps = Array.isArray(a.details?.reasoning_steps) ? (a.details.reasoning_steps as ReasoningStepOut[]) : [];
    for (const rs of rsteps) {
      steps.push({
        step: seq++,
        source: "reasoning",
        stage: rs.stage,
        summary: rs.summary,
        actor_kind: rs.actor_kind,
        actor_id: a.actor?.id ?? null,
        actor_name: a.actor?.name ?? null,
        at: rs.at,
        request_id: a.request_id,
        details: rs.details ?? {},
      });
    }
  }

  for (const ev of o.events) {
    const stage = ev.type.includes("policy")
      ? "policy_evaluation"
      : ev.type.includes("approval")
        ? "approval"
        : ev.type.includes("gateway") || ev.type.includes("placed")
          ? "gateway_submission"
          : ev.type;
    steps.push({
      step: seq++,
      source: "order_event",
      stage,
      summary: ev.message,
      actor_kind: ev.actor?.kind ?? "system",
      actor_id: ev.actor?.id ?? null,
      actor_name: ev.actor?.name ?? "Order Engine",
      at: ev.at,
      request_id: ev.request_id,
      details: { event_type: ev.type },
    });
  }

  steps.sort((a, b) => Date.parse(a.at) - Date.parse(b.at) || a.step - b.step);
  steps.forEach((s, idx) => {
    s.step = idx + 1;
  });

  const chatEvent = db.audit.find(
    (a) =>
      a.company_id === principal.company_id &&
      a.entity_type === "assistant_chat" &&
      ((a.details as { order_id?: unknown }).order_id === o.id || (a.details as { quote_id?: unknown }).quote_id === o.quote_id),
  );
  const chatTrace = chatEvent
    ? {
        user_message: chatEvent.details.user_message,
        assistant_reply: chatEvent.details.assistant_reply,
        channel: chatEvent.details.channel ?? "web",
        assurance_score: chatEvent.details.assurance_score ?? o.assurance_score ?? null,
        assurance_threshold: chatEvent.details.assurance_threshold ?? o.assurance_threshold ?? 80,
        auto_executed: chatEvent.details.auto_executed ?? o.auto_executed ?? false,
        llm_mode: chatEvent.details.llm_mode ?? "fake",
        processing_status: chatEvent.details.processing_status ?? "ok",
        processing_warnings: chatEvent.details.processing_warnings ?? [],
      }
    : null;

  return {
    order_id: o.id,
    quote_id: o.quote_id,
    status: o.status,
    policy_decision: o.policy,
    fired_rules: o.policy.rules,
    steps,
    chain_verified: true,
    source_conversation_id: null,
    source_a2a_task_id: task?.id ?? null,
    chat_trace: chatTrace,
  };
}

export function buildGovernanceSummaryResponse(principal: Principal, now: number): GovernanceSummaryResponse {
  const company = db.companies.get(principal.company_id);
  if (!company) throw new MockHttpError(404, "NOT_FOUND", "Company not found");
  const agents = db.agents.filter((a) => a.company_id === principal.company_id).map((a) => toAgent(a, now));
  const auditCount = db.audit.filter((a) => a.company_id === principal.company_id).length;
  const activePromos = db.promotions.filter((p) => p.company_id === principal.company_id && p.active).length;

  return {
    company_id: company.id,
    company_name: company.name,
    policies: {
      deterministic_layer1: true,
      human_only_promotions: true,
      idempotency_enforced: true,
    },
    agent_roles: agents,
    layer1_invariants: [
      { rule_id: "R-HARD-DIET", outcome: "BLOCKED", description: "Blocks any line failing confirmed allergen or Finnish menu diet code requirements." },
      { rule_id: "R-MAX-ORDER", outcome: "BLOCKED", description: "Blocks agent orders exceeding the configured max order EUR value." },
      { rule_id: "R-NO-ORDER-PERMISSION", outcome: "BLOCKED", description: "Blocks quote-only agents from placing orders." },
      { rule_id: "R-AUTO-LIMIT", outcome: "APPROVAL_REQUIRED", description: "Requires human approval when order total exceeds agent auto-approve limit." },
      { rule_id: "R-DAILY-LIMIT", outcome: "APPROVAL_REQUIRED", description: "Requires human approval when today's cumulative spend exceeds daily limit." },
      { rule_id: "R-DELIVERY-RISK", outcome: "APPROVAL_REQUIRED", description: "Requires human approval when any line is PARTIAL, LATE, or AT_RISK." },
      { rule_id: "R-USER-DIRECT", outcome: "AUTO", description: "Signed-in human managers may place compliant orders directly." },
    ],
    assistant_tools: [
      { name: "search_products", description: "Search catalog with Finnish menu codes (L, VL, M, G, VEG, HEART)." },
      { name: "find_on_sale_products", description: "Discover live discounted (listPrice > catalogPrice) & promoted items." },
      { name: "plan_menu", description: "Build Valio recipe menu plans with diet safety & Layer 1 policy preview." },
      { name: "create_quote", description: "Lock 30-minute wholesale quote with CO2 & delivery checks." },
    ],
    mcp_tools: [
      { name: "find_on_sale_products", description: "MCP tool: list on-sale and management-promoted wholesale SKUs." },
      { name: "list_promotions", description: "MCP tool: read-only inspection of active company promotion priorities." },
      { name: "plan_menu", description: "MCP tool: generate diet-safe menu plan and quote draft lines." },
      { name: "create_quote", description: "MCP tool: request a binding quote." },
      { name: "place_order", description: "MCP tool: submit order from quote under Layer 1 policy." },
    ],
    active_promotions_count: activePromos,
    audit_chain: {
      valid: true,
      checked_events: auditCount,
      broken_event_id: null,
    },
  };
}
