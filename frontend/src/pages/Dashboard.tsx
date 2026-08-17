import { useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, wsUrl } from "../api";
import { BatteryRing, formatRuntime, statusLabel } from "../components/BatteryRing";
import type { AppSettings, Policy, Sample, UpsStatus } from "../types";

export function Dashboard() {
  const [ups, setUps] = useState<UpsStatus>({});
  const [samples, setSamples] = useState<Sample[]>([]);
  const [events, setEvents] = useState<{ id: number; ts: string; level: string; message: string }[]>([]);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [busy, setBusy] = useState("");

  async function refresh() {
    const [u, h, e, p, s] = await Promise.all([
      api.get<UpsStatus>("/api/ups/status"),
      api.get<Sample[]>("/api/ups/history?hours=12"),
      api.get<{ id: number; ts: string; level: string; message: string }[]>("/api/events/recent"),
      api.get<Policy[]>("/api/policies"),
      api.get<AppSettings>("/api/settings"),
    ]);
    setUps(u);
    setSamples(h);
    setEvents(e);
    setPolicies(p);
    setSettings(s);
  }

  useEffect(() => {
    refresh().catch(console.error);
    const ws = new WebSocket(wsUrl());
    ws.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data);
        if (data.event === "ups.sample") setUps((prev) => ({ ...prev, ...data.payload }));
        if (data.event === "run.event") {
          setEvents((prev) => [...prev.slice(-80), { id: Date.now(), ts: new Date().toISOString(), ...data.payload }]);
        }
        if (data.event === "hello") setUps((prev) => ({ ...prev, ...(data.payload?.ups || {}) }));
      } catch {
        /* ignore */
      }
    };
    const poll = setInterval(() => refresh().catch(() => undefined), 8000);
    return () => {
      ws.close();
      clearInterval(poll);
    };
  }, []);

  const onBattery = Boolean(ups.on_battery);
  const mode = ups.power_mode || (onBattery ? "on_battery" : ups.status || "unknown");

  async function simulate(event: string, extra: Record<string, unknown> = {}) {
    setBusy(event);
    try {
      const res = await api.post<{ snapshot: UpsStatus }>("/api/ups/simulate", { event, ...extra });
      setUps((prev) => ({ ...prev, ...res.snapshot, source_kind: prev.source_kind || "simulator" }));
    } finally {
      setBusy("");
    }
  }

  const nextSteps = useMemo(() => {
    const charge = ups.charge ?? 100;
    const kind = onBattery ? "shed" : "restore";
    return policies
      .filter((p) => p.enabled && p.kind === kind)
      .flatMap((p) => p.steps.filter((s) => s.enabled).map((s) => ({ ...s, playbook: p.name })))
      .sort((a, b) => a.order - b.order)
      .slice(0, 5);
  }, [policies, onBattery, ups.charge]);

  const chart = samples.map((s) => ({
    t: new Date(s.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    charge: s.charge,
    watts: s.watts,
  }));

  return (
    <div className="p-6 space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-[11px] uppercase tracking-[0.22em] text-slate-500">Control room</div>
          <h1 className="text-2xl font-semibold">
            {ups.model || "EcoFlow Delta 3 Plus"}
            <span className="text-slate-500 font-normal text-base ml-3">{ups.device || ups.source_name}</span>
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <ModePill mode={mode} connected={ups.connected !== false} />
          {settings && !settings.live_actions && (
            <span className="text-xs px-2 py-1 rounded-full bg-amber/15 text-amber border border-amber/30">
              Dry-run mode — actions will not touch hosts
            </span>
          )}
          {settings?.engine_paused && (
            <span className="text-xs px-2 py-1 rounded-full bg-rose/15 text-rose">Engine paused</span>
          )}
          {ups.running_run_id ? (
            <button
              className="btn-danger"
              onClick={() => api.post("/api/runs/abort").then(() => refresh())}
            >
              Abort run #{ups.running_run_id}
            </button>
          ) : null}
        </div>
      </header>

      {ups.error && (
        <div className="card px-4 py-3 text-sm text-rose border-rose/30">UPS source error: {ups.error}</div>
      )}

      <div className="grid xl:grid-cols-[280px_1fr_320px] gap-5">
        <section className="card p-5">
          <BatteryRing charge={ups.charge} onBattery={onBattery} />
          <div className="text-center text-sm text-slate-400 -mt-2">{statusLabel(ups.status, onBattery)}</div>
          <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
            <Stat label="Runtime" value={formatRuntime(ups.runtime_sec)} />
            <Stat label="Load" value={ups.watts != null ? `${Math.round(ups.watts)} W` : "—"} />
            <Stat label="UPS load" value={ups.load_pct != null ? `${Math.round(ups.load_pct)}%` : "—"} />
            <Stat label="Input" value={ups.input_voltage != null ? `${Math.round(ups.input_voltage)} V` : "—"} />
          </div>
        </section>

        <section className="card p-5 min-h-[340px]">
          <div className="flex justify-between items-center mb-3">
            <h2 className="text-sm font-medium text-slate-300">Charge · last 12 hours</h2>
            <span className="text-[11px] text-slate-500 mono">{ups.status_raw || "—"}</span>
          </div>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chart}>
                <defs>
                  <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#5ce1c5" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="#5ce1c5" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="t" stroke="#334" tick={{ fill: "#7b8", fontSize: 11 }} minTickGap={40} />
                <YAxis domain={[0, 100]} stroke="#334" tick={{ fill: "#7b8", fontSize: 11 }} />
                <Tooltip
                  contentStyle={{ background: "#121821", border: "1px solid #243040", borderRadius: 8 }}
                />
                <Area type="monotone" dataKey="charge" stroke="#5ce1c5" fill="url(#g)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          {(ups.source_kind === "simulator" || !ups.source_kind) && (
            <div className="mt-4 flex flex-wrap gap-2">
              <button className="btn-warn" disabled={!!busy} onClick={() => simulate("pull_plug")}>
                Pull the plug
              </button>
              <button className="btn-primary" disabled={!!busy} onClick={() => simulate("restore_grid")}>
                Restore grid
              </button>
              <button className="btn-ghost" onClick={() => simulate("set_charge", { charge: 80 })}>
                80%
              </button>
              <button className="btn-ghost" onClick={() => simulate("set_charge", { charge: 40 })}>
                40%
              </button>
              <button className="btn-ghost" onClick={() => simulate("set_charge", { charge: 15 })}>
                15%
              </button>
            </div>
          )}
        </section>

        <section className="card p-5">
          <h2 className="text-sm font-medium text-slate-300 mb-3">
            {onBattery ? "Armed shed steps" : "Armed restore steps"}
          </h2>
          <ol className="space-y-3">
            {nextSteps.length === 0 && <li className="text-sm text-slate-500">No enabled playbook steps.</li>}
            {nextSteps.map((s) => (
              <li key={`${s.playbook}-${s.id}`} className="text-sm">
                <div className="flex justify-between gap-2">
                  <span>{s.name}</span>
                  <span className="mono text-[11px] text-slate-500">
                    {describeTrigger(s.trigger)}
                  </span>
                </div>
                <div className="text-[11px] text-slate-500">
                  {s.playbook} · {s.action.type}
                </div>
              </li>
            ))}
          </ol>
        </section>
      </div>

      <section className="card p-5">
        <h2 className="text-sm font-medium text-slate-300 mb-3">Live log</h2>
        <div className="mono text-xs space-y-1 max-h-64 overflow-auto">
          {events.length === 0 && <div className="text-slate-500">Waiting for events…</div>}
          {events.map((e) => (
            <div key={e.id} className="grid grid-cols-[150px_60px_1fr] gap-3">
              <span className="text-slate-500">{new Date(e.ts).toLocaleString()}</span>
              <span className={e.level === "error" ? "text-rose" : e.level === "warn" ? "text-amber" : "text-mint"}>
                {e.level}
              </span>
              <span>{e.message}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-ink-900/70 px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">{label}</div>
      <div className="text-base">{value}</div>
    </div>
  );
}

function ModePill({ mode, connected }: { mode: string; connected: boolean }) {
  const color = !connected
    ? "bg-rose/15 text-rose"
    : mode === "on_battery" || mode === "shedding"
      ? "bg-amber/15 text-amber"
      : "bg-mint/15 text-mint";
  return <span className={`text-xs px-2 py-1 rounded-full ${color}`}>{connected ? mode.replace("_", " ") : "disconnected"}</span>;
}

function describeTrigger(t: Policy["steps"][0]["trigger"]) {
  if (t.all) return "compound";
  if (t.type === "battery_lte") return `≤ ${t.value}%`;
  if (t.type === "battery_gte") return `≥ ${t.value}%`;
  if (t.type === "on_battery") return "on battery";
  if (t.type === "on_online") return "online";
  return t.type || "—";
}
