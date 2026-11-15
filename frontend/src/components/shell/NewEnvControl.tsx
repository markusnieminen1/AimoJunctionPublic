import { useState } from "react";
import { PlusCircle } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { endpoints } from "../../api/endpoints";
import { qk } from "../../api/hooks";
import type { AgentWithSecret } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { useStrings } from "../../i18n";
import { SecretModal } from "../agents/SecretModal";
import { toast } from "../Toast";

interface CreatedEnvState {
  agent: AgentWithSecret;
  companyName: string;
  email: string;
}

export function useNewEnvProvisioner() {
  const { register } = useAuth();
  const qc = useQueryClient();
  const p = useStrings().portal;
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<CreatedEnvState | null>(null);

  const provision = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const tag = Math.random().toString(36).slice(2, 7);
      const companyName = `Demo Kitchen ${tag.toUpperCase()} Oy`;
      const userName = `Chef ${tag.toUpperCase()}`;
      const email = `chef.${tag}@hotel.fi`;
      const password = `AimoDemo!${tag}9`;

      await register({
        company_name: companyName,
        business_id: "FI9999999-1",
        user_name: userName,
        email,
        password,
      });

      const createdAgent = await endpoints.createAgent({
        name: `Kitchen Agent (${tag.toUpperCase()})`,
        description: "Isolated sandbox buyer agent provisioned via New Env",
        auto_approve_limit_eur: 500,
        max_order_value_eur: 5000,
        daily_spend_limit_eur: 10000,
        allowed_categories: [],
        max_quantity_per_line: null,
        can_place_orders: true,
        expires_at: null,
      });

      void qc.invalidateQueries({ queryKey: qk.agentsAll });
      void qc.invalidateQueries({ queryKey: qk.me });
      setCreated({ agent: createdAgent, companyName, email });
      toast.success(p.newEnvCreatedToast);
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  };

  return {
    busy,
    created,
    provision,
    clear: () => setCreated(null),
  };
}

export function NewEnvTopButton({
  className = "",
  variant = "topbar",
}: {
  className?: string;
  variant?: "topbar" | "menu";
}) {
  const { busy, created, provision, clear } = useNewEnvProvisioner();
  const p = useStrings().portal;

  return (
    <>
      {variant === "topbar" ? (
        <button
          type="button"
          onClick={() => void provision()}
          disabled={busy}
          title={p.newEnvTitle}
          data-testid="new-env-btn"
          className={`inline-flex h-7 items-center gap-1 rounded border border-white/25 bg-white/10 px-2 text-[12px] font-semibold text-white transition-colors hover:bg-white/20 disabled:opacity-60 ${className}`}
        >
          <PlusCircle className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
          <span className="hidden sm:inline">{p.newEnvButton}</span>
        </button>
      ) : (
        <button
          type="button"
          onClick={() => void provision()}
          disabled={busy}
          data-testid="new-env-menu-btn"
          className={className}
        >
          <PlusCircle className="h-4 w-4 text-navy shrink-0" strokeWidth={1.5} aria-hidden="true" />
          <span>{p.newEnvMenuLabel}</span>
        </button>
      )}

      <SecretModal
        data={created?.agent ?? null}
        title={p.newEnvReadyTitle}
        subtitle={created ? p.newEnvSubtitle(created.companyName, created.email) : undefined}
        onDone={clear}
      />
    </>
  );
}
