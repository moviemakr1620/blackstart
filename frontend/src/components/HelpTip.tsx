import { Info, X } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

type Props = {
  title: string;
  children: ReactNode;
};

function isMobile() {
  return typeof window !== "undefined" && window.innerWidth < 640;
}

export function HelpTip({ title, children }: Props) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0, flip: false, mobile: false });
  const btnRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  function place() {
    const mobile = isMobile();
    if (!btnRef.current || mobile) {
      setPos({ top: 0, left: 0, flip: false, mobile });
      return;
    }
    const rect = btnRef.current.getBoundingClientRect();
    const width = Math.min(360, window.innerWidth - 24);
    let left = rect.left;
    if (left + width > window.innerWidth - 12) left = window.innerWidth - width - 12;
    if (left < 12) left = 12;
    const spaceBelow = window.innerHeight - rect.bottom - 16;
    const flip = spaceBelow < 240 && rect.top > spaceBelow;
    setPos({
      top: flip ? rect.top - 8 : rect.bottom + 8,
      left,
      flip,
      mobile: false,
    });
  }

  useEffect(() => {
    if (!open) return;
    place();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    function onDoc(e: MouseEvent) {
      const t = e.target as Node;
      if (btnRef.current?.contains(t) || panelRef.current?.contains(t)) return;
      setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDoc);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDoc);
      window.removeEventListener("resize", place);
    };
  }, [open]);

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className="inline-flex h-5 w-5 items-center justify-center rounded-full text-slate-400 hover:text-mint hover:bg-mint/10 align-middle"
        aria-label={`Help: ${title}`}
        aria-expanded={open}
        aria-controls={open ? titleId : undefined}
        onClick={() => setOpen((v) => !v)}
      >
        <Info size={14} />
      </button>
      {open &&
        createPortal(
          pos.mobile ? (
            <div className="fixed inset-0 z-[60] flex items-end sm:hidden">
              <div className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />
              <div
                ref={panelRef}
                role="dialog"
                aria-labelledby={titleId}
                className="relative z-10 w-full max-h-[80vh] overflow-y-auto rounded-t-2xl border border-white/10 bg-ink-900 p-4 pb-6 shadow-2xl"
              >
                <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-white/20" />
                <div className="flex items-start justify-between gap-3 mb-2">
                  <h3 id={titleId} className="font-medium text-slate-100">
                    {title}
                  </h3>
                  <button type="button" className="text-slate-400 hover:text-white" aria-label="Close help" onClick={() => setOpen(false)}>
                    <X size={16} />
                  </button>
                </div>
                <div className="help-tip-body text-sm text-slate-300 space-y-2 leading-relaxed">{children}</div>
              </div>
            </div>
          ) : (
            <div
              ref={panelRef}
              role="dialog"
              aria-labelledby={titleId}
              className="fixed z-[60] w-[min(360px,calc(100vw-24px))] rounded-xl border border-white/10 bg-ink-900 p-4 shadow-2xl shadow-black/50"
              style={{
                top: pos.top,
                left: pos.left,
                transform: pos.flip ? "translateY(-100%)" : undefined,
              }}
            >
              <div className="flex items-start justify-between gap-3 mb-2">
                <h3 id={titleId} className="font-medium text-slate-100">
                  {title}
                </h3>
                <button type="button" className="text-slate-400 hover:text-white" aria-label="Close help" onClick={() => setOpen(false)}>
                  <X size={16} />
                </button>
              </div>
              <div className="help-tip-body text-sm text-slate-300 space-y-2 leading-relaxed">{children}</div>
            </div>
          ),
          document.body,
        )}
    </>
  );
}
