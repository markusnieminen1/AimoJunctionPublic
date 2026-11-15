import type { ReactNode } from "react";
import { Button } from "./Button";
import { ErrorPanel } from "./ErrorPanel";
import { Modal } from "./Modal";

/** Small confirm dialog. `tone="danger"` renders the solid red `danger` confirm button (kill switch, rotate secret). */
export function ConfirmDialog({ open, title, children, confirmLabel, tone = "primary", busy, error, onConfirm, onCancel }: {
  open: boolean;
  title: ReactNode;
  children: ReactNode;
  confirmLabel: string;
  tone?: "primary" | "danger";
  busy?: boolean;
  error?: unknown;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal
      open={open}
      onClose={() => !busy && onCancel()}
      title={title}
      size="sm"
      footer={
        <>
          <Button variant="outline" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button variant={tone === "danger" ? "danger" : "primary"} loading={busy} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-[15px] leading-6 text-ink">
        {children}
        {error != null && <ErrorPanel error={error} />}
      </div>
    </Modal>
  );
}
