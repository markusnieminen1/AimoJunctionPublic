import { useState, type FormEvent } from "react";
import { SlidersHorizontal, Trash2 } from "lucide-react";
import {
  useBrandPreferences,
  useCategories,
  useCreateBrandPreference,
  useDeleteBrandPreference,
  useMe,
  useUpdateBrandPreference,
} from "../../api/hooks";
import type { BrandPreferenceOut } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { useStrings } from "../../i18n";
import { Button } from "../Button";
import { Card, CardHeader } from "../Card";
import { CopyChip } from "../CopyChip";
import { EmptyState } from "../EmptyState";
import { ErrorPanel } from "../ErrorPanel";
import { Field, TextInput, Toggle, inputClass } from "../FormField";
import { Skeleton } from "../Skeleton";
import { Stripe } from "../Stripe";
import { toast } from "../Toast";

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
 * Client-company Category Brand Preferences (`GET/POST/PATCH/DELETE /api/brand-preferences`),
 * scoped to the logged-in organization (`principal.company_id`). Rendered on `/account`.
 */
export function BrandPreferencesPanel() {
  const { isUser } = useAuth();
  const strings = useStrings();
  const t = strings.admin;

  const me = useMe({ silent: true });
  const prefs = useBrandPreferences({ silent: true });
  const categories = useCategories({ silent: true });
  const createMut = useCreateBrandPreference({ silent: true });
  const updateMut = useUpdateBrandPreference({ silent: true });
  const deleteMut = useDeleteBrandPreference({ silent: true });

  const [categoryPrefix, setCategoryPrefix] = useState("");
  const [categoryLabel, setCategoryLabel] = useState("");
  const [preferredBrandsRaw, setPreferredBrandsRaw] = useState("");
  const [active, setActive] = useState(true);

  const catOptions = categories.data?.items ?? [];
  const items: BrandPreferenceOut[] = prefs.data?.items ?? [];
  const companyId = me.data?.company.id ?? items[0]?.company_id ?? "";

  const handleCategoryChange = (code: string) => {
    setCategoryPrefix(code);
    const match = catOptions.find((c) => c.code === code);
    if (match) setCategoryLabel(match.name);
  };

  const handleCreate = (e: FormEvent) => {
    e.preventDefault();
    const prefix = categoryPrefix.trim();
    const label = categoryLabel.trim() || prefix;
    const brands = parseCsvList(preferredBrandsRaw);
    if (!prefix || brands.length === 0) return;

    createMut.mutate(
      {
        category_prefix: prefix,
        category_label: label,
        preferred_brands: brands,
        active,
      },
      {
        onSuccess: () => {
          toast.success(t.preferenceSaved);
          setCategoryPrefix("");
          setCategoryLabel("");
          setPreferredBrandsRaw("");
          setActive(true);
        },
      },
    );
  };

  const busy = createMut.isPending || updateMut.isPending || deleteMut.isPending;

  return (
    <Card data-testid="brand-preferences-card">
      <CardHeader title={t.tabPreferences} description={t.prefNotice} />

      {companyId && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs font-bold text-ink">
          <span className="shrink-0">{t.companyLabel}:</span>
          <CopyChip value={companyId} />
        </div>
      )}

      {!isUser && (
        <div className="mt-3">
          <Stripe kind="neutral" compact>
            {strings.promotions.readOnlyAgent}
          </Stripe>
        </div>
      )}

      {isUser && (
        <form onSubmit={handleCreate} className="mt-4 space-y-4 rounded border border-line bg-primary-soft p-4" data-testid="brand-pref-form">
          <h3 className="text-[15px] font-bold text-navy">{t.addPreference}</h3>
          <div className="grid gap-4 md:grid-cols-2">
            <Field label={t.prefFields.categoryPrefix}>
              {(f) =>
                catOptions.length > 0 ? (
                  <select
                    id={f.id}
                    required
                    aria-invalid={f["aria-invalid"]}
                    value={categoryPrefix}
                    onChange={(e) => handleCategoryChange(e.target.value)}
                    className={inputClass(f.invalid)}
                    data-testid="pref-category-select"
                  >
                    <option value="">{t.prefFields.categoryPlaceholder}</option>
                    {catOptions.map((c) => (
                      <option key={c.code} value={c.code}>
                        {c.name} ({c.code})
                      </option>
                    ))}
                  </select>
                ) : (
                  <TextInput
                    {...f}
                    required
                    value={categoryPrefix}
                    onChange={(e) => setCategoryPrefix(e.target.value)}
                    placeholder={t.prefFields.categoryPlaceholder}
                    data-testid="pref-category-select"
                  />
                )
              }
            </Field>
            <Field label={t.prefFields.categoryLabel}>
              {(f) => (
                <TextInput
                  {...f}
                  value={categoryLabel}
                  onChange={(e) => setCategoryLabel(e.target.value)}
                  placeholder={t.prefFields.categoryLabelPlaceholder}
                  data-testid="pref-label-input"
                />
              )}
            </Field>
          </div>
          <Field label={t.prefFields.preferredBrands}>
            {(f) => (
              <TextInput
                {...f}
                required
                value={preferredBrandsRaw}
                onChange={(e) => setPreferredBrandsRaw(e.target.value)}
                placeholder={t.prefFields.brandPlaceholder}
                data-testid="pref-brands-input"
              />
            )}
          </Field>
          <Toggle checked={active} onChange={setActive} label={t.prefFields.active} />
          {createMut.isError && <ErrorPanel error={createMut.error} />}
          <Button type="submit" size="sm" variant="primary" loading={createMut.isPending} data-testid="pref-save-btn">
            {t.savePreference}
          </Button>
        </form>
      )}

      <div className="mt-4 space-y-3">
        {prefs.isError ? (
          <ErrorPanel error={prefs.error} onRetry={() => void prefs.refetch()} />
        ) : prefs.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : items.length === 0 ? (
          <EmptyState icon={<SlidersHorizontal className="h-6 w-6" strokeWidth={1.75} />} title={t.emptyPreferences} />
        ) : (
          items.map((pref) => (
            <div
              key={pref.id}
              data-testid={`brand-pref-row-${pref.id}`}
              className="flex flex-wrap items-center justify-between gap-3 rounded border border-line bg-white px-4 py-3"
            >
              <div className="min-w-0 flex-1 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-bold text-ink">{pref.category_label}</span>
                  <SharpTag active={pref.active} activeLabel={t.activeLabel} inactiveLabel={t.inactiveLabel} />
                </div>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-secondary">
                  <span className="font-mono-code">{pref.category_prefix}</span>
                  <span className="font-semibold text-navy">{pref.preferred_brands.join(", ")}</span>
                </div>
              </div>
              {isUser && (
                <div className="flex shrink-0 flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => updateMut.mutate({ id: pref.id, patch: { active: !pref.active } })}
                    data-testid={`pref-toggle-${pref.id}`}
                  >
                    {pref.active ? t.deactivate : t.activate}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="danger-text"
                    icon={<Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} />}
                    disabled={busy}
                    onClick={() =>
                      deleteMut.mutate(pref.id, {
                        onSuccess: () => toast.success(t.preferenceDeleted),
                      })
                    }
                    data-testid={`pref-delete-${pref.id}`}
                  >
                    {t.deletePreference}
                  </Button>
                </div>
              )}
            </div>
          ))
        )}
        {(updateMut.isError || deleteMut.isError) && <ErrorPanel error={updateMut.error ?? deleteMut.error} />}
      </div>
    </Card>
  );
}
