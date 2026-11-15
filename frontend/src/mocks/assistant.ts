// Scripted assistant for mock mode: keyword → actions. Prices, policy and order state come from the mock engine,
// mirroring the real design ("the LLM only extracts intent and calls tools").
import type { AssistantAction, AssistantChatResponse, Principal, RecipeSanityWarning } from "../api/types";
import { defaultDeliveryDate, formatDayDate } from "../lib/dates";
import { mockNowMs } from "./clock";
import { productById } from "./data";
import { MockHttpError, audit, createOrder, createQuote, db, newId } from "./db";
import { buildDealsResponse, buildMenuPlanResponse } from "./m9";

const fmt = (n: number) => n.toLocaleString("en-US");
const eur = (n: number) => `€${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function parseQty(msg: string): number | null {
  const m = msg.match(/(\d{1,3}(?:[,\s]\d{3})+|\d+)\s*(?:l\b|litres?|liters?|st\b|pcs|kpl|units?|x\b|×|cartons?)/i);
  if (!m) return null;
  const n = Number(m[1]!.replace(/[,\s]/g, ""));
  return n > 0 ? n : null;
}

const products = (ids: string[]) => ids.map((id) => productById(id)).filter((p): p is NonNullable<typeof p> => !!p);

export const MILK_OPTIONS = ["Chilled · 100001 (+4..+8 °C)", "UHT ambient · 100002"];

export function assistantChat(principal: Principal, body: { conversation_id?: unknown; message?: unknown }, requestId: string): AssistantChatResponse {
  if (typeof body.message !== "string" || !body.message.trim()) {
    throw new MockHttpError(422, "VALIDATION_ERROR", "message is required", { field: "message" });
  }
  const now = mockNowMs();
  const threshold = db.assistantSettings.get(principal.company_id)?.order_assurance_threshold ?? 80;
  if (db.assistantDown) {
    audit(
      principal.company_id,
      principal,
      "assistant.processing_error",
      "assistant_chat",
      null,
      requestId,
      {
        user_message: body.message.slice(0, 200),
        assistant_reply: null,
        channel: "web",
        assurance_score: null,
        assurance_threshold: threshold,
        auto_executed: false,
        llm_mode: "fake",
        fallback_used: false,
        processing_status: "error",
        processing_warnings: ["llm_unavailable"],
        error_detail: "LLM_UNAVAILABLE: The assistant's language model is unavailable.",
      },
      "error",
      now,
    );
    throw new MockHttpError(503, "LLM_UNAVAILABLE", "The assistant's language model is unavailable. Catalog, cart and orders still work.");
  }
  const date = defaultDeliveryDate(now);
  let conv = typeof body.conversation_id === "string" ? db.conversations.get(body.conversation_id) : undefined;
  if (!conv || conv.principal_id !== principal.id) {
    conv = { id: newId("conv"), principal_id: principal.id, last_quote_id: null, pending_qty: null };
    db.conversations.set(conv.id, conv);
  }
  const msg = body.message;
  const m = msg.toLowerCase();
  const actions: AssistantAction[] = [];
  let reply: string;
  let assuranceScore: number | null = null;
  let autoExecuted = false;
  let createdQuoteId: string | null = null;
  let createdOrderId: string | null = null;
  const processingWarnings: string[] = [];

  const quote = (lines: { product_id: string; quantity: number }[]) => {
    const q = createQuote(principal, { requested_delivery_date: date, lines }, now);
    audit(principal.company_id, principal, "quote.create", "quote", q.id, requestId, { via: "assistant", total_excl_vat_eur: q.total_excl_vat_eur }, "ok", now);
    conv!.last_quote_id = q.id;
    createdQuoteId = q.id;
    actions.push({ type: "quote", quote: q });
    const policy =
      q.policy_preview.outcome === "AUTO"
        ? "It can be placed directly."
        : q.policy_preview.outcome === "APPROVAL_REQUIRED"
          ? `Placing it will need human approval (${q.policy_preview.rules.filter((r) => r.outcome !== "AUTO").map((r) => r.rule_id).join(", ")}).`
          : `Policy blocks it (${q.policy_preview.rules.filter((r) => r.outcome === "BLOCKED").map((r) => r.rule_id).join(", ")}).`;
    return `Quote ready for ${formatDayDate(date)}: ${eur(q.total_excl_vat_eur)} excl. VAT. ${policy}`;
  };

  if (/(over-order|overorder|sanity|lohikeitto.*120|a2a negotiation|kitchen agent)/i.test(m)) {
    const q = createQuote(principal, { requested_delivery_date: date, lines: [{ product_id: "100001", quantity: 120 }] }, now);
    const o = createOrder(principal, { quote_id: q.id, note: "60-pax Lohikeitto prep" }, requestId, now);
    assuranceScore = 40;
    createdQuoteId = q.id;
    createdOrderId = o.id;
    o.assurance_score = assuranceScore;
    o.assurance_threshold = threshold;
    o.auto_executed = false;
    const warning: RecipeSanityWarning = {
      triggered: true,
      rule_id: "R-RECIPE-SANITY",
      recipe_id: "rcp_lohikeitto_01",
      recipe_name: "Lohikeitto (Lactose-Free)",
      target_pax: 60,
      message: "Ordered 120 L milk vs ~18 L expected for 60 pax Lohikeitto (6.67x recipe yield).",
      offending_lines: [
        {
          product_id: "100001",
          product_name: productById("100001")?.name ?? "Lactose-free milk 1 L",
          ordered_qty: 120,
          unit: "L",
          expected_qty: 18,
          excess_ratio: 6.67,
          excess_cost_eur: 103.02,
        },
      ],
    };
    o.status = "PENDING_APPROVAL";
    o.recipe_sanity_warning = warning;
    o.policy = {
      outcome: "APPROVAL_REQUIRED",
      rules: [
        ...o.policy.rules.filter((r) => r.rule_id !== "R-RECIPE-SANITY"),
        {
          rule_id: "R-RECIPE-SANITY",
          outcome: "APPROVAL_REQUIRED",
          message: warning.message,
        },
      ],
    };
    actions.push({
      type: "a2a_negotiation",
      summary: "Kitchen Agent <-> Gateway Policy Negotiation (60-pax Lohikeitto)",
      target_pax: 60,
      turns: [
        { speaker: "buyer-agent", message: "Requesting 120 L lactose-free milk (100001) for 60 pax Lohikeitto.", status: "working" },
        { speaker: "supplier-agent", message: "Stock verified (unlimited chilled 100001) + recipe yield normalized: 60 pax × 0.30 L = 18 L.", status: "verified" },
        { speaker: "policy-engine", message: "R-RECIPE-SANITY triggered (6.67x expected yield, +€103.02). Escalated to human approval.", status: "PENDING_APPROVAL" },
      ],
      order: o,
    });
    actions.push({ type: "recipe_sanity_warning", warning });
    actions.push({ type: "order", order: o });
    reply = `A2A check flagged a recipe over-order on ${o.id}: 120 L ordered vs 18 L expected for 60-pax Lohikeitto (6.67x). Held in PENDING_APPROVAL for human review.`;
  } else if (/\b(place|confirm|order it|tilaa|go ahead)\b/.test(m) && conv.last_quote_id) {
    const order = createOrder(principal, { quote_id: conv.last_quote_id }, requestId, now);
    assuranceScore = 100;
    createdQuoteId = order.quote_id;
    createdOrderId = order.id;
    order.assurance_score = assuranceScore;
    order.assurance_threshold = threshold;
    order.auto_executed = false;
    actions.push({ type: "order", order });
    reply =
      order.status === "PENDING_APPROVAL"
        ? `Order ${order.id} is waiting for human approval.`
        : `Order ${order.id} sent to Valio Aimo (${order.status}).`;
    conv.last_quote_id = null;
  } else if (/\b20\s*lit(?:re|er)s?\s+of\s+(?:lactose[- ]free\s+)?milk\b/i.test(m) && !/\?/.test(m)) {
    const q = createQuote(principal, { requested_delivery_date: date, lines: [{ product_id: "100001", quantity: 20 }] }, now);
    assuranceScore = 62;
    createdQuoteId = q.id;
    if (threshold <= assuranceScore && q.policy_preview.outcome !== "BLOCKED") {
      const order = createOrder(principal, { quote_id: q.id }, requestId, now);
      autoExecuted = true;
      createdOrderId = order.id;
      order.assurance_score = assuranceScore;
      order.assurance_threshold = threshold;
      order.auto_executed = true;
      actions.push({ type: "order", order });
      reply = `Auto-ordered 20 L of chilled Organic Lactose-Free Semi-Skimmed Milk (100001) for ${formatDayDate(date)} (${eur(order.total_excl_vat_eur)} excl. VAT) — assurance ${assuranceScore}% ≥ ${threshold}%.`;
    } else {
      conv.last_quote_id = q.id;
      actions.push({ type: "quote", quote: q });
      reply = `Prepared quote for 20 L of chilled Organic Lactose-Free Semi-Skimmed Milk (100001) for ${formatDayDate(date)} (${eur(q.total_excl_vat_eur)} excl. VAT). Assurance ${assuranceScore}% < ${threshold}%, so please confirm to place the order.`;
    }
  } else if (/split/.test(m)) {
    const qty = conv.pending_qty ?? 1000;
    reply = `Split: 384 × 100003 (all stock) + ${fmt(Math.max(0, qty - 384))} × 100001. ` + quote([{ product_id: "100003", quantity: 384 }, { product_id: "100001", quantity: Math.max(1, qty - 384) }]);
  } else if (/switch/.test(m)) {
    const qty = conv.pending_qty ?? 1000;
    reply = `Switched to ${fmt(qty)} × 100001 (chilled). ` + quote([{ product_id: "100001", quantity: qty }]);
  } else if (/uht|100002|ambient/.test(m) && !/100003|catering/.test(m)) {
    const qty = conv.pending_qty ?? parseQty(msg) ?? 1000;
    reply = quote([{ product_id: "100002", quantity: qty }]);
  } else if (/chilled|100001|fresh/.test(m)) {
    const qty = conv.pending_qty ?? parseQty(msg) ?? 1000;
    reply = quote([{ product_id: "100001", quantity: qty }]);
  } else if (/100003|catering/.test(m)) {
    const qty = parseQty(msg) ?? 1000;
    conv.pending_qty = qty;
    reply = quote([{ product_id: "100003", quantity: qty }]) + " Only 384 ST are in stock.";
    actions.push({
      type: "clarification",
      question: `100003 has only 384 ST in stock for ${formatDayDate(date)}. Split the order or switch to 100001 (chilled, unlimited)?`,
      options: [`Split: 384 × 100003 + ${fmt(Math.max(0, qty - 384))} × 100001`, `Switch: ${fmt(qty)} × 100001 chilled`],
    });
  } else if (/\b(menu|lunch|dinner|breakfast|recipe)\b/.test(m)) {
    const mealType = m.includes("breakfast") ? "breakfast" : m.includes("dinner") ? "dinner" : "lunch";
    const diet = /\[l\]|lactose-free|lactose free/.test(m) ? "L" : /\[g\]|gluten-free|gluten free/.test(m) ? "G" : undefined;
    const paxMatch = m.match(/(\d+)\s*(?:pax|guests?|people|servings?)/);
    const pax = paxMatch ? Number(paxMatch[1]) : 40;
    const plan = buildMenuPlanResponse(principal, { pax, meal_type: mealType, requested_delivery_date: date, diet, prefer_on_sale: true, include_valio_recipes: true }, requestId, now);
    actions.push({ type: "menu_plan", menu_plan: plan });
    reply = `Planned a ${mealType} menu for ${plan.pax} pax (${formatDayDate(date)}) using kitchen recipes and on-sale ingredients: ${eur(plan.estimated_total_excl_vat_eur)} excl. VAT (saving ${eur(plan.estimated_savings_eur)} vs list price).`;
  } else if (/on sale|on-sale|\bdeals?\b|\bdiscount|\bpromo/.test(m)) {
    const deals = buildDealsResponse(principal, { on_sale_only: true, requested_delivery_date: date, limit: 8 }, now);
    actions.push({ type: "deals", deals });
    reply = `Found ${deals.total} on-sale and promoted products for ${formatDayDate(date)}.`;
  } else if (/\b(capabilit|what can you do|kyvyk|mitä osaat|negotiate|neuvot|tendering)/i.test(m)) {
    const negEnabled = db.negotiationSettings.get(principal.company_id)?.allow_price_negotiation ?? false;
    const companyName = db.companies.get(principal.company_id)?.name ?? "Hotel Aurora Oy";
    reply = negEnabled
      ? `For ${companyName}, I can search the catalog, plan menus, check diets & substitutes, create quotes, place policy-checked orders, and I can negotiate prices for larger quantities (managed in /admin).`
      : `For ${companyName}, I can search the catalog, plan menus, check diets & substitutes, create quotes, and place policy-checked orders. Price negotiation for larger quantities is disabled for ${companyName} (managed in /admin).`;
  } else if (/hotel prep|bread|pax/.test(m)) {
    conv.pending_qty = parseQty(msg) ?? 1000;
    actions.push({ type: "search_results", products: products(["100001", "100002", "100003", "100007"]) });
    actions.push({ type: "clarification", question: "Lactose-free (L) milk: chilled or UHT ambient? Gluten-free (G) bread: Artisan Bakehouse 100007 is the only G dark seed loaf in stock.", options: MILK_OPTIONS });
    reply = `Found lactose-free (L) milk and gluten-free (G) bread for ${formatDayDate(date)}. Which milk do you want?`;
  } else if (/tomorrow|under|cheap|budget/.test(m)) {
    actions.push({ type: "search_results", products: products(["100001", "100002", "100010", "100011", "100012"]) });
    reply = `Sunday deliveries are not available; the next delivery day is ${formatDayDate(date)}. These items are in stock and deliverable then. Ask me for a quote to see exact prices.`;
  } else if (/milk|maito|lactose|laktoos/.test(m)) {
    conv.pending_qty = parseQty(msg) ?? 1000;
    actions.push({ type: "search_results", products: products(["100001", "100002", "100003", "100004"]) });
    actions.push({ type: "clarification", question: `Lactose-free milk for ${formatDayDate(date)}: chilled or UHT ambient?`, options: MILK_OPTIONS });
    reply = `I found 4 lactose-free (L) milk products. ${fmt(conv.pending_qty)} units: which type do you want?`;
  } else {
    actions.push({
      type: "clarification",
      question: "What would you like to order?",
      options: ["1000 litres of lactose-free milk by Monday", "Monday hotel prep: 1,000 L [L] milk, [G] bread for 40 pax", "What can be delivered tomorrow under €300?"],
    });
    reply = "I can search the catalog, check prices and availability, create quotes and place orders within your policy.";
  }
  audit(principal.company_id, principal, "assistant.chat", "conversation", conv.id, requestId, { message: msg.slice(0, 200), actions: actions.map((a) => a.type) }, "ok", now);
  audit(
    principal.company_id,
    principal,
    processingWarnings.length > 0 ? "assistant.processing_error" : "assistant.chat_turn",
    "assistant_chat",
    createdOrderId ?? conv.id,
    requestId,
    {
      user_message: msg.slice(0, 500),
      assistant_reply: reply,
      channel: "web",
      branch_id: null,
      branch_name: null,
      assurance_score: assuranceScore,
      assurance_threshold: threshold,
      auto_executed: autoExecuted,
      quote_id: createdQuoteId,
      order_id: createdOrderId,
      llm_mode: "fake",
      fallback_used: false,
      processing_status: processingWarnings.length > 0 ? "degraded" : "ok",
      processing_warnings: processingWarnings,
      error_detail: null,
    },
    processingWarnings.length > 0 ? "error" : "ok",
    now,
  );
  return {
    conversation_id: conv.id,
    reply,
    actions,
    assurance_score: assuranceScore,
    assurance_threshold: threshold,
    auto_executed: autoExecuted,
    llm_mode: "fake",
    fallback_used: false,
    processing_warnings: processingWarnings,
  };
}
