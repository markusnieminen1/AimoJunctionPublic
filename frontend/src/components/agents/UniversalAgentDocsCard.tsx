import { useState } from "react";
import { Link } from "react-router-dom";
import { BookOpen, Check, Copy } from "lucide-react";
import { useA2AAgentCard, useA2ASessionConnect, useNegotiationSettings } from "../../api/hooks";
import type { Agent } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { useStrings } from "../../i18n";
import { buildUniversalAgentPrompt } from "../../lib/agentBundle";
import { copyToClipboard } from "../../lib/clipboard";
import { Badge } from "../Badge";
import { Button } from "../Button";
import { Card, CardHeader } from "../Card";
import { Stripe } from "../Stripe";
import { toast } from "../Toast";
import { ClaudeMark } from "./ClaudeMark";

type DocTab = "mcp" | "langgraph" | "rest" | "a2a";

export function UniversalAgentDocsCard({
  agents,
  className = "",
}: {
  agents: Agent[];
  className?: string;
}) {
  const strings = useStrings();
  const t = strings.agents;
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<DocTab>("mcp");
  const [copied, setCopied] = useState(false);

  const sessionQ = useA2ASessionConnect({ silent: true });
  const cardQ = useA2AAgentCard({ silent: true });
  const negSettingsQ = useNegotiationSettings({ silent: true });
  const { company } = useAuth();
  const a2aMatters = sessionQ.data?.result?.supported_matters ?? cardQ.data?.supported_matters ?? [];
  const negotiationAllowed =
    negSettingsQ.data?.allow_price_negotiation ??
    a2aMatters.find((m) => m.id === "preliminary_tendering")?.enabled ??
    false;

  const activeAgent = agents.find((a) => a.status === "active") ?? agents[0];
  const agentId = activeAgent?.id ?? "<AIMO_AGENT_ID>";
  const origin = window.location.origin;
  const fullPrompt = buildUniversalAgentPrompt({ agentId, origin });

  const handleCopyPrompt = async () => {
    await copyToClipboard(fullPrompt);
    setCopied(true);
    toast.success(strings.common.copied);
    window.setTimeout(() => setCopied(false), 1800);
  };

  const snippets: Record<DocTab, string> = {
    mcp: JSON.stringify(
      {
        mcpServers: {
          aimoannos: {
            command: "python3",
            args: ["./mcp/aimo_mcp_bridge.py"],
            env: {
              AIMO_BASE_URL: origin,
              AIMO_AGENT_ID: agentId,
              AIMO_AGENT_SECRET: "<AIMO_AGENT_SECRET>",
            },
          },
        },
      },
      null,
      2,
    ),
    langgraph: [
      "from langchain_core.tools import tool",
      "import httpx, uuid",
      "",
      `@tool`,
      `def aimo_place_order(quote_id: str, token: str) -> dict:`,
      `    return httpx.post(`,
      `        "${origin}/api/orders",`,
      `        json={"quote_id": quote_id},`,
      `        headers={`,
      `            "Authorization": f"Bearer {token}",`,
      `            "Idempotency-Key": str(uuid.uuid4()),`,
      `        },`,
      `    ).json()`,
    ].join("\n"),
    rest: [
      `# 1. Exchange agent secret for 60-min JWT`,
      `curl -sS -X POST ${origin}/api/agent-auth/token \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '{"agent_id":"${agentId}","agent_secret":"<AIMO_AGENT_SECRET>"}'`,
      ``,
      `# 2. Quote & Order (policy.py + content_hash)`,
      `curl -sS -X POST ${origin}/api/orders \\`,
      `  -H "Authorization: Bearer <TOKEN>" \\`,
      `  -H "Idempotency-Key: $(uuidgen)" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '{"quote_id":"qt_..."}'`,
    ].join("\n"),
    a2a: [
      `# 1. Discover Agent Card & Supported Matters`,
      `curl -sS ${origin}/api/.well-known/agent-card.json`,
      ``,
      `# 2. A2A session/connect handshake (returns menu_planning, order_execution, preliminary_tendering, exception_handling)`,
      `curl -sS -X POST ${origin}/api/a2a \\`,
      `  -H "Authorization: Bearer <TOKEN>" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '{"jsonrpc":"2.0","id":"1","method":"session/connect","params":{}}'`,
    ].join("\n"),
  };

  return (
    <Card className={className} data-testid="universal-agent-docs">
      <CardHeader
        title={
          <span className="inline-flex items-center gap-2">
            <BookOpen className="h-4 w-4 text-navy" strokeWidth={1.75} aria-hidden="true" />
            {t.docsTitle}
          </span>
        }
        description={t.docsSubtitle}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="lime"
              size="sm"
              data-testid="copy-agent-docs-prompt"
              onClick={() => void handleCopyPrompt()}
            >
              {copied ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : <Copy className="h-3.5 w-3.5" aria-hidden="true" />}
              {copied ? strings.common.copied : t.copyPromptFullCta}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              data-testid="toggle-agent-docs"
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
            >
              {open ? t.docsHide : t.docsShow}
            </Button>
          </div>
        }
      />

      {a2aMatters.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2" data-testid="a2a-matters-strip">
          <span className="text-[11.5px] font-bold uppercase tracking-wide text-ink-secondary">{t.a2aMattersTitle}:</span>
          {a2aMatters.map((m) => (
            <Badge key={m.id} tone={m.enabled ? "success" : "neutral"}>
              {m.title} · {m.enabled ? t.a2aEnabledBadge : t.a2aDisabledBadge}
            </Badge>
          ))}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-none border border-line bg-surface px-3 py-2 text-[12.5px]">
        <span className="min-w-0 text-ink-secondary">
          {t.negotiationAdminLabel(company?.name, negotiationAllowed)}
        </span>
        <Link
          to="/admin"
          data-testid="negotiation-admin-link"
          className="shrink-0 font-bold text-navy underline underline-offset-2 hover:text-primary"
        >
          {t.negotiationAdminLink} →
        </Link>
      </div>

      {open && (
        <div className="mt-4 space-y-3 border-t border-line pt-3" data-testid="universal-agent-docs-body">
          <Stripe kind="neutral" compact>
            {t.docsRules}
          </Stripe>

          <div className="flex flex-wrap gap-1.5" role="tablist" aria-label={t.docsTitle}>
            {(
              [
                { id: "mcp", label: t.docsTabMcp },
                { id: "langgraph", label: t.docsTabLangGraph },
                { id: "rest", label: t.docsTabRest },
                { id: "a2a", label: t.docsTabA2a },
              ] as const
            ).map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={tab === item.id}
                data-testid={`agent-docs-tab-${item.id}`}
                onClick={() => setTab(item.id)}
                className={`inline-flex items-center gap-1.5 rounded border px-3 py-1 text-[13px] font-bold transition-colors ${
                  tab === item.id
                    ? "border-navy bg-navy text-white"
                    : "border-line bg-surface text-navy hover:bg-primary-soft"
                }`}
              >
                {item.id === "mcp" && <ClaudeMark className="h-3.5 w-3.5 shrink-0" />}
                {item.label}
              </button>
            ))}
          </div>

          {tab === "mcp" && (
            <p className="text-xs leading-relaxed text-ink-secondary">{t.docsMcpInstallHint}</p>
          )}

          <pre className="font-mono-code overflow-x-auto rounded bg-[#003747] p-3 text-[12px] leading-5 text-[#F2F6F8]">
            {snippets[tab]}
          </pre>
        </div>
      )}
    </Card>
  );
}
