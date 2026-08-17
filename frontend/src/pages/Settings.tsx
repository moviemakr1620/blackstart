import { useEffect, useState } from "react";
import { api } from "../api";
import type { AppSettings, UpsSource } from "../types";

export function Settings() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [sources, setSources] = useState<UpsSource[]>([]);
  const [msg, setMsg] = useState("");
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const [src, setSrc] = useState({
    name: "PeaNUT",
    kind: "peanut" as UpsSource["kind"],
    base_url: "http://192.168.1.50:8080",
    host: "192.168.1.50",
    port: 3493,
    device_name: "ecoflow",
    username: "",
    password: "",
    poll_interval_sec: 5,
    enabled: true,
  });

  async function load() {
    const [s, u] = await Promise.all([
      api.get<AppSettings>("/api/settings"),
      api.get<UpsSource[]>("/api/ups/sources"),
    ]);
    setSettings(s);
    setSources(u);
  }

  useEffect(() => {
    load().catch(console.error);
  }, []);

  async function saveSettings(patch: Partial<AppSettings>) {
    if (!settings) return;
    const next = await api.put<AppSettings>("/api/settings", { ...settings, ...patch });
    setSettings(next);
    setMsg("Settings saved.");
  }

  async function addSource() {
    await api.post("/api/ups/sources", src);
    setMsg("UPS source saved. Disable the simulator and enable this one to go live.");
    await load();
  }

  async function enableSource(s: UpsSource) {
    await api.put(`/api/ups/sources/${s.id}`, { ...s, enabled: true, password: "" });
    await load();
  }

  async function testSource(id: number) {
    try {
      const res = await api.post<{ snapshot: { charge?: number; status?: string } }>(`/api/ups/sources/${id}/test`);
      setMsg(`OK — ${res.snapshot.status} ${res.snapshot.charge ?? ""}%`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Test failed");
    }
  }

  if (!settings) return <div className="p-6">Loading…</div>;

  return (
    <div className="p-6 space-y-6 max-w-4xl">
      <header>
        <div className="text-[11px] uppercase tracking-[0.22em] text-slate-500">Safety and sources</div>
        <h1 className="text-2xl font-semibold">Settings</h1>
      </header>
      {msg && <div className="text-sm text-mint">{msg}</div>}

      <section className="card p-5 space-y-4">
        <h2 className="font-medium">Safety</h2>
        <label className="flex items-center gap-3 text-sm normal-case tracking-normal">
          <input
            type="checkbox"
            checked={settings.live_actions}
            onChange={(e) => saveSettings({ live_actions: e.target.checked })}
          />
          <span>
            <strong className="text-rose">Enable live actions</strong>
            <div className="text-slate-500 text-xs">
              Off by default. When off, playbooks still evaluate and log, but Proxmox / Synology / Docker are not
              touched.
            </div>
          </span>
        </label>
        <label className="flex items-center gap-3 text-sm normal-case tracking-normal">
          <input
            type="checkbox"
            checked={settings.engine_paused}
            onChange={(e) => saveSettings({ engine_paused: e.target.checked })}
          />
          Pause the engine (stop evaluating playbooks)
        </label>
        <label className="flex items-center gap-3 text-sm normal-case tracking-normal">
          <input
            type="checkbox"
            checked={settings.protect_critical}
            onChange={(e) => saveSettings({ protect_critical: e.target.checked })}
          />
          Never stop targets marked critical (peanut, blackstart, DNS…)
        </label>
        <div className="grid md:grid-cols-2 gap-3">
          <div className="space-y-1">
            <label>Default shed hold (seconds)</label>
            <input
              className="w-full"
              type="number"
              value={settings.default_shed_hold_sec}
              onChange={(e) => saveSettings({ default_shed_hold_sec: Number(e.target.value) })}
            />
          </div>
          <div className="space-y-1">
            <label>Default restore hold (seconds)</label>
            <input
              className="w-full"
              type="number"
              value={settings.default_restore_hold_sec}
              onChange={(e) => saveSettings({ default_restore_hold_sec: Number(e.target.value) })}
            />
          </div>
        </div>
      </section>

      <section className="card p-5 space-y-4">
        <h2 className="font-medium">Notifications</h2>
        <div className="grid md:grid-cols-2 gap-3">
          <div className="space-y-1">
            <label>Kind</label>
            <select
              className="w-full"
              value={settings.notify_kind}
              onChange={(e) => saveSettings({ notify_kind: e.target.value })}
            >
              <option value="none">none</option>
              <option value="ntfy">ntfy</option>
              <option value="gotify">gotify</option>
              <option value="webhook">webhook</option>
            </select>
          </div>
          <div className="space-y-1">
            <label>URL</label>
            <input
              className="w-full"
              value={settings.notify_url}
              onChange={(e) => setSettings({ ...settings, notify_url: e.target.value })}
              onBlur={() => saveSettings({ notify_url: settings.notify_url })}
            />
          </div>
          <div className="space-y-1">
            <label>ntfy topic</label>
            <input
              className="w-full"
              value={settings.ntfy_topic}
              onChange={(e) => setSettings({ ...settings, ntfy_topic: e.target.value })}
              onBlur={() => saveSettings({ ntfy_topic: settings.ntfy_topic })}
            />
          </div>
          <div className="space-y-1">
            <label>Gotify token</label>
            <input
              className="w-full"
              type="password"
              value={settings.gotify_token}
              onChange={(e) => setSettings({ ...settings, gotify_token: e.target.value })}
              onBlur={() => saveSettings({ gotify_token: settings.gotify_token })}
            />
          </div>
        </div>
      </section>

      <section className="card p-5 space-y-4">
        <h2 className="font-medium">UPS sources</h2>
        <p className="text-sm text-slate-500">
          PeaNUT is already talking to the EcoFlow over USB HID / NUT. Point Blackstart at PeaNUT&apos;s HTTP API, or
          skip it and talk to upsd on port 3493. Only one source is enabled at a time.
        </p>
        <div className="space-y-2">
          {sources.map((s) => (
            <div key={s.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-ink-900/70 px-3 py-2">
              <div>
                <div className="text-sm">
                  {s.name} <span className="text-slate-500">({s.kind})</span>
                  {s.enabled && <span className="text-mint text-xs ml-2">active</span>}
                </div>
                <div className="text-[11px] text-slate-500 mono">
                  {s.base_url || `${s.host}:${s.port}`} · {s.device_name}
                </div>
              </div>
              <div className="flex gap-2">
                <button className="btn-ghost" onClick={() => testSource(s.id)}>
                  Test
                </button>
                {!s.enabled && (
                  <button className="btn-primary" onClick={() => enableSource(s)}>
                    Use this
                  </button>
                )}
                <button
                  className="btn-danger"
                  onClick={async () => {
                    await api.del(`/api/ups/sources/${s.id}`);
                    await load();
                  }}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
        <div className="grid md:grid-cols-3 gap-3">
          <div className="space-y-1">
            <label>Name</label>
            <input className="w-full" value={src.name} onChange={(e) => setSrc({ ...src, name: e.target.value })} />
          </div>
          <div className="space-y-1">
            <label>Kind</label>
            <select className="w-full" value={src.kind} onChange={(e) => setSrc({ ...src, kind: e.target.value as UpsSource["kind"] })}>
              <option value="peanut">peanut</option>
              <option value="nut">nut</option>
              <option value="simulator">simulator</option>
            </select>
          </div>
          <div className="space-y-1">
            <label>Device name</label>
            <input className="w-full" value={src.device_name} onChange={(e) => setSrc({ ...src, device_name: e.target.value })} />
          </div>
          <div className="space-y-1 md:col-span-2">
            <label>PeaNUT URL or NUT host</label>
            <input
              className="w-full"
              value={src.kind === "peanut" ? src.base_url : src.host}
              onChange={(e) =>
                src.kind === "peanut" ? setSrc({ ...src, base_url: e.target.value }) : setSrc({ ...src, host: e.target.value })
              }
            />
          </div>
          <div className="space-y-1">
            <label>Port</label>
            <input className="w-full" type="number" value={src.port} onChange={(e) => setSrc({ ...src, port: Number(e.target.value) })} />
          </div>
          <div className="space-y-1">
            <label>Username</label>
            <input className="w-full" value={src.username} onChange={(e) => setSrc({ ...src, username: e.target.value })} />
          </div>
          <div className="space-y-1">
            <label>Password</label>
            <input className="w-full" type="password" value={src.password} onChange={(e) => setSrc({ ...src, password: e.target.value })} />
          </div>
        </div>
        <button className="btn-primary" onClick={addSource}>
          Add UPS source
        </button>
      </section>

      <section className="card p-5 space-y-3">
        <h2 className="font-medium">Change password</h2>
        <div className="grid md:grid-cols-2 gap-3">
          <input
            type="password"
            placeholder="Current"
            value={pw.current_password}
            onChange={(e) => setPw({ ...pw, current_password: e.target.value })}
          />
          <input
            type="password"
            placeholder="New (8+ chars)"
            value={pw.new_password}
            onChange={(e) => setPw({ ...pw, new_password: e.target.value })}
          />
        </div>
        <button
          className="btn-ghost"
          onClick={async () => {
            await api.post("/api/auth/password", pw);
            setMsg("Password changed.");
          }}
        >
          Update password
        </button>
      </section>
    </div>
  );
}
