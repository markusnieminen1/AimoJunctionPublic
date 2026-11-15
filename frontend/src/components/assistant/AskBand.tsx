// The portal's "Ask A.I." pattern (DESIGN_VALIO §4.10, my_aimo_1280.png): lime-light band, petrol Bot circle, display
// heading, lime-framed composer with a primary ASK, a disclaimer line and example pills.
import { useState, type FormEvent, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { Bot, Info } from "lucide-react";
import { useStrings } from "../../i18n";
import { Button } from "../Button";
import { SuggestionPill } from "../Pills";

/** Lime frame holding a white 48 px input (Bot icon at 16 px) + primary ASK. Pass `children` to supply your own field. */
export function AskComposer({ value, onChange, onSubmit, disabled = false, busy = false, submitLabel, className = "", children }: {
  value?: string;
  onChange?: (v: string) => void;
  onSubmit: (e: FormEvent) => void;
  disabled?: boolean;
  busy?: boolean;
  submitLabel?: string;
  className?: string;
  children?: ReactNode;
}) {
  const f = useStrings().front;
  return (
    <form onSubmit={onSubmit} className={`flex items-stretch gap-2 rounded bg-lime p-2 ${className}`} data-testid="ask-composer">
      <div className="relative min-w-0 flex-1">
        <Bot className="pointer-events-none absolute left-4 top-3 h-6 w-6 text-navy" strokeWidth={1.5} aria-hidden="true" />
        {children ?? (
          <input
            aria-label={f.askPlaceholder}
            value={value}
            onChange={(e) => onChange?.(e.target.value)}
            placeholder={f.askPlaceholder}
            className="h-12 w-full rounded-sm border border-line-strong bg-white pl-[54px] pr-3 text-[16px] text-ink placeholder:text-ink-muted focus:outline-none focus:ring-2 focus:ring-navy"
          />
        )}
      </div>
      <Button type="submit" size="lg" disabled={disabled} loading={busy} aria-label={submitLabel} className="self-end px-5 disabled:opacity-60 md:px-[30px]">
        {f.ask}
      </Button>
    </form>
  );
}

/** Disclaimer line with the info icon on the right. */
export function AskDisclaimer({ className = "" }: { className?: string }) {
  const f = useStrings().front;
  return (
    <p className={`flex items-start gap-2 text-[14px] leading-[17px] text-navy ${className}`}>
      {f.askDisclaimer}
      <Info className="h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
    </p>
  );
}

/** "Examples:" + suggestion pills. */
export function AskExamples({ onPick, disabled = false, className = "" }: { onPick: (s: string) => void; disabled?: boolean; className?: string }) {
  const strings = useStrings();
  const f = strings.front;
  return (
    <div className={`flex flex-wrap items-center gap-x-3 gap-y-2 ${className}`} data-testid="suggestions">
      <span className="text-[16px] font-bold text-ink">{f.examples}</span>
      {strings.assistant.suggested.map((s) => (
        <SuggestionPill key={s} onClick={() => onPick(s)} disabled={disabled}>
          {s}
        </SuggestionPill>
      ))}
    </div>
  );
}

/** Petrol 56 px circle with the Bot icon (stands in for the portal's robot illustration). */
export function BotMark({ className = "" }: { className?: string }) {
  return (
    <span className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-navy text-white ${className}`} aria-hidden="true">
      <Bot className="h-7 w-7" strokeWidth={1.5} />
    </span>
  );
}

/** Dashboard band: composer + examples hand the prompt to /assistant?prompt=… (which sends it). */
export function AskBand() {
  const f = useStrings().front;
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const go = (text: string) => {
    const v = text.trim();
    navigate(v ? `/assistant?prompt=${encodeURIComponent(v)}` : "/assistant");
  };
  return (
    <section className="bg-lime-soft" aria-labelledby="ask-band-title">
      <div className="container-portal pb-12 pt-10">
        <div className="flex items-end gap-4">
          <BotMark />
          <h2 id="ask-band-title" className="font-display text-[32px] leading-[35px] tracking-[0.01em] text-navy">
            {f.askTitle}
          </h2>
        </div>
        <AskComposer
          value={q}
          onChange={setQ}
          onSubmit={(e) => {
            e.preventDefault();
            go(q);
          }}
          className="mt-4"
        />
        <AskDisclaimer className="mt-2" />
        <AskExamples onPick={go} className="mt-4" />
      </div>
    </section>
  );
}
