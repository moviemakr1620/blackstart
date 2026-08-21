import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { formatTime } from "./time";
import type { Run, RunEvent } from "./types";

type Toast = { id: number; text: string; kind: "ok" | "err" | "info" };
type RunWatch = { runId: number; title: string } | null;
type ResultBox = { title: string; body: string; ok: boolean } | null;

type Feedback = {
  toast: (text: string, kind?: Toast["kind"]) => void;
  watchRun: (runId: number, title: string) => void;
  showResult: (title: string, body: unknown, ok?: boolean) => void;
};

const Ctx = createContext<Feedback | null>(null);

export function useFeedback() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useFeedback requires FeedbackProvider");
  return ctx;
}

export function FeedbackProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [runWatch, setRunWatch] = useState<RunWatch>(null);
  const [result, setResult] = useState<ResultBox>(null);

  const apiValue = useMemo<Feedback>(
    () => ({
      toast(text, kind = "ok") {
        const id = Date.now() + Math.random();
        setToasts((prev) => [...prev.slice(-4), { id, text, kind }]);
        window.setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4500);
      },
      watchRun(runId, title) {
        setRunWatch({ runId, title });
      },
      showResult(title, body, ok = true) {
        const text = typeof body === "string" ? body : JSON.stringify(body, null, 2);
        setResult({ title, body: text, ok });
      },
    }),
    [],
  );

  return (
    <Ctx.Provider value={apiValue}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 space-y-2 w-80">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`card px-3 py-2 text-sm shadow-glow ${
              t.kind === "err" ? "border-rose/40 text-rose" : t.kind === "info" ? "text-slate-200" : "text-mint"
            }`}
          >
            {t.text}
          </div>
        ))}
      </div>
      {runWatch && <RunPanel runId={runWatch.runId} title={runWatch.title} onClose={() => setRunWatch(null)} />}
      {result && (
        <Modal title={result.title} onClose={() => setResult(null)}>
          <pre className={`mono text-xs whitespace-pre-wrap ${result.ok ? "text-slate-200" : "text-rose"}`}>
            {result.body}
          </pre>
        </Modal>
      )}
    </Ctx.Provider>
  );
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-40 bg-black/60 grid place-items-center p-4" onClick={onClose}>
      <div className="card w-full max-w-xl max-h-[80vh] overflow-auto p-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-start gap-3 mb-3">
          <h2 className="font-medium">{title}</h2>
          <button className="btn-ghost" onClick={onClose}>
            Close
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function RunPanel({ runId, title, onClose }: { runId: number; title: string; onClose: () => void }) {
  const [detail, setDetail] = useState<(Run & { events: RunEvent[] }) | null>(null);

  usePoll(runId, setDetail);

  const status = detail?.status || "running";
  return (
    <div className="fixed inset-0 z-40 bg-black/60 grid place-items-center p-4" onClick={onClose}>
      <div className="card w-full max-w-2xl max-h-[85vh] overflow-auto p-5 space-y-3" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-start gap-3">
          <div>
            <div className="text-[11px] uppercase tracking-[0.16em] text-slate-500">Run #{runId}</div>
            <h2 className="font-medium">{title}</h2>
          </div>
          <button className="btn-ghost" onClick={onClose}>
            Close
          </button>
        </div>
        <div
          className={`text-sm px-3 py-2 rounded-xl ${
            status === "ok"
              ? "bg-mint/10 text-mint"
              : status === "error" || status === "aborted"
                ? "bg-rose/10 text-rose"
                : "bg-amber/10 text-amber"
          }`}
        >
          {status === "running" && "Running…"}
          {status === "ok" && (detail?.dry_run ? "Finished (dry run — nothing was touched)" : "Finished")}
          {status === "error" && "Finished with errors"}
          {status === "aborted" && "Aborted"}
          {detail?.dry_run && status === "ok" && (
            <div className="text-xs text-slate-400 mt-1">
              Live actions may also be off in Settings. Dry run only writes this log.
            </div>
          )}
        </div>
        <div className="mono text-xs space-y-2 max-h-[50vh] overflow-auto">
          {!detail && <div className="text-slate-500">Loading run…</div>}
          {detail?.events.map((e) => (
            <div key={e.id} className="grid grid-cols-[70px_50px_1fr] gap-2">
              <span className="text-slate-500">{formatTime(e.ts, true)}</span>
              <span className={e.level === "error" ? "text-rose" : e.level === "warn" ? "text-amber" : "text-mint"}>
                {e.level}
              </span>
              <span>{e.message}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function usePoll(runId: number, setDetail: (d: Run & { events: RunEvent[] }) => void) {
  useEffect(() => {
    let stop = false;
    async function tick() {
      try {
        const d = await api.get<Run & { events: RunEvent[] }>(`/api/runs/${runId}`);
        if (!stop) setDetail(d);
        if (d.status === "running") window.setTimeout(tick, 800);
      } catch {
        if (!stop) window.setTimeout(tick, 1200);
      }
    }
    tick();
    return () => {
      stop = true;
    };
  }, [runId, setDetail]);
}
