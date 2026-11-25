import { useState, type ReactNode } from "react";

type Props = {
  label: ReactNode;
  saved?: boolean;
  cleared?: boolean;
  onClear?: () => void;
  onUndo?: () => void;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  multiline?: boolean;
  rows?: number;
  type?: "text" | "password";
  autoComplete?: string;
  className?: string;
  hint?: ReactNode;
  monospaced?: boolean;
};

/** Write-only secret / kept-value field: blank means keep, Remove marks a clear. */
export function KeptField({
  label,
  saved = false,
  cleared = false,
  onClear,
  onUndo,
  value,
  onChange,
  placeholder,
  multiline,
  rows = 3,
  type = "password",
  autoComplete = "new-password",
  className = "",
  hint,
  monospaced,
}: Props) {
  const [replace, setReplace] = useState(false);
  const showInput = !saved || cleared || replace || Boolean(value);

  const inputCls = `w-full ${monospaced ? "mono text-xs" : ""}`;

  return (
    <div className={`space-y-1 ${className}`}>
      <label className="flex items-center gap-1.5 flex-wrap">{label}</label>
      {cleared ? (
        <div className="flex items-center justify-between gap-2 rounded-lg border border-amber/40 bg-amber/10 px-3 py-2 text-sm">
          <span className="text-amber">Will be removed when you save</span>
          {onUndo && (
            <button type="button" className="text-xs text-slate-200 hover:underline" onClick={onUndo}>
              Undo
            </button>
          )}
        </div>
      ) : (
        <>
          {saved && !value && !replace && (
            <div className="flex items-center justify-between gap-2 rounded-lg border border-ink-600 bg-ink-900 px-3 py-2 text-sm">
              <span className="text-slate-400">
                Saved <span className="tracking-widest text-slate-300">•••</span>
              </span>
              <span className="flex gap-3 shrink-0">
                <button
                  type="button"
                  className="text-xs text-slate-300 hover:underline"
                  onClick={() => setReplace(true)}
                >
                  Replace
                </button>
                {onClear && (
                  <button type="button" className="text-xs text-rose hover:underline" onClick={onClear}>
                    Remove
                  </button>
                )}
              </span>
            </div>
          )}
          {showInput && (
            <>
              {multiline ? (
                <textarea
                  className={inputCls}
                  rows={rows}
                  value={value}
                  placeholder={placeholder}
                  onChange={(e) => onChange(e.target.value)}
                  autoComplete={autoComplete}
                />
              ) : (
                <input
                  className={inputCls}
                  type={type}
                  value={value}
                  placeholder={placeholder}
                  onChange={(e) => onChange(e.target.value)}
                  autoComplete={autoComplete}
                />
              )}
              {saved && (replace || value) && onClear && (
                <div className="flex justify-end">
                  <button
                    type="button"
                    className="text-[11px] text-rose hover:underline"
                    onClick={() => {
                      onChange("");
                      setReplace(false);
                      onClear();
                    }}
                  >
                    Remove instead
                  </button>
                </div>
              )}
            </>
          )}
        </>
      )}
      {hint}
    </div>
  );
}
