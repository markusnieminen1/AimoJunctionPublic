import { useState } from "react";
import { ArrowRight, GitBranch } from "lucide-react";
import { isEndpointMissing, toApiError } from "../../api/client";
import { useInspectRouting } from "../../api/hooks";
import type { RoutingInspectRequest } from "../../api/types";
import { useStrings } from "../../i18n";
import { Badge } from "../Badge";
import { Button } from "../Button";
import { Card, CardHeader } from "../Card";
import { ErrorPanel } from "../ErrorPanel";
import { Stripe } from "../Stripe";

type PresetId = "quote_order" | "quote_lines" | "nl_order";

interface RouterPreset {
  id: PresetId;
  request: RoutingInspectRequest;
}

const PRESETS: RouterPreset[] = [
  {
    id: "quote_order",
    request: {
      method: "POST",
      path: "/api/orders",
      payload: { quote_id: "qt_demo_01" },
    },
  },
  {
    id: "quote_lines",
    request: {
      method: "POST",
      path: "/api/quotes",
      payload: {
        requested_delivery_date: "2026-11-16",
        lines: [{ product_id: "100001", quantity: 10 }],
      },
    },
  },
  {
    id: "nl_order",
    request: {
      method: "POST",
      path: "/api/orders",
      payload: { prompt: "Order 10 crates of lactose-free milk for Monday" },
    },
  },
];

export function RequestRouterInspector({ className = "" }: { className?: string }) {
  const strings = useStrings();
  const t = strings.audit;
  const inspect = useInspectRouting({ silent: true });
  const [selected, setSelected] = useState<RouterPreset | null>(null);

  const presetLabel = (id: PresetId): string => {
    if (id === "quote_order") return t.routerPresetQuoteOrder;
    if (id === "quote_lines") return t.routerPresetQuoteLines;
    return t.routerPresetNlOrder;
  };

  const handleSelect = (preset: RouterPreset) => {
    setSelected(preset);
    inspect.mutate(preset.request);
  };

  const errMissing =
    inspect.isError && (isEndpointMissing(inspect.error) || toApiError(inspect.error).status === 404);

  return (
    <section data-testid="request-router-inspector" className={className}>
      <Card>
        <CardHeader
          title={
            <span className="inline-flex items-center gap-2">
              <GitBranch className="h-4 w-4 text-navy" strokeWidth={1.75} aria-hidden="true" />
              {t.routerTitle}
            </span>
          }
          description={t.routerSubtitle}
        />

        <div className="mt-3 flex flex-wrap gap-2">
          {PRESETS.map((preset) => {
            const active = selected?.id === preset.id;
            return (
              <Button
                key={preset.id}
                type="button"
                size="sm"
                variant={active ? "primary" : "outline"}
                loading={active && inspect.isPending}
                onClick={() => handleSelect(preset)}
                data-testid={`router-preset-${preset.id}`}
              >
                {presetLabel(preset.id)}
              </Button>
            );
          })}
        </div>

        {errMissing ? (
          <Stripe kind="neutral" compact className="mt-3" data-testid="router-unavailable">
            {t.routerUnavailable}
          </Stripe>
        ) : inspect.isError ? (
          <div className="mt-3">
            <ErrorPanel error={inspect.error} />
          </div>
        ) : inspect.data ? (
          <div
            data-testid="router-result"
            className="mt-4 rounded border border-line bg-surface p-3.5 text-[13px] space-y-3"
          >
            <div className="grid gap-3 md:grid-cols-3">
              <div>
                <div className="text-[11px] font-bold uppercase tracking-wide text-ink-secondary">
                  {t.routerTargetLabel}
                </div>
                <div className="mt-1">
                  <Badge
                    data-testid="router-target-service"
                    tone={inspect.data.target_service === "core-api" ? "success" : "info"}
                  >
                    {inspect.data.target_service}
                  </Badge>
                </div>
              </div>

              <div>
                <div className="text-[11px] font-bold uppercase tracking-wide text-ink-secondary">
                  {t.routerMatchedLabel}
                </div>
                <div className="mt-1 flex flex-wrap gap-1" data-testid="router-matched-fields">
                  {inspect.data.matched_fields.length > 0 ? (
                    inspect.data.matched_fields.map((field) => (
                      <Badge key={field} tone="info">
                        {field}
                      </Badge>
                    ))
                  ) : (
                    <span className="font-mono-code text-[12px] text-ink-secondary">{t.routerNone}</span>
                  )}
                </div>
              </div>

              <div>
                <div className="text-[11px] font-bold uppercase tracking-wide text-ink-secondary">
                  {t.routerStructuredLabel}
                </div>
                <div className="mt-1 flex flex-wrap gap-1" data-testid="router-structured-fields">
                  {inspect.data.structured_fields.length > 0 ? (
                    inspect.data.structured_fields.map((field) => (
                      <Badge key={field} tone="neutral">
                        {field}
                      </Badge>
                    ))
                  ) : (
                    <span className="font-mono-code text-[12px] text-ink-secondary">{t.routerNone}</span>
                  )}
                </div>
              </div>
            </div>

            <div>
              <div className="text-[11px] font-bold uppercase tracking-wide text-ink-secondary">
                {t.routerReasonLabel}
              </div>
              <p className="mt-0.5 text-[13px] text-ink" data-testid="router-reason">
                {inspect.data.reason}
              </p>
            </div>

            <div>
              <div className="text-[11px] font-bold uppercase tracking-wide text-ink-secondary">
                {t.routerFlowLabel}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-1.5" data-testid="router-downstream-flow">
                {inspect.data.downstream_flow.map((step, idx) => (
                  <span key={`${step}-${idx}`} className="inline-flex items-center gap-1.5">
                    {idx > 0 && <ArrowRight className="h-3.5 w-3.5 text-ink-secondary" aria-hidden="true" />}
                    <span className="font-mono-code rounded border border-line bg-white px-2 py-0.5 text-[12px] font-medium text-navy">
                      {step}
                    </span>
                  </span>
                ))}
              </div>
            </div>

            {selected && (
              <div className="border-t border-line pt-2 text-[12px] text-ink-secondary">
                <span className="font-bold text-ink">{t.routerPayloadLabel}:</span>{" "}
                <code className="font-mono-code">
                  {selected.request.method ?? "POST"} {selected.request.path}{" "}
                  {JSON.stringify(selected.request.payload)}
                </code>
              </div>
            )}
          </div>
        ) : (
          <p className="mt-3 text-[13px] text-ink-secondary">{t.routerPromptHint}</p>
        )}
      </Card>
    </section>
  );
}
