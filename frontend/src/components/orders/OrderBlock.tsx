// Portal order block (DESIGN_VALIO §4.7, order_management_1280.png): 1 px petrol container, petrol header bar,
// petrol-light 5-column summary row, centred text actions, and expandable sub-header + meta grid + §4.7 line rows.
// Every figure is returned verbatim by the API (never computed in the UI).
import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Bot, Check, ChevronDown, ChevronRight, Copy, FileText, ShoppingCart, Truck, User } from "lucide-react";
import { endpoints } from "../../api/endpoints";
import type { Order, PolicyOutcome, Principal, QuoteLine } from "../../api/types";
import { useCart, type CartProductSnapshot } from "../../cart/CartContext";
import { useStrings } from "../../i18n";
import { copyToClipboard } from "../../lib/clipboard";
import { formatDateTime, formatDayDate } from "../../lib/dates";
import { formatNumber } from "../../lib/format";
import { shortId } from "../../lib/ids";
import { ActorPill } from "../ActorPill";
import { NegotiatedSavingsBadge } from "../NegotiatedSavingsBadge";
import { Tag, type TagKind } from "../Badge";
import { Button, buttonClass } from "../Button";
import { CO2Value } from "../CO2Value";
import { DietRequirementBadge } from "../cart/DietRequirementPicker";
import { DeliveryStatusBadge } from "../DeliveryStatusBadge";
import { FlagChips } from "../FlagChips";
import { Money } from "../Money";
import { ProductImage } from "../ProductImage";
import { SkuChip } from "../product/ProductBits";
import { StatusBadge } from "../StatusBadge";
import { toast } from "../Toast";
import { orderHref } from "./OrderBits";

/** Creator on a petrol background: lavender agent tag + "authorized by …", or the user's name. */
export function ActorOnDark({ actor }: { actor: Principal | null | undefined }) {
  const strings = useStrings();
  if (!actor) return <span className="text-white/80">System</span>;
  if (actor.kind === "agent") {
    return (
      <span className="inline-flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5">
        <span className="inline-flex items-center gap-1 whitespace-nowrap rounded-sm bg-lavender px-1.5 py-0.5 text-[12px] font-bold text-navy">
          <Bot className="h-3.5 w-3.5" strokeWidth={1.75} aria-label="AI agent" />
          {actor.name}
        </span>
        {actor.delegated_by && (
          <span className="text-[13px] text-white/85">
            {strings.actor.authorizedBy} {actor.delegated_by.name}
          </span>
        )}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1">
      <User className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
      {actor.name}
    </span>
  );
}

/**
 * The petrol header bar of an order block. `href` makes the whole bar a link (collapsed blocks);
 * `onToggle` makes the bar an expand/collapse button. Every figure is the API's.
 */
export function OrderBlockHeader({
  order,
  href,
  onToggle,
  expanded = false,
  leading,
  className = "",
}: {
  order: Order;
  href?: string;
  onToggle?: () => void;
  expanded?: boolean;
  leading?: ReactNode;
  className?: string;
}) {
  const f = useStrings().front;
  const chevron = onToggle ? (
    expanded ? (
      <ChevronDown className="h-5 w-5 shrink-0" strokeWidth={2} aria-hidden="true" />
    ) : (
      <ChevronRight className="h-5 w-5 shrink-0" strokeWidth={2} aria-hidden="true" />
    )
  ) : null;

  const body = (
    <>
      {leading ?? chevron}
      <Truck className="h-5 w-5 shrink-0" strokeWidth={1.5} aria-hidden="true" />
      <h4 className="tabular-nums whitespace-nowrap text-[18px] font-bold leading-[22px] md:text-[20px]">{formatDayDate(order.requested_delivery_date)}</h4>
      <StatusBadge status={order.status} onDark />
      <span className="hidden whitespace-nowrap text-[14px] md:inline">
        <span className="font-bold">{f.orderNumber}</span>{" "}
        <span className="font-mono-code text-[13px]" title={order.id}>
          {shortId(order.id)}
        </span>
      </span>
      <span className="flex min-w-0 items-center gap-1.5 text-[14px]">
        <span className="hidden font-bold 2xl:inline">{f.createdBy}</span>
        <ActorOnDark actor={order.created_by} />
      </span>
      <span className="hidden whitespace-nowrap text-[14px] 2xl:inline">{f.products(order.lines.length)}</span>
      <span className="ml-auto flex shrink-0 items-center gap-1.5 whitespace-nowrap text-[14px]">
        <NegotiatedSavingsBadge savingsEur={order.negotiated_savings_eur} />
        <Money value={order.total_excl_vat_eur} className="text-[16px] font-bold" />
        <span className="hidden sm:inline">{f.exclVat}</span>
        {href && <ChevronRight className="h-4 w-4" strokeWidth={2} aria-hidden="true" />}
      </span>
    </>
  );

  const cls = `flex w-full flex-wrap items-center gap-x-3 gap-y-2 bg-navy p-4 text-left text-white ${className}`;
  if (href) {
    return (
      <Link to={href} className={`${cls} hover:bg-navy-900`} data-testid="order-block-header">
        {body}
      </Link>
    );
  }
  if (onToggle) {
    return (
      <button type="button" onClick={onToggle} aria-expanded={expanded} className={`${cls} transition-colors hover:bg-navy-900`} data-testid="order-block-header">
        {body}
      </button>
    );
  }
  return (
    <div className={cls} data-testid="order-block-header">
      {body}
    </div>
  );
}

/** Collapsed order block (header bar only) linking to /orders/:id. */
export function CollapsedOrderBlock({ order }: { order: Order }) {
  return (
    <article className="overflow-hidden rounded border border-navy" data-testid="order-block">
      <OrderBlockHeader order={order} href={orderHref(order.id)} />
    </article>
  );
}

const POLICY_TAG: Record<PolicyOutcome, TagKind> = {
  AUTO: "auto",
  APPROVAL_REQUIRED: "approval",
  BLOCKED: "blocked",
};

/** Display API tax rates from lines verbatim (no VAT arithmetic in UI). */
export function formatVatRates(lines: Pick<QuoteLine, "tax_rate">[]): string {
  const rates = Array.from(new Set(lines.map((l) => l.tax_rate).filter((r): r is number => r != null)));
  if (rates.length === 0) return "—";
  return rates.map((r) => `${formatNumber(r)} %`).join(" · ");
}

/** §4.7 line row shared by expanded OrderBlock and ApprovalCard. */
export function OrderBlockLineRow({
  line: l,
  highlight = false,
  note,
  baseHint,
  compact = false,
}: {
  line: QuoteLine;
  highlight?: boolean;
  note?: ReactNode;
  baseHint?: string | null;
  compact?: boolean;
}) {
  const strings = useStrings();
  const t = strings.orders;
  return (
    <li
      className={`px-4 py-4 ${highlight ? "border-l-4 border-l-warning bg-yellow-soft" : "bg-white"}`}
      data-testid="order-line"
      data-changed={highlight ? "true" : undefined}
    >
      <div className={`flex flex-col gap-3 ${compact ? "" : "lg:flex-row lg:items-center lg:gap-4"}`}>
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <ProductImage src={null} alt="" className="h-12 w-12 shrink-0 rounded border border-line [&_svg]:h-7 [&_svg]:w-7" />
          <div className="min-w-0 flex-1">
            <Link to={`/products/${encodeURIComponent(l.product_id)}`} className="line-clamp-2 text-[14px] font-bold leading-5 text-navy hover:underline">
              {l.product_name}
            </Link>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <SkuChip id={l.product_id} />
              <FlagChips flags={l.flags} co2Basis={l.co2_basis} />
            </div>
            {note && <div className="mt-1.5 text-[12px] font-medium text-warning">{note}</div>}
          </div>
        </div>

        <div
          className={`grid grid-cols-2 gap-x-4 gap-y-2 text-[14px] sm:grid-cols-4 ${
            compact ? "" : "lg:w-[420px] xl:w-[460px] lg:shrink-0 lg:items-center"
          }`}
        >
          <div className="tabular-nums text-ink-secondary">
            <span className="block text-[11px] font-bold uppercase text-ink-muted lg:hidden">{t.colUnit}</span>
            {l.unit_price_eur == null ? (
              <span className="text-xs font-bold text-danger">{strings.quote.noPrice}</span>
            ) : (
              <>
                <Money value={l.unit_price_eur} /> <span className="text-ink-muted">/ {l.sales_unit}</span>
              </>
            )}
          </div>
          <div className="tabular-nums text-ink">
            <span className="block text-[11px] font-bold uppercase text-ink-muted lg:hidden">{t.colQty}</span>
            <span className="font-bold">
              {formatNumber(l.quantity)} {l.sales_unit}
            </span>
            {baseHint && <span className="block text-[12px] text-ink-muted">{baseHint}</span>}
          </div>
          <div className="tabular-nums lg:text-right">
            <span className="block text-[11px] font-bold uppercase text-ink-muted lg:hidden">{t.colLine}</span>
            <Money value={l.line_total_eur} className="font-bold text-ink" />
          </div>
          <div className="min-w-0">
            <DeliveryStatusBadge status={l.delivery_status} probability={l.on_time_probability} />
            {l.available_date && (
              <div className="tabular-nums mt-0.5 text-[12px] text-ink-muted">
                {strings.quote.available} {formatDayDate(l.available_date)}
              </div>
            )}
            <div className="mt-0.5">
              <CO2Value kg={l.co2_kg} basis={l.co2_basis} className="text-[12px]" />
            </div>
          </div>
        </div>
      </div>
    </li>
  );
}

/**
 * Full expandable order block (DESIGN_VALIO §4.7):
 * header bar → 5-column summary row → centred actions row → (when expanded) policy banner slot, sub-header, meta grid & line rows.
 */
const IN_FLIGHT = new Set<Order["status"]>(["PENDING_APPROVAL", "SUBMITTING", "SUBMISSION_UNKNOWN"]);

export function OrderBlock({
  order,
  defaultExpanded = false,
  expanded: controlledExpanded,
  onToggle,
  collapsible = true,
  showOpenLink = true,
  showReorder = true,
  compact = false,
  policyBanner,
  highlightIds,
  previousNoteFor,
}: {
  order: Order;
  defaultExpanded?: boolean;
  expanded?: boolean;
  onToggle?: () => void;
  collapsible?: boolean;
  showOpenLink?: boolean;
  showReorder?: boolean;
  compact?: boolean;
  policyBanner?: ReactNode;
  highlightIds?: Set<string>;
  previousNoteFor?: (productId: string) => ReactNode;
}) {
  const strings = useStrings();
  const f = strings.front;
  const t = strings.orders;
  const [uncontrolled, setUncontrolled] = useState(defaultExpanded);
  const expanded = controlledExpanded ?? uncontrolled;
  const toggle = onToggle ?? (collapsible ? () => setUncontrolled((v) => !v) : undefined);

  const [copiedReq, setCopiedReq] = useState(false);
  const [reordering, setReordering] = useState(false);
  const cart = useCart();
  const navigate = useNavigate();

  // Copying an order that is still open (pending approval) or in flight (SUBMITTING / SUBMISSION_UNKNOWN) into the cart
  // would be a "retry" in disguise and could duplicate it (DESIGN §6F): only settled orders can be reordered.
  const canReorder = showReorder && order.lines.length > 0 && !IN_FLIGHT.has(order.status);

  const requestId = order.events.at(-1)?.request_id ?? order.events[0]?.request_id ?? null;

  const copyReqId = async () => {
    if (!requestId) return;
    await copyToClipboard(requestId);
    setCopiedReq(true);
    window.setTimeout(() => setCopiedReq(false), 1500);
  };

  const handleReorder = async () => {
    if (reordering || order.lines.length === 0) return;
    setReordering(true);
    try {
      const resolved = await Promise.all(
        order.lines.map(async (l) => {
          try {
            const p = await endpoints.getProduct(l.product_id);
            return { product: p as CartProductSnapshot, quantity: l.quantity };
          } catch {
            return {
              product: {
                id: l.product_id,
                name: l.product_name,
                sales_unit: l.sales_unit,
                base_unit: l.sales_unit,
                units: [],
                temperature_condition: null,
                image_url: null,
                brand: null,
                labels: [],
                allergens: [],
                non_allergens: [],
              } satisfies CartProductSnapshot,
              quantity: l.quantity,
            };
          }
        }),
      );
      for (const item of resolved) cart.addLine(item.product, item.quantity);
      toast.success(t.reordered(resolved.length));
      navigate("/cart");
    } finally {
      setReordering(false);
    }
  };

  const approvalSummary =
    order.decided_by || order.decided_at || order.decision_note ? (
      <div className="space-y-0.5 text-[14px]">
        {order.decided_by && <ActorPill actor={order.decided_by} stacked />}
        {order.decided_at && <div className="tabular-nums text-[12px] text-ink-muted">{formatDateTime(order.decided_at)}</div>}
        {order.decision_note && <div className="text-[13px] text-ink-secondary">“{order.decision_note}”</div>}
      </div>
    ) : order.policy.outcome === "AUTO" ? (
      <span className="text-[14px] text-ink-secondary">{t.metaAutoApproved}</span>
    ) : order.status === "PENDING_APPROVAL" ? (
      <span className="text-[14px] text-ink-secondary">{t.metaPendingApproval}</span>
    ) : (
      <span className="text-[14px] text-ink-muted">—</span>
    );

  return (
    <article className="overflow-hidden rounded border border-navy bg-white" data-testid="order-block" data-order-id={order.id}>
      <OrderBlockHeader order={order} onToggle={toggle} expanded={expanded} />

      <dl
        className={`grid grid-cols-2 gap-4 border-b border-line bg-primary-soft p-4 sm:grid-cols-3 ${compact ? "" : "lg:grid-cols-5"}`}
        data-testid="order-summary-row"
      >
        <div className="min-w-0">
          <dt className="text-[14px] font-bold leading-[17px] text-ink">{t.summaryTotalExcl}</dt>
          <dd className="mt-1 text-[14px] leading-[17px] text-ink">
            <Money value={order.total_excl_vat_eur} className="font-bold text-ink" />
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-[14px] font-bold leading-[17px] text-ink">{t.summaryVat}</dt>
          <dd className="tabular-nums mt-1 text-[14px] leading-[17px] text-ink">{formatVatRates(order.lines)}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-[14px] font-bold leading-[17px] text-ink">{t.summaryTotalIncl}</dt>
          <dd className="mt-1 text-[14px] leading-[17px] text-ink">
            <Money value={order.total_incl_vat_eur} />
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-[14px] font-bold leading-[17px] text-ink">{t.summaryCo2}</dt>
          <dd className="mt-1">
            <CO2Value kg={order.total_co2_kg} basis={strings.quote.co2TotalBasis} className="text-[14px]" />
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-[14px] font-bold leading-[17px] text-ink">{t.summaryPolicy}</dt>
          <dd className="mt-1">
            <Tag kind={POLICY_TAG[order.policy.outcome] ?? "neutral"}>{strings.policy[order.policy.outcome]?.short ?? order.policy.outcome}</Tag>
          </dd>
        </div>
      </dl>

      {(showOpenLink || requestId || canReorder) && (
        <div className={`flex flex-wrap items-center justify-center gap-x-6 gap-y-2 bg-primary-soft px-4 py-2.5 ${expanded ? "border-b border-line" : ""}`}>
          {showOpenLink && (
            <Link to={orderHref(order.id)} className={buttonClass("text", "md")}>
              <FileText className="h-4 w-4" strokeWidth={1.75} />
              {t.openOrder}
            </Link>
          )}
          {requestId && (
            <Button
              variant="text"
              size="md"
              onClick={() => void copyReqId()}
              icon={copiedReq ? <Check className="h-4 w-4 text-success" strokeWidth={2} /> : <Copy className="h-4 w-4" strokeWidth={1.75} />}
            >
              {copiedReq ? t.copiedRequestId : t.copyRequestId}
            </Button>
          )}
          {canReorder && (
            <Button
              variant="text"
              size="md"
              loading={reordering}
              onClick={() => void handleReorder()}
              icon={<ShoppingCart className="h-4 w-4" strokeWidth={1.75} />}
            >
              {t.reorder}
            </Button>
          )}
        </div>
      )}

      {expanded && (
        <div data-testid="order-block-details">
          {policyBanner && <div className="border-b border-line bg-white p-4">{policyBanner}</div>}

          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-primary-soft px-4 py-3">
            <h5 className="min-w-0 max-w-full text-[18px] font-bold leading-[20px] text-ink">
              Order <span className="font-mono-code break-all">{order.id}</span>
            </h5>
            <div className="flex flex-wrap items-center gap-4 text-[14px]">
              <StatusBadge status={order.status} />
              <span className="tabular-nums text-ink-secondary">{f.products(order.lines.length)}</span>
              <Money value={order.total_excl_vat_eur} vat="excl" className="font-bold text-ink" />
            </div>
          </div>

          <dl className={`grid grid-cols-1 gap-4 border-b border-line bg-white p-4 sm:grid-cols-2 ${compact ? "" : "lg:grid-cols-4"}`}>
            <div>
              <dt className="text-[12px] font-bold uppercase tracking-[0.04em] text-ink-muted">{t.metaOrdered}</dt>
              <dd className="tabular-nums mt-1 text-[14px] text-ink">{formatDateTime(order.created_at)}</dd>
            </div>
            <div>
              <dt className="text-[12px] font-bold uppercase tracking-[0.04em] text-ink-muted">{t.metaUser}</dt>
              <dd className="mt-1">
                <ActorPill actor={order.created_by} stacked />
              </dd>
            </div>
            <div>
              <dt className="text-[12px] font-bold uppercase tracking-[0.04em] text-ink-muted">{t.metaDiet}</dt>
              <dd className="mt-1">{order.diet ? <DietRequirementBadge diet={order.diet} /> : <span className="text-[14px] text-ink-muted">—</span>}</dd>
            </div>
            <div>
              <dt className="text-[12px] font-bold uppercase tracking-[0.04em] text-ink-muted">{t.metaApproval}</dt>
              <dd className="mt-1">{approvalSummary}</dd>
            </div>
          </dl>

          <ul className="divide-y divide-line bg-white" data-testid="order-lines">
            {order.lines.map((l, i) => {
              const hl = highlightIds?.has(l.product_id) ?? false;
              return <OrderBlockLineRow key={`${l.product_id}-${i}`} line={l} compact={compact} highlight={hl} note={hl ? previousNoteFor?.(l.product_id) : undefined} />;
            })}
          </ul>
        </div>
      )}
    </article>
  );
}
