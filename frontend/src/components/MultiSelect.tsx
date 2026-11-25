import { Check, ChevronDown, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

export type MultiSelectOption = {
  value: string;
  label: string;
  group?: string;
};

export function MultiSelect({
  options,
  values,
  onChange,
  placeholder = "— pick one or more —",
  disabled = false,
  searchPlaceholder = "Search…",
  emptyText = "Nothing matches",
}: {
  options: MultiSelectOption[];
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
  searchPlaceholder?: string;
  emptyText?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const root = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const selected = useMemo(() => new Set(values.map(String)), [values]);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  useEffect(() => {
    if (open) {
      setQuery("");
      window.setTimeout(() => searchRef.current?.focus(), 0);
    }
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (o) =>
        o.label.toLowerCase().includes(q) ||
        (o.group || "").toLowerCase().includes(q) ||
        o.value.toLowerCase().includes(q),
    );
  }, [options, query]);

  const groups = useMemo(() => {
    const map = new Map<string, MultiSelectOption[]>();
    for (const o of filtered) {
      const key = o.group || "";
      const list = map.get(key);
      if (list) list.push(o);
      else map.set(key, [o]);
    }
    return [...map.entries()];
  }, [filtered]);

  const selectedOptions = options.filter((o) => selected.has(o.value));
  const visibleIds = filtered.map((o) => o.value);
  const allVisibleOn = visibleIds.length > 0 && visibleIds.every((v) => selected.has(v));

  function toggle(value: string) {
    if (selected.has(value)) onChange(values.filter((v) => String(v) !== value));
    else onChange([...values, value]);
  }

  function selectAllVisible() {
    const next = new Set(values.map(String));
    for (const v of visibleIds) next.add(v);
    onChange([...next]);
  }

  function clearVisible() {
    const drop = new Set(visibleIds);
    onChange(values.filter((v) => !drop.has(String(v))));
  }

  return (
    <div className="relative" ref={root}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className="w-full min-h-[42px] flex items-center justify-between gap-2 text-left bg-ink-900 border border-ink-600 rounded-lg px-3 py-2 text-sm text-slate-100 outline-none focus:border-mint/70 focus:ring-1 focus:ring-mint/40 disabled:opacity-40"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className={selectedOptions.length ? "text-slate-100" : "text-slate-500"}>
          {selectedOptions.length === 0
            ? placeholder
            : selectedOptions.length === 1
              ? selectedOptions[0].label
              : `${selectedOptions.length} selected`}
        </span>
        <ChevronDown size={14} className={`shrink-0 text-slate-500 ${open ? "rotate-180" : ""}`} />
      </button>

      {selectedOptions.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2">
          {selectedOptions.map((o) => (
            <span
              key={o.value}
              className="inline-flex items-center gap-1 max-w-full rounded-full bg-mint/10 text-mint border border-mint/25 px-2 py-0.5 text-xs"
            >
              <span className="truncate">{o.label}</span>
              <button
                type="button"
                className="shrink-0 hover:text-white"
                aria-label={`Remove ${o.label}`}
                onClick={(e) => {
                  e.stopPropagation();
                  toggle(o.value);
                }}
              >
                <X size={11} />
              </button>
            </span>
          ))}
        </div>
      )}

      {open && (
        <div className="absolute z-30 mt-1 w-full rounded-xl border border-white/10 bg-ink-900 shadow-2xl shadow-black/50 overflow-hidden sm:max-h-[min(24rem,70vh)] max-sm:fixed max-sm:inset-x-3 max-sm:bottom-3 max-sm:top-auto max-sm:mt-0 max-sm:max-h-[70vh] flex flex-col">
          <div className="p-2 border-b border-white/5 space-y-2">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
              <input
                ref={searchRef}
                className="w-full !pl-8 !py-1.5"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={searchPlaceholder}
                aria-label="Search"
              />
            </div>
            <div className="flex justify-between text-[11px] px-1">
              <button type="button" className="text-mint hover:underline" onClick={allVisibleOn ? clearVisible : selectAllVisible}>
                {allVisibleOn ? "Clear shown" : "Select all shown"}
              </button>
              <button
                type="button"
                className="text-slate-400 hover:underline"
                onClick={() => onChange([])}
                disabled={values.length === 0}
              >
                Clear all
              </button>
            </div>
          </div>
          <div className="overflow-y-auto overscroll-contain p-1 min-h-0" role="listbox" aria-multiselectable="true">
            {filtered.length === 0 && <div className="px-3 py-4 text-sm text-slate-500">{emptyText}</div>}
            {groups.map(([group, items]) => (
              <div key={group || "default"}>
                {group && (
                  <div className="px-2.5 pt-2 pb-1 text-[10px] uppercase tracking-[0.16em] text-slate-500">{group}</div>
                )}
                {items.map((o) => {
                  const on = selected.has(o.value);
                  return (
                    <label
                      key={o.value}
                      className={`flex items-center gap-2 px-2.5 py-2 rounded-lg text-sm normal-case tracking-normal cursor-pointer ${
                        on ? "bg-mint/10 text-slate-100" : "text-slate-200 hover:bg-white/5"
                      }`}
                    >
                      <span
                        className={`h-4 w-4 rounded border grid place-items-center shrink-0 ${
                          on ? "bg-mint border-mint text-ink-950" : "border-ink-500 bg-ink-900"
                        }`}
                      >
                        {on && <Check size={11} strokeWidth={3} />}
                      </span>
                      <input
                        type="checkbox"
                        className="sr-only"
                        checked={on}
                        onChange={() => toggle(o.value)}
                      />
                      <span className="truncate">{o.label}</span>
                    </label>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
