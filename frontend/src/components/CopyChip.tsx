import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { copyToClipboard } from "../lib/clipboard";

/** Small monospace chip that copies `value` (request_id, SKU, content_hash) on click. `label` overrides the shown text. */
export function CopyChip({ value, label, className = "" }: { value: string; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await copyToClipboard(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };
  return (
    <button
      type="button"
      onClick={copy}
      title={`Copy ${value}`}
      className={`font-mono-code inline-flex min-w-0 max-w-full items-center gap-1 rounded-sm border border-line bg-white px-1.5 py-0.5 text-[11px] text-ink-secondary hover:border-navy hover:text-navy ${className}`}
    >
      <span className="min-w-0 truncate">{label ?? value}</span>
      {copied ? <Check className="h-3 w-3 shrink-0 text-success" strokeWidth={2} /> : <Copy className="h-3 w-3 shrink-0" strokeWidth={1.75} />}
    </button>
  );
}
