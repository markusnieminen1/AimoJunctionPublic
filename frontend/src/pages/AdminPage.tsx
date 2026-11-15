import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Pencil, Plus, ShieldAlert, SlidersHorizontal, Trash2 } from "lucide-react";
import {
  useAimoCampaigns,
  useCategories,
  useCreateAimoCampaign,
  useDeleteAimoCampaign,
  useUpdateAimoCampaign,
} from "../api/hooks";
import type { AimoCampaignOut } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Button } from "../components/Button";
import { Card, CardHeader } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { ErrorPanel } from "../components/ErrorPanel";
import { Field, TextInput, Toggle, inputClass } from "../components/FormField";
import { Skeleton } from "../components/Skeleton";
import { Stripe } from "../components/Stripe";
import { toast } from "../components/Toast";
import { NegotiationSettingsCard } from "../components/account/NegotiationSettingsCard";
import { useStrings } from "../i18n";

function SharpTag({ active, activeLabel, inactiveLabel }: { active: boolean; activeLabel: string; inactiveLabel: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-none px-2 py-0.5 text-[11px] font-bold uppercase tracking-[0.04em] ${
        active ? "bg-lime text-navy" : "bg-muted text-ink-secondary"
      }`}
    >
      {active ? activeLabel : inactiveLabel}
    </span>
  );
}

function parseCsvList(raw: string): string[] {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * `/admin` — Valio Aimo Internal Platform Console (Valio Aimo POV, NOT client company settings).
 * Hosts invisible supplier ranking campaigns and allows/disallows specific client organization
 * bots to negotiate preliminary volume pricing.
 */
export default function AdminPage() {
  const { isUser, company } = useAuth();
  const strings = useStrings();
  const t = strings.admin;

  return (
    <div className="mx-auto max-w-[920px] space-y-6" data-testid="aimo-internal-console">
      <section className="rounded-none border-2 border-navy bg-navy p-5 text-white md:p-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span
            data-testid="aimo-internal-eyebrow"
            className="inline-flex items-center rounded-none bg-lime px-2.5 py-1 text-[11px] font-extrabold uppercase tracking-[0.06em] text-navy"
          >
            {t.eyebrow}
          </span>
          <Link
            to="/account"
            data-testid="aimo-console-back-link"
            className="text-[13px] font-bold text-white/90 underline underline-offset-2 hover:text-lime"
          >
            {t.backToClient(company?.name)}
          </Link>
        </div>
        <h1 className="mt-3 font-display text-[30px] leading-[34px] tracking-[0.01em] text-white md:text-[38px] md:leading-[42px]">
          {t.title}
        </h1>
        <p className="mt-2 max-w-[760px] text-[14px] leading-relaxed text-white/90">
          {t.subtitle}
        </p>
      </section>

      {!isUser && (
        <Stripe kind="warning" accent data-testid="admin-readonly-notice" icon={<ShieldAlert className="h-5 w-5 text-red-strong" strokeWidth={1.75} />}>
          {t.readOnlyNotice}
        </Stripe>
      )}

      <NegotiationSettingsCard />

      <section aria-label={t.tabCampaigns} className="space-y-4">
        <PlatformCampaignsPanel isUser={isUser} />
      </section>
    </div>
  );
}

function PlatformCampaignsPanel({ isUser }: { isUser: boolean }) {
  const strings = useStrings();
  const t = strings.admin;

  const campaigns = useAimoCampaigns({ enabled: isUser, silent: true });
  const categories = useCategories({ silent: true });
  const createMut = useCreateAimoCampaign({ silent: true });
  const updateMut = useUpdateAimoCampaign({ silent: true });
  const deleteMut = useDeleteAimoCampaign({ silent: true });

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<AimoCampaignOut | null>(null);
  const [title, setTitle] = useState("");
  const [brandFilter, setBrandFilter] = useState("");
  const [categoryPrefix, setCategoryPrefix] = useState("");
  const [productIdsRaw, setProductIdsRaw] = useState("");
  const [boostWeight, setBoostWeight] = useState("1.5");
  const [active, setActive] = useState(true);

  const openCreate = () => {
    setEditing(null);
    setTitle("");
    setBrandFilter("");
    setCategoryPrefix("");
    setProductIdsRaw("");
    setBoostWeight("1.5");
    setActive(true);
    setFormOpen(true);
  };

  const openEdit = (c: AimoCampaignOut) => {
    setEditing(c);
    setTitle(c.title);
    setBrandFilter(c.brand_filter ?? "");
    setCategoryPrefix(c.category_prefix ?? "");
    setProductIdsRaw(c.product_ids.join(", "));
    setBoostWeight(String(c.boost_weight));
    setActive(c.active);
    setFormOpen(true);
  };

  const closeForm = () => {
    setFormOpen(false);
    setEditing(null);
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    const trimmedTitle = title.trim();
    if (!trimmedTitle) return;
    const parsedBoost = Number(boostWeight);
    const weight = Number.isFinite(parsedBoost) ? Math.min(10, Math.max(0.1, parsedBoost)) : 1.5;
    const payload = {
      title: trimmedTitle,
      brand_filter: brandFilter.trim() || null,
      category_prefix: categoryPrefix.trim() || null,
      product_ids: parseCsvList(productIdsRaw),
      boost_weight: weight,
      active,
    };
    if (editing) {
      updateMut.mutate(
        { id: editing.id, patch: payload },
        {
          onSuccess: () => {
            toast.success(t.campaignUpdated);
            closeForm();
          },
        },
      );
    } else {
      createMut.mutate(payload, {
        onSuccess: () => {
          toast.success(t.campaignCreated);
          closeForm();
        },
      });
    }
  };

  const busy = createMut.isPending || updateMut.isPending || deleteMut.isPending;
  const items = campaigns.data?.items ?? [];
  const catOptions = categories.data?.items ?? [];

  return (
    <>
      <Stripe kind="neutral" accent title={t.invisibleNoticeTitle} data-testid="invisible-campaigns-notice">
        {t.invisibleNoticeBody}
      </Stripe>

      <Card data-testid="aimo-campaigns-card">
        <CardHeader
          title={t.tabCampaigns}
          description={t.campaignsSubtitle}
          actions={
            isUser && !formOpen ? (
              <Button
                type="button"
                size="sm"
                variant="primary"
                icon={<Plus className="h-4 w-4" strokeWidth={2} />}
                onClick={openCreate}
                data-testid="new-campaign-btn"
              >
                {t.addCampaign}
              </Button>
            ) : null
          }
        />

        {formOpen && isUser && (
          <form onSubmit={handleSubmit} className="mt-4 space-y-4 rounded border border-line bg-primary-soft p-4" data-testid="campaign-form">
            <h3 className="text-[15px] font-bold text-navy">{editing ? t.editCampaign : t.addCampaign}</h3>
            <div className="grid gap-4 md:grid-cols-2">
              <Field label={t.campaignFields.title}>
                {(f) => (
                  <TextInput
                    {...f}
                    required
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder={t.campaignFields.titlePlaceholder}
                    data-testid="campaign-title-input"
                  />
                )}
              </Field>
              <Field label={t.campaignFields.boostWeight}>
                {(f) => (
                  <TextInput
                    {...f}
                    type="number"
                    min="0.1"
                    max="10"
                    step="0.1"
                    value={boostWeight}
                    onChange={(e) => setBoostWeight(e.target.value)}
                    data-testid="campaign-boost-input"
                  />
                )}
              </Field>
              <Field label={t.campaignFields.brandFilter}>
                {(f) => (
                  <TextInput
                    {...f}
                    value={brandFilter}
                    onChange={(e) => setBrandFilter(e.target.value)}
                    placeholder={t.campaignFields.brandPlaceholder}
                    data-testid="campaign-brand-input"
                  />
                )}
              </Field>
              <Field label={t.campaignFields.categoryPrefix}>
                {(f) =>
                  catOptions.length > 0 ? (
                    <select
                      id={f.id}
                      aria-invalid={f["aria-invalid"]}
                      value={categoryPrefix}
                      onChange={(e) => setCategoryPrefix(e.target.value)}
                      className={inputClass(f.invalid)}
                      data-testid="campaign-category-select"
                    >
                      <option value="">{t.campaignFields.categoryPlaceholder}</option>
                      {catOptions.map((c) => (
                        <option key={c.code} value={c.code}>
                          {c.name} ({c.code})
                        </option>
                      ))}
                    </select>
                  ) : (
                    <TextInput
                      {...f}
                      value={categoryPrefix}
                      onChange={(e) => setCategoryPrefix(e.target.value)}
                      placeholder={t.campaignFields.categoryPlaceholder}
                      data-testid="campaign-category-select"
                    />
                  )
                }
              </Field>
            </div>
            <Field label={t.campaignFields.productIds}>
              {(f) => (
                <TextInput
                  {...f}
                  value={productIdsRaw}
                  onChange={(e) => setProductIdsRaw(e.target.value)}
                  placeholder={t.campaignFields.productIdsPlaceholder}
                  data-testid="campaign-products-input"
                />
              )}
            </Field>
            <Toggle checked={active} onChange={setActive} label={t.campaignFields.active} />
            {(createMut.isError || updateMut.isError) && (
              <ErrorPanel error={createMut.error ?? updateMut.error} />
            )}
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" size="sm" variant="primary" loading={createMut.isPending || updateMut.isPending} data-testid="campaign-save-btn">
                {editing ? t.saveCampaign : t.createCampaign}
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={closeForm}>
                {t.cancel}
              </Button>
            </div>
          </form>
        )}

        <div className="mt-4 space-y-3">
          {!isUser ? (
            <Stripe kind="neutral" compact>
              {strings.admin.readOnlyNotice}
            </Stripe>
          ) : campaigns.isError ? (
            <ErrorPanel error={campaigns.error} onRetry={() => void campaigns.refetch()} />
          ) : campaigns.isPending ? (
            <Skeleton className="h-24 w-full" />
          ) : items.length === 0 ? (
            <EmptyState icon={<SlidersHorizontal className="h-6 w-6" strokeWidth={1.75} />} title={t.emptyCampaigns} />
          ) : (
            items.map((c) => (
              <div
                key={c.id}
                data-testid={`campaign-row-${c.id}`}
                className="flex flex-wrap items-center justify-between gap-3 rounded border border-line bg-white px-4 py-3"
              >
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-bold text-ink">{c.title}</span>
                    <SharpTag active={c.active} activeLabel={t.activeLabel} inactiveLabel={t.inactiveLabel} />
                    <span className="rounded-none bg-primary-soft px-2 py-0.5 text-[11px] font-bold uppercase tracking-[0.04em] text-navy">
                      {c.boost_weight.toFixed(1)}× {t.boostUnit}
                    </span>
                  </div>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-secondary">
                    <span className="font-mono-code">{c.id}</span>
                    {c.brand_filter && <span>Brand: {c.brand_filter}</span>}
                    {c.category_prefix && <span>Category: {c.category_prefix}</span>}
                    {c.product_ids.length > 0 && <span>SKUs: {c.product_ids.join(", ")}</span>}
                  </div>
                </div>
                {isUser && (
                  <div className="flex shrink-0 flex-wrap items-center gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() => updateMut.mutate({ id: c.id, patch: { active: !c.active } })}
                      data-testid={`campaign-toggle-${c.id}`}
                    >
                      {c.active ? t.deactivate : t.activate}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="text"
                      icon={<Pencil className="h-3.5 w-3.5" strokeWidth={1.75} />}
                      disabled={busy}
                      onClick={() => openEdit(c)}
                    >
                      {t.editCampaign}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="danger-text"
                      icon={<Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} />}
                      disabled={busy}
                      onClick={() =>
                        deleteMut.mutate(c.id, {
                          onSuccess: () => toast.success(t.campaignDeleted),
                        })
                      }
                      data-testid={`campaign-delete-${c.id}`}
                    >
                      {t.deleteCampaign}
                    </Button>
                  </div>
                )}
              </div>
            ))
          )}
          {deleteMut.isError && <ErrorPanel error={deleteMut.error} />}
        </div>
      </Card>
    </>
  );
}
