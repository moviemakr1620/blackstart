import { Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { ConfirmDialog } from "../confirm";
import { formatDateTime, formatTime } from "../time";
import type { Episode, Run, RunEvent } from "../types";

export function History() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [detail, setDetail] = useState<(Run & { events: RunEvent[] }) | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  async function load() {
    const [r, e] = await Promise.all([api.get<Run[]>("/api/runs"), api.get<Episode[]>("/api/episodes")]);
    setRuns(r);
    setEpisodes(e);
    if (r[0]) {
      const d = await api.get<Run & { events: RunEvent[] }>(`/api/runs/${r[0].id}`);
      setDetail(d);
    } else {
      setDetail(null);
    }
  }

  useEffect(() => {
    load().catch((e) => setErr(e instanceof Error ? e.message : "Failed to load"));
  }, []);

  async function openRun(id: number) {
    setDetail(await api.get<Run & { events: RunEvent[] }>(`/api/runs/${id}`));
  }

  async function clearHistory() {
    setBusy(true);
    setErr("");
    try {
      const res = await api.del<{ runs: number; events: number; episodes: number }>("/api/history");
      setConfirmClear(false);
      setMsg(
        `Cleared ${res.runs} run${res.runs === 1 ? "" : "s"}, ${res.events} log line${res.events === 1 ? "" : "s"}, ${res.episodes} episode${res.episodes === 1 ? "" : "s"}.`,
      );
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Clear failed");
    } finally {
      setBusy(false);
    }
  }

  const openEpisode = episodes.some((e) => e.status === "open");
  const empty = runs.length === 0 && episodes.length === 0;

  return (
    <div className="p-6 space-y-6">
      <header className="flex flex-wrap justify-between gap-3">
        <div>
          <div className="text-[11px] uppercase tracking-[0.22em] text-slate-500">Outages and executions</div>
          <h1 className="text-2xl font-semibold">History</h1>
          <p className="text-sm text-slate-500 mt-1">
            Dry runs, live runs, and real outages land here. Click a run on the left to see every step.
          </p>
        </div>
        <button className="btn-danger" onClick={() => setConfirmClear(true)} disabled={busy || empty}>
          <Trash2 size={14} /> Clear history
        </button>
      </header>
      {msg && <div className="text-sm text-mint">{msg}</div>}
      {err && <div className="text-sm text-rose">{err}</div>}

      <section className="card p-5">
        <h2 className="font-medium mb-3">Outage episodes</h2>
        <div className="space-y-2 text-sm">
          {episodes.length === 0 && (
            <div className="text-slate-500">
              No real (or simulated) outages yet. An episode starts when the UPS goes on battery. To rehearse, enable
              the EcoFlow simulator in Settings and use Pull the plug on the Dashboard.
            </div>
          )}
          {episodes.map((e) => (
            <div key={e.id} className="flex justify-between border-b border-white/5 py-2">
              <div>
                #{e.id} · {e.cause} · {e.status}
              </div>
              <div className="text-slate-400">
                {formatDateTime(e.started_at)}
                {e.min_charge != null && <span className="ml-3">min {Math.round(e.min_charge)}%</span>}
              </div>
            </div>
          ))}
        </div>
      </section>

      <div className="grid lg:grid-cols-2 gap-5">
        <section className="card overflow-hidden">
          {runs.length === 0 ? (
            <div className="p-5 text-sm text-slate-500">
              No runs yet. Open Playbooks and click Dry run — a window will show each step, and the log will appear
              here.
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-[11px] uppercase tracking-[0.16em] text-slate-500 bg-ink-900/60">
                <tr>
                  <th className="px-4 py-3">Run</th>
                  <th>Kind</th>
                  <th>Status</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr
                    key={r.id}
                    className={`border-t border-white/5 cursor-pointer hover:bg-white/5 ${
                      detail?.id === r.id ? "bg-white/5" : ""
                    }`}
                    onClick={() => openRun(r.id)}
                  >
                    <td className="px-4 py-3">
                      #{r.id} {r.dry_run && <span className="text-amber text-xs">dry</span>}
                    </td>
                    <td>{r.kind}</td>
                    <td className={r.status === "error" ? "text-rose" : r.status === "ok" ? "text-mint" : ""}>
                      {r.status}
                    </td>
                    <td className="text-slate-400">{formatDateTime(r.started_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
        <section className="card p-5">
          <h2 className="font-medium mb-3">{detail ? `Run #${detail.id}` : "No run selected"}</h2>
          <div className="mono text-xs space-y-2 max-h-[480px] overflow-auto">
            {!detail && <div className="text-slate-500">Pick a run on the left.</div>}
            {detail?.events.map((e) => (
              <div key={e.id}>
                <span className="text-slate-500">{formatTime(e.ts, true)} </span>
                <span className={e.level === "error" ? "text-rose" : "text-mint"}>{e.level}</span> {e.message}
              </div>
            ))}
          </div>
        </section>
      </div>
      {confirmClear && (
        <ConfirmDialog
          title="Clear all history?"
          body={
            `This deletes every run log and outage episode (${runs.length} run${runs.length === 1 ? "" : "s"}, ${episodes.length} episode${episodes.length === 1 ? "" : "s"}). The dashboard UPS chart is kept.` +
            (openEpisode
              ? "\n\nAn outage is still marked open. Restore will no longer remember what this outage stopped."
              : "")
          }
          confirmLabel="Clear history"
          busy={busy}
          onCancel={() => setConfirmClear(false)}
          onConfirm={() => void clearHistory()}
        />
      )}
    </div>
  );
}
