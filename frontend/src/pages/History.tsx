import { useEffect, useState } from "react";
import { api } from "../api";
import type { Episode, Run, RunEvent } from "../types";

export function History() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [detail, setDetail] = useState<(Run & { events: RunEvent[] }) | null>(null);

  async function load() {
    const [r, e] = await Promise.all([api.get<Run[]>("/api/runs"), api.get<Episode[]>("/api/episodes")]);
    setRuns(r);
    setEpisodes(e);
  }

  useEffect(() => {
    load().catch(console.error);
  }, []);

  async function openRun(id: number) {
    setDetail(await api.get<Run & { events: RunEvent[] }>(`/api/runs/${id}`));
  }

  return (
    <div className="p-6 space-y-6">
      <header>
        <div className="text-[11px] uppercase tracking-[0.22em] text-slate-500">Outages and executions</div>
        <h1 className="text-2xl font-semibold">History</h1>
      </header>

      <section className="card p-5">
        <h2 className="font-medium mb-3">Outage episodes</h2>
        <div className="space-y-2 text-sm">
          {episodes.length === 0 && <div className="text-slate-500">No outages recorded yet. Use the simulator to create one.</div>}
          {episodes.map((e) => (
            <div key={e.id} className="flex justify-between border-b border-white/5 py-2">
              <div>
                #{e.id} · {e.cause} · {e.status}
              </div>
              <div className="text-slate-400">
                {new Date(e.started_at).toLocaleString()}
                {e.min_charge != null && <span className="ml-3">min {Math.round(e.min_charge)}%</span>}
              </div>
            </div>
          ))}
        </div>
      </section>

      <div className="grid lg:grid-cols-2 gap-5">
        <section className="card overflow-hidden">
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
                  className="border-t border-white/5 cursor-pointer hover:bg-white/5"
                  onClick={() => openRun(r.id)}
                >
                  <td className="px-4 py-3">
                    #{r.id} {r.dry_run && <span className="text-amber text-xs">dry</span>}
                  </td>
                  <td>{r.kind}</td>
                  <td className={r.status === "error" ? "text-rose" : r.status === "ok" ? "text-mint" : ""}>
                    {r.status}
                  </td>
                  <td className="text-slate-400">{new Date(r.started_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="card p-5">
          <h2 className="font-medium mb-3">{detail ? `Run #${detail.id}` : "Select a run"}</h2>
          <div className="mono text-xs space-y-2 max-h-[480px] overflow-auto">
            {detail?.events.map((e) => (
              <div key={e.id}>
                <span className="text-slate-500">{new Date(e.ts).toLocaleTimeString()} </span>
                <span className={e.level === "error" ? "text-rose" : "text-mint"}>{e.level}</span> {e.message}
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
