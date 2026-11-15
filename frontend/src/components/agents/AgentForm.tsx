import { formatNumber } from "../../lib/format";
// Agent create / limit-edit form. Client-side checks are input validation only (required, positive, auto ≤ max);
// the server remains the authority and its VALIDATION_ERROR is shown inline.
import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import { Search, X } from "lucide-react";
import { toApiError } from "../../api/client";
import { useCategories } from "../../api/hooks";
import type { Agent, AgentCreateRequest } from "../../api/types";
import { helsinkiDate, helsinkiEndOfDayIso } from "../../lib/dates";
import { getStrings, useStrings } from "../../i18n";
import { Button } from "../Button";
import { ErrorPanel } from "../ErrorPanel";
import { Field, TextArea, TextInput, Toggle } from "../FormField";
import { Skeleton } from "../Skeleton";

export type AgentFormValues = {
  name: string;
  description: string;
  auto: string;
  max: string;
  daily: string;
  maxQty: string;
  categories: string[];
  canOrder: boolean;
  /** YYYY-MM-DD (Helsinki) or "" */
  expires: string;
};
export type AgentFormErrors = Partial<Record<keyof AgentFormValues, string>>;

export const DEFAULT_AGENT_FORM: AgentFormValues = {
  name: "",
  description: "",
  auto: "500",
  max: "5000",
  daily: "3000",
  maxQty: "",
  categories: [],
  canOrder: true,
  expires: "",
};

export function agentToForm(a: Agent): AgentFormValues {
  return {
    name: a.name,
    description: a.description ?? "",
    auto: String(a.auto_approve_limit_eur),
    max: String(a.max_order_value_eur),
    daily: String(a.daily_spend_limit_eur),
    maxQty: a.max_quantity_per_line == null ? "" : String(a.max_quantity_per_line),
    categories: [...a.allowed_categories],
    canOrder: a.can_place_orders,
    expires: a.expires_at ? helsinkiDate(new Date(a.expires_at)) : "",
  };
}

const parseNum = (s: string): number | null => {
  const v = s.trim().replace(",", ".");
  if (!v) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : NaN;
};

/** Input validation. `today` = server-side Helsinki date (YYYY-MM-DD), used for the expiry check. */
export function validateAgentForm(v: AgentFormValues, today: string, opts: { requireFutureExpiry?: boolean } = {}): AgentFormErrors {
  const strings = getStrings();
  const t = strings.agents;
  const e: AgentFormErrors = {};
  if (!v.name.trim()) e.name = strings.auth.errRequired(t.name);
  const auto = parseNum(v.auto);
  const max = parseNum(v.max);
  const daily = parseNum(v.daily);
  if (auto == null || Number.isNaN(auto) || auto < 0) e.auto = t.errNonNegative;
  if (max == null || Number.isNaN(max) || max <= 0) e.max = t.errPositive;
  if (daily == null || Number.isNaN(daily) || daily < 0) e.daily = t.errNonNegative;
  if (!e.auto && !e.max && auto! > max!) e.auto = t.errAutoAboveMax;
  if (v.maxQty.trim()) {
    const q = parseNum(v.maxQty);
    if (q == null || Number.isNaN(q) || q <= 0 || !Number.isInteger(q)) e.maxQty = t.errInteger;
  }
  if (v.expires && (opts.requireFutureExpiry ?? true) && v.expires <= today) e.expires = t.errFuture;
  return e;
}

/** Form → full create body. */
export function formToRequest(v: AgentFormValues): AgentCreateRequest {
  return {
    name: v.name.trim(),
    description: v.description.trim() || null,
    auto_approve_limit_eur: parseNum(v.auto)!,
    max_order_value_eur: parseNum(v.max)!,
    daily_spend_limit_eur: parseNum(v.daily)!,
    allowed_categories: v.categories,
    max_quantity_per_line: v.maxQty.trim() ? parseNum(v.maxQty) : null,
    can_place_orders: v.canOrder,
    expires_at: v.expires ? helsinkiEndOfDayIso(v.expires) : null,
  };
}

/** Only the fields that differ from the agent (for PATCH; keeps the audit diff small). */
export function formToPatch(v: AgentFormValues, a: Agent): Partial<AgentCreateRequest> {
  const full = formToRequest(v);
  const initial = formToRequest(agentToForm(a));
  const patch: Partial<AgentCreateRequest> = {};
  for (const k of Object.keys(full) as (keyof AgentCreateRequest)[]) {
    if (JSON.stringify(full[k]) !== JSON.stringify(initial[k])) (patch as Record<string, unknown>)[k] = full[k];
  }
  return patch;
}

const API_FIELD: Record<string, keyof AgentFormValues> = {
  name: "name",
  description: "description",
  auto_approve_limit_eur: "auto",
  max_order_value_eur: "max",
  daily_spend_limit_eur: "daily",
  max_quantity_per_line: "maxQty",
  allowed_categories: "categories",
  expires_at: "expires",
};

/**
 * The form itself. `mode="create"` shows every field; `mode="edit"` is the limit editor.
 * `onSubmit` must throw on API failure (ApiError is rendered inline, field-mapped when possible).
 */
export function AgentForm({
  initial,
  mode,
  today,
  authorizedBy,
  submitLabel,
  onSubmit,
  onCancel,
  footerExtra,
  formId,
  hideActions,
}: {
  initial: AgentFormValues;
  mode: "create" | "edit";
  today: string;
  authorizedBy?: string | null;
  submitLabel: string;
  onSubmit: (v: AgentFormValues) => Promise<void>;
  onCancel?: () => void;
  footerExtra?: ReactNode;
  formId?: string;
  hideActions?: boolean;
}) {
  const strings = useStrings();
  const t = strings.agents;
  const [v, setV] = useState<AgentFormValues>(initial);
  const [errors, setErrors] = useState<AgentFormErrors>({});
  const [apiError, setApiError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const dirty = JSON.stringify(v) !== JSON.stringify(initial);

  const set = <K extends keyof AgentFormValues>(k: K, val: AgentFormValues[K]) => {
    setV((x) => ({ ...x, [k]: val }));
    if (errors[k]) setErrors((x) => ({ ...x, [k]: undefined }));
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setApiError(null);
    // Editing an agent that already expired must not force a new date just to save other limits.
    const errs = validateAgentForm(v, today, { requireFutureExpiry: mode === "create" || v.expires !== initial.expires });
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      await onSubmit(v);
    } catch (err) {
      setApiError(err);
      const ae = toApiError(err);
      const f = typeof ae.details.field === "string" ? API_FIELD[ae.details.field] : undefined;
      if (ae.code === "VALIDATION_ERROR" && f) setErrors({ [f]: typeof ae.details.field_message === "string" ? ae.details.field_message : ae.message });
    } finally {
      setBusy(false);
    }
  };

  const money = (k: "auto" | "max" | "daily", label: string, hint: string) => (
    <Field label={label} hint={hint} error={errors[k]}>
      {(p) => <TextInput {...p} prefix="€" inputMode="decimal" className="tabular-nums" value={v[k]} onChange={(e) => set(k, e.target.value)} />}
    </Field>
  );

  return (
    <form id={formId} onSubmit={submit} noValidate className="space-y-4">
      {mode === "create" && (
        <>
          <Field label={t.name} error={errors.name}>
            {(p) => <TextInput {...p} autoFocus placeholder="e.g. Kitchen Reorder Bot" value={v.name} onChange={(e) => set("name", e.target.value)} />}
          </Field>
          <Field label={t.description} optional hint={t.descriptionHint} error={errors.description}>
            {(p) => <TextArea {...p} rows={2} value={v.description} onChange={(e) => set("description", e.target.value)} />}
          </Field>
        </>
      )}
      <div className="grid gap-4 sm:grid-cols-3">
        {money("auto", t.autoLimit, t.autoLimitHint)}
        {money("max", t.maxOrder, t.maxOrderHint)}
        {money("daily", t.dailyLimit, t.dailyLimitHint)}
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t.maxQty} optional hint={t.maxQtyHint} error={errors.maxQty}>
          {(p) => <TextInput {...p} inputMode="numeric" className="tabular-nums" placeholder="No limit" value={v.maxQty} onChange={(e) => set("maxQty", e.target.value)} />}
        </Field>
        <Field label={t.expires} optional hint={t.expiresHint} error={errors.expires}>
          {(p) => <TextInput {...p} type="date" min={today} value={v.expires} onChange={(e) => set("expires", e.target.value)} />}
        </Field>
      </div>
      <CategoryMultiSelect value={v.categories} onChange={(c) => set("categories", c)} />
      <div className="rounded-md border border-line bg-surface-muted p-3">
        <Toggle checked={v.canOrder} onChange={(c) => set("canOrder", c)} label={t.canOrder} description={t.canOrderHint} />
      </div>
      {authorizedBy && (
        <p className="text-xs text-ink-secondary">
          {t.authorizedBy} <span className="font-semibold text-ink">{authorizedBy}</span> · the agent acts as itself, delegated by you, and every action is audited.
        </p>
      )}
      {apiError != null && <ErrorPanel error={apiError} />}
      {!hideActions && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" variant="primary" loading={busy} disabled={mode === "edit" && !dirty}>
              {submitLabel}
            </Button>
            {onCancel && (
              <Button variant="outline" onClick={onCancel} disabled={busy}>
                {strings.common.cancel}
              </Button>
            )}
          </div>
          {footerExtra}
        </div>
      )}
    </form>
  );
}

/** Multi-select of `/api/categories` (empty = all). Unknown codes (e.g. prefixes like "104") stay selectable as chips. */
export function CategoryMultiSelect({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const t = useStrings().agents;
  const cats = useCategories({ silent: true });
  const [filter, setFilter] = useState("");
  const items = cats.data?.items ?? [];
  const names = useMemo(() => new Map(items.map((c) => [c.code, c.name])), [items]);
  const shown = items.filter((c) => {
    const f = filter.trim().toLowerCase();
    return !f || c.code.includes(f) || c.name.toLowerCase().includes(f);
  });
  const toggle = (code: string) => onChange(value.includes(code) ? value.filter((c) => c !== code) : [...value, code]);

  return (
    <fieldset className="min-w-0">
      <legend className="mb-1 flex w-full items-baseline justify-between gap-2 text-xs font-semibold text-ink-secondary">
        <span>{t.categories}</span>
        <span className="font-normal text-ink-muted">{value.length ? t.categoriesCount(value.length) : `${t.allCategories} categories`}</span>
      </legend>
      <p className="mb-2 text-xs text-ink-muted">{t.categoriesHint}</p>
      {value.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {value.map((code) => (
            <span key={code} className="inline-flex items-center gap-1 rounded-md border border-[#DEE4E6] bg-primary-soft py-0.5 pl-2 pr-1 text-xs font-medium text-navy">
              <span className="font-mono-code text-[11px]">{names.has(code) ? code : `${code}*`}</span>
              <span className="max-w-[160px] truncate">{names.get(code) ?? "prefix"}</span>
              <button type="button" onClick={() => toggle(code)} className="rounded p-0.5 hover:bg-white" aria-label={`Remove category ${code}`}>
                <X className="h-3 w-3" strokeWidth={2} />
              </button>
            </span>
          ))}
          <button type="button" onClick={() => onChange([])} className="text-xs font-semibold text-primary hover:underline">
            Allow all
          </button>
        </div>
      )}
      <div className="rounded-md border border-line-strong">
        <div className="relative border-b border-line">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-muted" strokeWidth={1.75} />
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={t.categoriesSearch}
            aria-label={t.categoriesSearch}
            className="h-8 w-full rounded-t-md bg-white pl-8 pr-2 text-xs focus:outline-none"
          />
        </div>
        <div className="max-h-40 overflow-y-auto py-1">
          {cats.isLoading ? (
            <div className="space-y-1.5 px-2 py-1">
              <Skeleton className="h-5 w-full" />
              <Skeleton className="h-5 w-full" />
              <Skeleton className="h-5 w-2/3" />
            </div>
          ) : cats.isError ? (
            <p className="px-3 py-2 text-xs text-danger">Could not load categories ({toApiError(cats.error).code}).</p>
          ) : shown.length === 0 ? (
            <p className="px-3 py-2 text-xs text-ink-muted">No categories match.</p>
          ) : (
            shown.map((c) => (
              <label key={c.code} className="flex cursor-pointer items-center gap-2 px-3 py-1 text-sm hover:bg-surface-muted">
                <input type="checkbox" className="h-3.5 w-3.5 accent-[#005670]" checked={value.includes(c.code)} onChange={() => toggle(c.code)} />
                <span className="font-mono-code text-[11px] text-ink-muted">{c.code}</span>
                <span className="min-w-0 flex-1 truncate">{c.name}</span>
                <span className="tabular-nums text-xs text-ink-muted">{formatNumber(c.product_count)}</span>
              </label>
            ))
          )}
        </div>
      </div>
    </fieldset>
  );
}
