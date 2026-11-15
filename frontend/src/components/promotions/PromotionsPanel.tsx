// Management Promotions panel (M11): human managers prioritize brands/categories/on-sale items; AI agents are read-only.
import { useState, type FormEvent } from "react";
import { Plus, ShieldCheck, Trash2, X } from "lucide-react";
import { useCreatePromotion, useDeletePromotion, usePromotions, useUpdatePromotion } from "../../api/hooks";
import type { PromotionOut } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { useStrings } from "../../i18n";
import { Badge, Tag } from "../Badge";
import { Button } from "../Button";
import { EmptyState } from "../EmptyState";
import { ErrorPanel } from "../ErrorPanel";
import { Field, TextArea, TextInput, Toggle } from "../FormField";
import { Skeleton } from "../Skeleton";
import { Stripe } from "../Stripe";
import { toast } from "../Toast";

export function PromotionsPanel() {
  const strings = useStrings();
  const t = strings.promotions;
  const { isUser } = useAuth();
  const promos = usePromotions({ silent: true });
  const createPromo = useCreatePromotion();
  const updatePromo = useUpdatePromotion();
  const deletePromo = useDeletePromotion();

  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [brandFilter, setBrandFilter] = useState("Valio");
  const [categoryPrefix, setCategoryPrefix] = useState("");
  const [boostWeight, setBoostWeight] = useState("1.5");
  const [preferOnSale, setPreferOnSale] = useState(true);
  const [active, setActive] = useState(true);
  const [titleErr, setTitleErr] = useState<string | null>(null);
  const [boostErr, setBoostErr] = useState<string | null>(null);
  const [criterionErr, setCriterionErr] = useState<string | null>(null);

  const [busyId, setBusyId] = useState<string | null>(null);

  const items = promos.data?.items ?? [];

  function resetForm() {
    setTitle("");
    setDescription("");
    setBrandFilter("Valio");
    setCategoryPrefix("");
    setBoostWeight("1.5");
    setPreferOnSale(true);
    setActive(true);
    setTitleErr(null);
    setBoostErr(null);
    setCriterionErr(null);
  }

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    const trimmedTitle = title.trim();
    const parsedBoost = Number(boostWeight);
    const hasCriterion = Boolean(brandFilter.trim() || categoryPrefix.trim() || preferOnSale);
    let valid = true;
    if (!trimmedTitle) {
      setTitleErr(t.errTitleRequired);
      valid = false;
    } else {
      setTitleErr(null);
    }
    if (!Number.isFinite(parsedBoost) || parsedBoost <= 0) {
      setBoostErr(t.errBoostPositive);
      valid = false;
    } else {
      setBoostErr(null);
    }
    if (!hasCriterion) {
      setCriterionErr(t.errCriterionRequired);
      valid = false;
    } else {
      setCriterionErr(null);
    }
    if (!valid) return;

    try {
      await createPromo.mutateAsync({
        title: trimmedTitle,
        description: description.trim() || null,
        brand_filter: brandFilter.trim() || null,
        category_prefix: categoryPrefix.trim() || null,
        boost_weight: parsedBoost,
        prefer_on_sale: preferOnSale,
        active,
      });
      toast.success(t.created);
      resetForm();
      setShowForm(false);
    } catch {
      // Mutation hook shows error toast with code + request_id.
    }
  }

  async function handleToggleActive(item: PromotionOut) {
    setBusyId(item.id);
    try {
      await updatePromo.mutateAsync({ id: item.id, patch: { active: !item.active } });
      toast.success(t.updated);
    } catch {
      // Mutation hook shows error toast.
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(item: PromotionOut) {
    setBusyId(item.id);
    try {
      await deletePromo.mutateAsync(item.id);
      toast.success(t.deleted);
    } catch {
      // Mutation hook shows error toast.
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section
      id="promotions"
      data-testid="promotions-panel"
      aria-labelledby="promotions-heading"
      className="border-t border-line pt-8"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 id="promotions-heading" className="text-[18px] font-bold leading-[24px] text-ink">
              {t.title}
            </h2>
            <Badge tone="navy" icon={<ShieldCheck className="h-3.5 w-3.5" strokeWidth={2} />}>
              {t.badge}
            </Badge>
          </div>
          <p className="mt-1 text-[14px] text-ink-secondary">{t.subtitle}</p>
        </div>
        {isUser && (
          <Button
            type="button"
            variant={showForm ? "outline" : "lime"}
            size="sm"
            onClick={() => setShowForm((v) => !v)}
          >
            {showForm ? (
              <>
                <X className="h-4 w-4" strokeWidth={1.75} />
                {strings.common.cancel}
              </>
            ) : (
              <>
                <Plus className="h-4 w-4" strokeWidth={1.75} />
                {t.newRule}
              </>
            )}
          </Button>
        )}
      </div>

      {!isUser && (
        <div className="mt-4">
          <Stripe kind="neutral">{t.readOnlyAgent}</Stripe>
        </div>
      )}

      {isUser && showForm && (
        <form
          onSubmit={(e) => void handleCreate(e)}
          data-testid="promotion-create-form"
          className="mt-4 space-y-4 rounded-[4px] border border-line bg-primary-soft p-4"
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t.formTitle} error={titleErr}>
              {(f) => (
                <TextInput
                  {...f}
                  value={title}
                  onChange={(e) => {
                    setTitle(e.target.value);
                    if (titleErr) setTitleErr(null);
                  }}
                  placeholder={t.formTitlePlaceholder}
                />
              )}
            </Field>
            <Field label={t.formBoost} error={boostErr}>
              {(f) => (
                <TextInput
                  {...f}
                  type="number"
                  step="0.1"
                  min="0.1"
                  className="tabular-nums"
                  value={boostWeight}
                  onChange={(e) => {
                    setBoostWeight(e.target.value);
                    if (boostErr) setBoostErr(null);
                  }}
                />
              )}
            </Field>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t.formBrand} optional>
              {(f) => (
                <TextInput
                  {...f}
                  value={brandFilter}
                  onChange={(e) => {
                    setBrandFilter(e.target.value);
                    if (criterionErr) setCriterionErr(null);
                  }}
                  placeholder={t.formBrandPlaceholder}
                />
              )}
            </Field>
            <Field label={t.formCategory} optional>
              {(f) => (
                <TextInput
                  {...f}
                  value={categoryPrefix}
                  onChange={(e) => {
                    setCategoryPrefix(e.target.value);
                    if (criterionErr) setCriterionErr(null);
                  }}
                  placeholder={t.formCategoryPlaceholder}
                />
              )}
            </Field>
          </div>

          <Field label={t.formDesc} optional>
            {(f) => (
              <TextArea
                {...f}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t.formDescPlaceholder}
                rows={2}
              />
            )}
          </Field>

          <div className="grid gap-3 sm:grid-cols-2">
            <Toggle
              checked={preferOnSale}
              onChange={(next) => {
                setPreferOnSale(next);
                if (criterionErr) setCriterionErr(null);
              }}
              label={t.formPreferOnSale}
            />
            <Toggle checked={active} onChange={setActive} label={t.formActive} />
          </div>

          {criterionErr && (
            <p className="text-xs font-medium text-danger" role="alert" data-testid="promotion-criterion-error">
              {criterionErr}
            </p>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                resetForm();
                setShowForm(false);
              }}
            >
              {strings.common.cancel}
            </Button>
            <Button type="submit" variant="primary" size="sm" loading={createPromo.isPending}>
              {t.createCta}
            </Button>
          </div>
        </form>
      )}

      <div className="mt-4">
        {promos.isError ? (
          <ErrorPanel error={promos.error} onRetry={() => void promos.refetch()} />
        ) : promos.isLoading ? (
          <div className="space-y-2" aria-busy="true" aria-label={strings.common.loading}>
            <Skeleton className="h-[68px] w-full" />
            <Skeleton className="h-[68px] w-full" />
          </div>
        ) : items.length === 0 ? (
          <EmptyState title={t.emptyTitle} description={t.emptyBody} />
        ) : (
          <ul className="divide-y divide-line rounded-[4px] border border-line bg-white" data-testid="promotions-list">
            {items.map((item) => {
              const rowBusy = busyId === item.id;
              return (
                <li
                  key={item.id}
                  data-testid={`promotion-row-${item.id}`}
                  className="flex flex-wrap items-start justify-between gap-3 px-4 py-3.5"
                >
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[15px] font-bold text-ink">{item.title}</span>
                      <Badge tone={item.active ? "success" : "neutral"}>
                        {item.active ? t.active : t.inactive}
                      </Badge>
                      {item.prefer_on_sale && <Tag kind="offer">{t.colOnSale}</Tag>}
                      <span className="rounded bg-surface-subtle px-1.5 py-0.5 font-mono-code text-[11px] text-ink-secondary tabular-nums">
                        {t.colBoost} ×{item.boost_weight.toFixed(1)}
                      </span>
                    </div>
                    {item.description && (
                      <p className="text-[13px] text-ink-secondary">{item.description}</p>
                    )}
                    <div className="flex flex-wrap items-center gap-3 text-[12px] text-ink-muted">
                      {item.brand_filter && (
                        <span>
                          {t.colBrand}: <strong className="text-ink-secondary">{item.brand_filter}</strong>
                        </span>
                      )}
                      {item.category_prefix && (
                        <span>
                          {t.colCategory}: <strong className="text-ink-secondary">{item.category_prefix}</strong>
                        </span>
                      )}
                    </div>
                  </div>
                  {isUser && (
                    <div className="flex shrink-0 items-center gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={rowBusy}
                        onClick={() => void handleToggleActive(item)}
                      >
                        {item.active ? t.pause : t.activate}
                      </Button>
                      <Button
                        type="button"
                        variant="danger-text"
                        size="sm"
                        disabled={rowBusy}
                        aria-label={`${t.delete} ${item.title}`}
                        onClick={() => void handleDelete(item)}
                      >
                        <Trash2 className="h-4 w-4" strokeWidth={1.75} />
                        <span className="sr-only">{t.delete}</span>
                      </Button>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
