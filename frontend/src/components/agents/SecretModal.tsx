// One-time agent secret screen (DESIGN.md §6G): dark token box, copy buttons, curl snippet, "you will not see this again".
import { useEffect, useState, type ReactNode } from "react";
import { Check, Copy, Download, Terminal } from "lucide-react";
import { endpoints } from "../../api/endpoints";
import type { AgentWithSecret } from "../../api/types";
import {
  buildClaudeMcpAddCommand,
  buildClaudePluginZip,
  buildMcpJson,
  buildUniversalAgentPrompt,
  claudePluginZipFilename,
} from "../../lib/agentBundle";
import { copyToClipboard } from "../../lib/clipboard";
import { triggerInMemoryDownload } from "../../lib/zip";
import { useStrings } from "../../i18n";
import { Button } from "../Button";
import { Modal } from "../Modal";
import { Stripe } from "../Stripe";
import { toast } from "../Toast";
import { ClaudeMark } from "./ClaudeMark";

/** Ready-to-paste token exchange, same origin as the UI (works behind any HTTPS reverse proxy too). */
export function agentTokenCurl(agentId: string, secret: string, origin = window.location.origin): string {
  return [
    `curl -s -X POST ${origin}/api/agent-auth/token \\`,
    `  -H 'Content-Type: application/json' \\`,
    `  -d '{"agent_id":"${agentId}","agent_secret":"${secret}"}'`,
  ].join("\n");
}

function DarkCopy({ label, value }: { label: string; value: string }) {
  const strings = useStrings();
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="icon-light"
      size="sm"
      onClick={async () => {
        await copyToClipboard(value);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      }}
      className="h-7 gap-1 px-2 text-[11px]"
      aria-label={`Copy ${label}`}
    >
      {copied ? <Check className="h-3 w-3 text-[#C0E021]" strokeWidth={2} /> : <Copy className="h-3 w-3" strokeWidth={1.75} />}
      {copied ? strings.common.copied : strings.common.copy}
    </Button>
  );
}

function SecretRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-b border-white/10 px-3 py-2.5 last:border-b-0">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10.5px] font-semibold uppercase tracking-[0.06em] text-[#8C9699]">{label}</span>
        <DarkCopy label={label} value={value} />
      </div>
      <p className="font-mono-code mt-1 break-all text-[13px] text-[#F2F6F8]" data-testid={`secret-${label}`}>
        {value}
      </p>
    </div>
  );
}

/**
 * Not dismissible by Esc/backdrop: the user must confirm they stored the secret.
 * The caller should drop `data` from state in `onDone` so the secret is gone from memory.
 */
export function SecretModal({
  data,
  onDone,
  title,
  subtitle,
}: {
  data: AgentWithSecret | null;
  onDone: () => void;
  title?: string;
  subtitle?: ReactNode;
}) {
  const strings = useStrings();
  const t = strings.agents;
  const [copiedKey, setCopiedKey] = useState<"prompt" | "cli" | null>(null);
  const [mintedToken, setMintedToken] = useState<{
    agentId: string;
    token: string;
    expires_at: string;
  } | null>(null);
  const activeAgentId = data?.agent.id ?? null;
  const activeAgentSecret = data?.agent_secret ?? null;

  useEffect(() => {
    if (!activeAgentId || !activeAgentSecret) {
      setMintedToken(null);
      return;
    }
    let cancelled = false;
    setMintedToken(null);
    endpoints
      .agentToken({ agent_id: activeAgentId, agent_secret: activeAgentSecret })
      .then((res) => {
        if (!cancelled && res?.token) {
          setMintedToken({
            agentId: activeAgentId,
            token: res.token,
            expires_at: res.expires_at,
          });
        }
      })
      .catch(() => {
        // Keep synchronous .zip/.mcp.json downloads working via agent_id + agent_secret even if offline.
      });
    return () => {
      cancelled = true;
    };
  }, [activeAgentId, activeAgentSecret]);

  if (!data) return null;
  const currentMinted = mintedToken?.agentId === data.agent.id ? mintedToken : null;
  const mcpValues = {
    agentId: data.agent.id,
    agentSecret: data.agent_secret,
    bearerToken: currentMinted?.token,
    tokenExpiresAt: currentMinted?.expires_at,
  };
  const effectiveTitle = title ?? t.secretTitle;
  const curl = agentTokenCurl(data.agent.id, data.agent_secret);
  const mcpJson = buildMcpJson(mcpValues);
  const claudeCmd = buildClaudeMcpAddCommand({ agentId: data.agent.id, agentSecret: data.agent_secret });
  const universalPrompt = buildUniversalAgentPrompt({ agentId: data.agent.id, agentSecret: data.agent_secret });

  async function copyPreset(kind: "prompt" | "cli", value: string) {
    await copyToClipboard(value);
    setCopiedKey(kind);
    toast.success(strings.common.copied);
    window.setTimeout(() => setCopiedKey((c) => (c === kind ? null : c)), 1500);
  }

  function handleDownloadMcp() {
    triggerInMemoryDownload(".mcp.json", mcpJson, "application/json;charset=utf-8");
  }

  function handleDownloadZip() {
    if (!data) return;
    const archive = buildClaudePluginZip(mcpValues);
    const bytes = new Uint8Array(archive.length);
    bytes.set(archive);
    triggerInMemoryDownload(claudePluginZipFilename(data.agent.id), bytes.buffer, "application/zip");
  }

  return (
    <Modal
      open
      onClose={onDone}
      dismissible={false}
      size="lg"
      title={
        <span>
          {effectiveTitle} · <span className="text-navy">{data.agent.name}</span>
        </span>
      }
      footer={<Button variant="primary" onClick={onDone}>{t.secretDone}</Button>}
    >
      <div className="space-y-4">
        {subtitle && (
          <Stripe kind="success" compact data-testid="secret-modal-subtitle">
            {subtitle}
          </Stripe>
        )}
        <Stripe kind="warning" accent role="alert" title={t.secretWarning}>
          <p>{t.secretWarningBody}</p>
        </Stripe>
        <div className="overflow-hidden rounded bg-[#003747]">
          <SecretRow label="agent_id" value={data.agent.id} />
          <SecretRow label="agent_secret" value={data.agent_secret} />
        </div>
        <div>
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <h3 className="text-[13px] font-semibold text-ink">{t.secretCurl}</h3>
            <span className="font-mono-code text-[11px] text-ink-muted">POST /api/agent-auth/token</span>
          </div>
          <div className="relative rounded bg-[#003747]">
            <div className="absolute right-2 top-2">
              <DarkCopy label="curl command" value={curl} />
            </div>
            <pre className="font-mono-code overflow-x-auto whitespace-pre px-3 py-3 pr-20 text-[12px] leading-5 text-[#F2F6F8]">{curl}</pre>
          </div>
          <p className="mt-1.5 text-xs text-ink-muted">{t.secretTokenHint}</p>
        </div>

        <div className="border-t border-border pt-4">
          <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
            <h3 className="inline-flex items-center gap-1.5 text-[13px] font-semibold text-ink">
              <ClaudeMark className="h-4 w-4 shrink-0" />
              {t.secretMcpTitle}
            </h3>
            <span className="font-mono-code text-[11px] text-ink-muted">.mcp.json + ./mcp/aimo_mcp_bridge.py</span>
          </div>
          <p className="mb-2 text-xs text-ink-muted">{t.secretMcpLead}</p>
          <ol className="mb-3 space-y-1 rounded border border-line bg-primary-soft px-3 py-2 text-xs leading-relaxed text-ink">
            {t.secretMcpSteps.map((step, i) => (
              <li key={i} className="flex gap-2">
                <span className="font-bold text-navy">{i + 1}.</span>
                <span>{step}</span>
              </li>
            ))}
          </ol>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              data-testid="download-claude-zip"
              onClick={handleDownloadZip}
            >
              <ClaudeMark className="h-3.5 w-3.5 shrink-0" />
              <Download className="h-3.5 w-3.5" aria-hidden />
              {t.secretDownloadZip}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              data-testid="download-mcp-json"
              onClick={handleDownloadMcp}
            >
              <Download className="h-3.5 w-3.5" aria-hidden />
              {t.secretDownloadMcp}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              data-testid="copy-claude-cli-btn"
              onClick={() => void copyPreset("cli", claudeCmd)}
            >
              {copiedKey === "cli" ? <Check className="h-3.5 w-3.5 text-success" aria-hidden /> : <Terminal className="h-3.5 w-3.5" aria-hidden />}
              {copiedKey === "cli" ? strings.common.copied : t.copyClaudeCmd}
            </Button>
            <Button
              type="button"
              variant="lime"
              size="sm"
              data-testid="copy-universal-agent-prompt"
              onClick={() => void copyPreset("prompt", universalPrompt)}
            >
              {copiedKey === "prompt" ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
              {copiedKey === "prompt" ? strings.common.copied : t.copyPromptCta}
            </Button>
          </div>
          <div className="relative mt-2.5 rounded bg-[#003747]">
            <div className="absolute right-2 top-2">
              <DarkCopy label=".mcp.json" value={mcpJson} />
            </div>
            <pre
              data-testid="secret-mcp-json"
              className="font-mono-code overflow-x-auto whitespace-pre px-3 py-3 pr-20 text-[12px] leading-5 text-[#F2F6F8]"
            >
              {mcpJson}
            </pre>
          </div>
          <p data-testid="secret-mcp-warning" className="mt-1.5 text-xs font-medium text-warn-text">
            {t.secretMcpWarning}
          </p>
        </div>
      </div>
    </Modal>
  );
}
