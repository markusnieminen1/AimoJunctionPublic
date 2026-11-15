import type { ReactNode } from "react";

const INLINE_RE = /(\*\*[^*\n]+?\*\*|`[^`\n]+?`)/g;

/** Safely renders inline `**bold**` and `` `code` `` spans without HTML injection. */
function renderInline(line: string, keyPrefix: string): ReactNode[] {
  const parts = line.split(INLINE_RE);
  return parts.map((part, idx) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return (
        <strong key={`${keyPrefix}-b-${idx}`} className="font-bold text-ink">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code
          key={`${keyPrefix}-c-${idx}`}
          className="font-mono-code bg-primary-soft px-1 py-0.5 text-[0.9em] text-navy"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    return part;
  });
}

const BULLET_RE = /^\s*(?:[-*]|\u2022)\s+(.*)$/;
const NUMBERED_RE = /^\s*(\d+)\.\s+(.*)$/;
const HEADING_RE = /^\s*#{1,3}\s+(.*)$/;

/** Safe Markdown subset renderer (`**bold**`, `` `code` ``, bullet/numbered lists, headings) for assistant chat. */
export function MarkdownText({ text, className = "" }: { text: string; className?: string }) {
  const lines = text.split(/\r?\n/);
  const blocks: ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i]!;
    if (!line.trim()) {
      i += 1;
      continue;
    }

    if (BULLET_RE.test(line)) {
      const items: string[] = [];
      while (i < lines.length && BULLET_RE.test(lines[i]!)) {
        items.push(lines[i]!.match(BULLET_RE)![1]!);
        i += 1;
      }
      blocks.push(
        <ul key={`ul-${i}`} className="space-y-1 pl-0.5">
          {items.map((item, j) => (
            <li key={j} className="flex items-start gap-2">
              <span className="mt-[8px] h-1.5 w-1.5 shrink-0 bg-navy" aria-hidden="true" />
              <span className="min-w-0 flex-1 break-words">{renderInline(item, `ul-${i}-${j}`)}</span>
            </li>
          ))}
        </ul>,
      );
      continue;
    }

    if (NUMBERED_RE.test(line)) {
      const items: { num: string; body: string }[] = [];
      while (i < lines.length && NUMBERED_RE.test(lines[i]!)) {
        const m = lines[i]!.match(NUMBERED_RE)!;
        items.push({ num: m[1]!, body: m[2]! });
        i += 1;
      }
      blocks.push(
        <ol key={`ol-${i}`} className="space-y-1 pl-0.5">
          {items.map((item, j) => (
            <li key={j} className="flex items-start gap-2">
              <span className="tabular-nums shrink-0 font-bold text-navy">{item.num}.</span>
              <span className="min-w-0 flex-1 break-words">{renderInline(item.body, `ol-${i}-${j}`)}</span>
            </li>
          ))}
        </ol>,
      );
      continue;
    }

    const heading = line.match(HEADING_RE);
    if (heading) {
      blocks.push(
        <p key={`h-${i}`} className="break-words font-bold text-navy">
          {renderInline(heading[1]!, `h-${i}`)}
        </p>,
      );
      i += 1;
      continue;
    }

    const paraLines: string[] = [];
    while (
      i < lines.length &&
      lines[i]!.trim() &&
      !BULLET_RE.test(lines[i]!) &&
      !NUMBERED_RE.test(lines[i]!) &&
      !HEADING_RE.test(lines[i]!)
    ) {
      paraLines.push(lines[i]!);
      i += 1;
    }
    blocks.push(
      <p key={`p-${i}`} className="break-words">
        {paraLines.map((pl, j) => (
          <span key={j}>
            {j > 0 && <br />}
            {renderInline(pl, `p-${i}-${j}`)}
          </span>
        ))}
      </p>,
    );
  }

  return <div className={`space-y-1.5 ${className}`}>{blocks}</div>;
}
