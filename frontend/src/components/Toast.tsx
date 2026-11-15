// Toasts (DESIGN.md §8). Call `toast.error(err)` / `toast.success(title)` from anywhere (no hook needed).
// Error toasts always show message + code pill + copyable request_id. Mount <Toaster /> once (App.tsx).
import { useSyncExternalStore } from "react";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { toApiError } from "../api/client";
import { CopyChip } from "./CopyChip";

export type ToastKind = "error" | "success" | "info";
export type ToastItem = {
  id: number;
  kind: ToastKind;
  title: string;
  message?: string;
  code?: string;
  request_id?: string | null;
};

let items: ToastItem[] = [];
let nextId = 1;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

function push(t: Omit<ToastItem, "id">, ttlMs: number): number {
  const id = nextId++;
  // De-duplicate identical errors fired by several queries at once.
  if (items.some((x) => x.kind === t.kind && x.title === t.title && x.code === t.code && x.request_id === t.request_id)) return id;
  items = [...items.slice(-4), { ...t, id }];
  emit();
  if (ttlMs > 0) window.setTimeout(() => dismissToast(id), ttlMs);
  return id;
}

export function dismissToast(id: number): void {
  items = items.filter((t) => t.id !== id);
  emit();
}

export const toast = {
  /** Error toast from anything thrown (ApiError gets code + request_id). */
  error(err: unknown, title?: string) {
    const e = toApiError(err);
    return push({ kind: "error", title: title ?? e.message, message: title ? e.message : undefined, code: e.code, request_id: e.request_id }, 12000);
  },
  success(title: string, message?: string) {
    return push({ kind: "success", title, message }, 5000);
  },
  info(title: string, message?: string) {
    return push({ kind: "info", title, message }, 6000);
  },
};

function useToasts(): ToastItem[] {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => items,
    () => items,
  );
}

// Toasts look like portal notification stripes (DESIGN_VALIO §4.3) and sit below the sticky header.
const kindStyle: Record<ToastKind, { box: string; icon: JSX.Element }> = {
  error: { box: "bg-red-soft", icon: <AlertTriangle className="h-5 w-5 text-red-strong" strokeWidth={1.75} /> },
  success: { box: "bg-success-bg", icon: <CheckCircle2 className="h-5 w-5 text-success" strokeWidth={1.75} /> },
  info: { box: "bg-primary-soft", icon: <Info className="h-5 w-5 text-navy" strokeWidth={1.75} /> },
};

export function Toaster() {
  const list = useToasts();
  if (list.length === 0) return null;
  return (
    <div
      className="pointer-events-none fixed inset-x-4 top-[112px] z-50 flex flex-col items-center gap-2 lg:inset-x-auto lg:right-6 lg:top-[136px] lg:items-end"
      aria-live="polite"
    >
      {list.map((t) => (
        <div
          key={t.id}
          role={t.kind === "error" ? "alert" : "status"}
          className={`pointer-events-auto w-full rounded-[3px] px-4 py-3 shadow-popover lg:w-[400px] ${kindStyle[t.kind].box}`}
        >
          <div className="flex items-start gap-3">
            <div className="shrink-0">{kindStyle[t.kind].icon}</div>
            <div className="min-w-0 flex-1">
              <p className="text-[14px] font-bold leading-5 text-ink">{t.title}</p>
              {t.message && <p className="mt-0.5 text-[13px] leading-5 text-ink">{t.message}</p>}
              {(t.code || t.request_id) && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  {t.code && <span className="font-mono-code rounded-sm bg-white/50 px-1.5 py-0.5 text-[11px] font-medium text-danger">{t.code}</span>}
                  {t.request_id && <CopyChip value={t.request_id} />}
                </div>
              )}
            </div>
            <button
              type="button"
              onClick={() => dismissToast(t.id)}
              className="shrink-0 rounded-full p-0.5 text-ink hover:bg-white/60"
              aria-label="Dismiss"
            >
              <X className="h-4 w-4" strokeWidth={1.75} />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
