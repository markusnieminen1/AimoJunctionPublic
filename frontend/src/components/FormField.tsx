import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from "react";
import { AlertCircle } from "lucide-react";

/** Portal input (DESIGN_VALIO §4.2): h 45, 1 px #8C9699, radius 4, 16 px text, petrol focus ring. `invalid` turns it red. */
export function inputClass(invalid?: boolean, extra = ""): string {
  return `h-[45px] w-full rounded border bg-white px-2 text-[16px] text-ink placeholder:text-line-strong focus:outline-none focus:ring-[3px] read-only:bg-primary-soft disabled:bg-primary-soft disabled:text-ink-muted ${
    invalid ? "border-danger focus:border-danger focus:ring-[rgba(219,0,2,0.15)]" : "border-line-strong focus:border-navy focus:ring-[rgba(0,86,112,0.15)]"
  } ${extra}`;
}

/**
 * Label + control + hint/error. `children` receives the generated id and aria props so the
 * control is labelled and announces its error: `{(p) => <TextInput {...p} />}`.
 */
export function Field({ label, hint, error, optional, children, className = "" }: {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  optional?: boolean;
  className?: string;
  children: (p: { id: string; invalid: boolean; "aria-invalid": boolean; "aria-describedby"?: string }) => ReactNode;
}) {
  const id = useId();
  const msgId = `${id}-msg`;
  const hasMsg = !!error || !!hint;
  return (
    <div className={className}>
      <label htmlFor={id} className="mb-2 flex items-baseline justify-between gap-2 text-[14px] font-bold leading-[17px] text-ink">
        <span>{label}</span>
        {optional && <span className="text-[13px] font-normal text-ink-muted">Optional</span>}
      </label>
      {children({ id, invalid: !!error, "aria-invalid": !!error, "aria-describedby": hasMsg ? msgId : undefined })}
      {error ? (
        <p id={msgId} className="mt-1.5 flex items-center gap-1 text-[13px] font-medium text-danger">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
          {error}
        </p>
      ) : hint ? (
        <p id={msgId} className="mt-1.5 text-[12px] text-ink-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

/** Text/number/date input with the shared style. `invalid` turns it red; `suffix` renders a unit (e.g. "€") inside the box. */
export const TextInput = forwardRef<HTMLInputElement, Omit<InputHTMLAttributes<HTMLInputElement>, "prefix"> & { invalid?: boolean; prefix?: ReactNode }>(function TextInput(
  { invalid, prefix, className = "", ...rest },
  ref,
) {
  if (!prefix) return <input ref={ref} className={inputClass(invalid, className)} {...rest} />;
  const pad = typeof prefix === "string" ? "pl-7" : "pl-9";
  return (
    <div className="relative">
      <span className="pointer-events-none absolute inset-y-0 left-2.5 flex items-center text-[16px] text-ink-muted">{prefix}</span>
      <input ref={ref} className={inputClass(invalid, `${pad} ${className}`)} {...rest} />
    </div>
  );
});

export function TextArea({ invalid, className = "", ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement> & { invalid?: boolean }) {
  return <textarea className={inputClass(invalid, `h-auto min-h-[64px] py-2 ${className}`)} {...rest} />;
}

/** Accessible on/off switch (role="switch"). */
export function Toggle({ checked, onChange, label, description, id, disabled, testId }: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: ReactNode;
  description?: ReactNode;
  id?: string;
  disabled?: boolean;
  testId?: string;
}) {
  const gen = useId();
  const tid = id ?? gen;
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <label htmlFor={tid} className="text-[14px] font-bold text-ink">
          {label}
        </label>
        {description && <p className="mt-0.5 text-[13px] text-ink-secondary">{description}</p>}
      </div>
      <button
        id={tid}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        data-testid={testId}
        onClick={() => onChange(!checked)}
        className={`relative mt-0.5 inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors disabled:opacity-60 ${
          checked ? "border-navy bg-navy" : "border-line-strong bg-line"
        }`}
      >
        <span className={`inline-block h-4 w-4 rounded-full bg-white transition-transform ${checked ? "translate-x-[17px]" : "translate-x-0.5"}`} />
      </button>
    </div>
  );
}

/** Status pill inside an input (portal "Not verified"): right side, 8 px inset. Wrap the input in `relative`. */
export function InputPill({ children, tone = "yellow" }: { children: ReactNode; tone?: "yellow" | "lime" | "grey" | "red" }) {
  const cls = { yellow: "bg-yellow text-ink", lime: "bg-lime-soft text-navy", grey: "bg-line text-ink-secondary", red: "bg-red-soft text-danger" }[tone];
  return <span className={`pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rounded-sm px-3 py-1 text-[14px] font-medium leading-4 ${cls}`}>{children}</span>;
}
