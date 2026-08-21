export function ConfirmDialog({
  title,
  body,
  confirmLabel = "Delete",
  danger = true,
  busy,
  onCancel,
  onConfirm,
}: {
  title: string;
  body: string;
  confirmLabel?: string;
  danger?: boolean;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="fixed inset-0 z-40 bg-black/60 grid place-items-center p-4" onClick={onCancel}>
      <div className="card w-full max-w-lg p-5 space-y-4" onClick={(e) => e.stopPropagation()}>
        <div>
          <div className={`text-[11px] uppercase tracking-[0.16em] ${danger ? "text-rose" : "text-amber"}`}>
            Confirm
          </div>
          <h2 className="font-medium mt-1">{title}</h2>
        </div>
        <p className="text-sm text-slate-300 whitespace-pre-wrap">{body}</p>
        <div className="flex justify-end gap-2">
          <button className="btn-ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button className={danger ? "btn-danger" : "btn-warn"} onClick={onConfirm} disabled={busy}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
