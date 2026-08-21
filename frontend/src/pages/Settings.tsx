import { useEffect, useState } from "react";
import { api } from "../api";
import { COMMON_TIMEZONES, browserTimezone, listTimezones, setAppTimezone } from "../time";
import type { AppSettings, NotifyChannel, UpsSource } from "../types";

const emptySrc = {
  name: "PeaNUT",
  kind: "peanut" as UpsSource["kind"],
  base_url: "http://10.8.2.50:8080",
  host: "10.8.2.50",
  port: 3493,
  device_name: "",
  username: "",
  password: "",
  poll_interval_sec: 5,
  enabled: true,
};

export function Settings() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [sources, setSources] = useState<UpsSource[]>([]);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const [src, setSrc] = useState(emptySrc);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [channels, setChannels] = useState<NotifyChannel[]>([]);
  const [chForm, setChForm] = useState({
    name: "Discord",
    kind: "discord" as NotifyChannel["kind"],
    url: "",
    topic: "",
    token: "",
    enabled: true,
  });
  const [editingChannelId, setEditingChannelId] = useState<number | null>(null);

  async function load() {
    const [s, u, c] = await Promise.all([
      api.get<AppSettings>("/api/settings"),
      api.get<UpsSource[]>("/api/ups/sources"),
      api.get<NotifyChannel[]>("/api/notify-channels"),
    ]);
    setSettings(s);
    setSources(u);
    setChannels(c);
  }

  useEffect(() => {
    load().catch((e) => setErr(e instanceof Error ? e.message : "Failed to load"));
  }, []);

  async function saveSettings(patch: Partial<AppSettings>) {
    if (!settings) return;
    try {
      const next = await api.put<AppSettings>("/api/settings", { ...settings, ...patch });
      setSettings(next);
      setMsg("Settings saved.");
      setErr("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Save failed");
    }
  }

  function editSource(s: UpsSource) {
    setEditingId(s.id);
    setSrc({
      name: s.name,
      kind: s.kind,
      base_url: s.base_url || "",
      host: s.host || "",
      port: s.port || (s.kind === "peanut" ? 8080 : 3493),
      device_name: s.device_name || "",
      username: s.username || "",
      password: "",
      poll_interval_sec: s.poll_interval_sec || 5,
      enabled: s.enabled,
    });
    setMsg(`Editing “${s.name}”. Leave password blank to keep the current one.`);
    setErr("");
  }

  function resetForm() {
    setEditingId(null);
    setSrc(emptySrc);
  }

  async function saveSource() {
    setErr("");
    try {
      if (editingId) {
        await api.put(`/api/ups/sources/${editingId}`, src);
        setMsg("UPS source updated.");
      } else {
        await api.post("/api/ups/sources", src);
        setMsg("UPS source saved.");
      }
      resetForm();
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not save UPS source");
    }
  }

  async function enableSource(s: UpsSource, enabled: boolean) {
    setErr("");
    try {
      await api.put(`/api/ups/sources/${s.id}`, { ...s, enabled, password: "" });
      setMsg(enabled ? `${s.name} enabled.` : `${s.name} disabled.`);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not update source");
    }
  }

  async function setPrimary(s: UpsSource) {
    try {
      await api.put("/api/settings", { ...settings, primary_ups_id: s.id });
      setMsg(`${s.name} is the UPS playbooks watch.`);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not set primary UPS");
    }
  }

  async function testSource(id: number) {
    setErr("");
    try {
      const res = await api.post<{ snapshot: { charge?: number; status?: string; device?: string; model?: string } }>(
        `/api/ups/sources/${id}/test`,
      );
      const snap = res.snapshot;
      setMsg(
        `OK — ${snap.model || snap.device || ""} ${snap.status || ""} ${snap.charge != null ? `${Math.round(snap.charge)}%` : ""}`.trim(),
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Test failed");
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
      {err && <div className="text-sm text-rose">{err}</div>}

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
        <div className="space-y-1 max-w-md">
          <label>Timezone</label>
          <select
            className="w-full"
            value={settings.timezone || ""}
            onChange={(e) => {
              const timezone = e.target.value;
              setAppTimezone(timezone);
              saveSettings({ timezone });
            }}
          >
            <option value="">Browser local time ({browserTimezone()})</option>
            <optgroup label="Common">
              {COMMON_TIMEZONES.map((z) => (
                <option key={`c-${z}`} value={z}>
                  {z}
                </option>
              ))}
            </optgroup>
            <optgroup label="All">
              {listTimezones()
                .filter((z) => !COMMON_TIMEZONES.includes(z))
                .map((z) => (
                  <option key={z} value={z}>
                    {z}
                  </option>
                ))}
            </optgroup>
          </select>
          <p className="text-xs text-slate-500">Dashboard chart, live log, and history timestamps. Stored data stays UTC.</p>
        </div>
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
        <h2 className="font-medium">Notification channels</h2>
        <p className="text-sm text-slate-500">
          Add as many as you want — Discord, ntfy, Gotify, or a generic webhook. Playbook notify steps pick a channel
          (or all of them).
        </p>
        <div className="space-y-2">
          {channels.length === 0 && <div className="text-sm text-slate-500">No channels yet.</div>}
          {channels.map((ch) => (
            <div key={ch.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-ink-900/70 px-3 py-2">
              <div>
                <div className="text-sm">
                  {ch.name} <span className="text-slate-500">({ch.kind})</span>
                  {!ch.enabled && <span className="text-amber text-xs ml-2">off</span>}
                </div>
                <div className="text-[11px] text-slate-500 mono truncate max-w-md">{ch.url || ch.topic || "—"}</div>
              </div>
              <div className="flex gap-2">
                <button
                  className="btn-ghost"
                  onClick={() => {
                    setEditingChannelId(ch.id);
                    setChForm({
                      name: ch.name,
                      kind: ch.kind,
                      url: ch.url,
                      topic: ch.topic,
                      token: "",
                      enabled: ch.enabled,
                    });
                  }}
                >
                  Edit
                </button>
                <button
                  className="btn-ghost"
                  onClick={async () => {
                    try {
                      await api.post(`/api/notify-channels/${ch.id}/test`);
                      setMsg(`Test sent to ${ch.name}.`);
                      setErr("");
                    } catch (e) {
                      setErr(e instanceof Error ? e.message : "Test failed");
                    }
                  }}
                >
                  Test
                </button>
                <button
                  className="btn-danger"
                  onClick={async () => {
                    await api.del(`/api/notify-channels/${ch.id}`);
                    if (editingChannelId === ch.id) setEditingChannelId(null);
                    await load();
                  }}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
        <div className="text-sm text-slate-300">
          {editingChannelId ? `Editing channel #${editingChannelId}` : "Add a channel"}
        </div>
        <div className="grid md:grid-cols-3 gap-3">
          <div className="space-y-1">
            <label>Name</label>
            <input className="w-full" value={chForm.name} onChange={(e) => setChForm({ ...chForm, name: e.target.value })} />
          </div>
          <div className="space-y-1">
            <label>Kind</label>
            <select
              className="w-full"
              value={chForm.kind}
              onChange={(e) => setChForm({ ...chForm, kind: e.target.value as NotifyChannel["kind"] })}
            >
              <option value="discord">Discord webhook</option>
              <option value="ntfy">ntfy</option>
              <option value="gotify">Gotify</option>
              <option value="webhook">Generic webhook</option>
            </select>
          </div>
          <div className="space-y-1">
            <label>URL</label>
            <input
              className="w-full"
              value={chForm.url}
              placeholder="https://discord.com/api/webhooks/…"
              onChange={(e) => setChForm({ ...chForm, url: e.target.value })}
            />
          </div>
          {chForm.kind === "ntfy" && (
            <div className="space-y-1">
              <label>ntfy topic</label>
              <input className="w-full" value={chForm.topic} onChange={(e) => setChForm({ ...chForm, topic: e.target.value })} />
            </div>
          )}
          {chForm.kind === "gotify" && (
            <div className="space-y-1">
              <label>Token {editingChannelId ? "(blank = keep)" : ""}</label>
              <input className="w-full" type="password" value={chForm.token} onChange={(e) => setChForm({ ...chForm, token: e.target.value })} />
            </div>
          )}
          <label className="flex items-center gap-2 text-sm normal-case tracking-normal mt-6">
            <input type="checkbox" checked={chForm.enabled} onChange={(e) => setChForm({ ...chForm, enabled: e.target.checked })} />
            Enabled
          </label>
        </div>
        <div className="flex gap-2">
          <button
            className="btn-primary"
            onClick={async () => {
              try {
                if (editingChannelId) {
                  await api.put(`/api/notify-channels/${editingChannelId}`, chForm);
                  setMsg("Channel updated.");
                } else {
                  await api.post("/api/notify-channels", chForm);
                  setMsg("Channel added.");
                }
                setEditingChannelId(null);
                setChForm({ name: "Discord", kind: "discord", url: "", topic: "", token: "", enabled: true });
                setErr("");
                await load();
              } catch (e) {
                setErr(e instanceof Error ? e.message : "Could not save channel");
              }
            }}
          >
            {editingChannelId ? "Save channel" : "Add channel"}
          </button>
          {editingChannelId && (
            <button
              className="btn-ghost"
              onClick={() => {
                setEditingChannelId(null);
                setChForm({ name: "Discord", kind: "discord", url: "", topic: "", token: "", enabled: true });
              }}
            >
              Cancel
            </button>
          )}
        </div>
      </section>

      <section className="card p-5 space-y-4">
        <h2 className="font-medium">UPS sources</h2>
        <p className="text-sm text-slate-500">
          PeaNUT&apos;s web UI can show the EcoFlow while its <em>API</em> still requires the PeaNUT login. Save that
          username and password here. Device name can be left blank. Enable more than one UPS if you want. Each
          playbook picks its UPS. The dashboard switcher appears when two or more are on.
        </p>
        <div className="space-y-2">
          {sources.map((s) => (
            <div key={s.id} className="flex items-start gap-3 rounded-xl bg-ink-900/70 px-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="text-sm">
                  {s.name} <span className="text-slate-500">({s.kind})</span>
                  {s.enabled && <span className="text-mint text-xs ml-2">on</span>}
                  {s.username && <span className="text-slate-500 text-xs ml-2">user {s.username}</span>}
                  {s.has_password && <span className="text-slate-500 text-xs ml-1">· password set</span>}
                  {!s.username && s.kind === "peanut" && (
                    <span className="text-rose text-xs ml-2">no API login</span>
                  )}
                </div>
                <div className="text-[11px] text-slate-500 mono truncate">
                  {s.base_url || `${s.host}:${s.port}`} · {s.device_name || "(auto)"}
                </div>
              </div>
              <div className="flex flex-wrap gap-2 shrink-0 justify-end">
                <button className="btn-ghost" onClick={() => editSource(s)}>
                  Edit
                </button>
                <button className="btn-ghost" onClick={() => testSource(s.id)}>
                  Test
                </button>
                <button className="btn-ghost" onClick={() => enableSource(s, !s.enabled)}>
                  {s.enabled ? "Disable" : "Enable"}
                </button>
                <button
                  className="btn-danger"
                  onClick={async () => {
                    try {
                      await api.del(`/api/ups/sources/${s.id}`);
                      if (editingId === s.id) resetForm();
                      await load();
                    } catch (e) {
                      setErr(e instanceof Error ? e.message : "Delete failed");
                    }
                  }}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
        <div className="text-sm text-slate-300">{editingId ? `Editing source #${editingId}` : "Add a new source"}</div>
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
            <label>Device name (blank = auto)</label>
            <input className="w-full" value={src.device_name} onChange={(e) => setSrc({ ...src, device_name: e.target.value })} placeholder="leave blank to auto-detect" />
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
            <label>PeaNUT / NUT username</label>
            <input className="w-full" value={src.username} onChange={(e) => setSrc({ ...src, username: e.target.value })} autoComplete="off" />
          </div>
          <div className="space-y-1">
            <label>Password {editingId ? "(blank = keep)" : ""}</label>
            <input className="w-full" type="password" value={src.password} onChange={(e) => setSrc({ ...src, password: e.target.value })} autoComplete="new-password" />
          </div>
          <label className="flex items-center gap-2 text-sm normal-case tracking-normal mt-6">
            <input type="checkbox" checked={src.enabled} onChange={(e) => setSrc({ ...src, enabled: e.target.checked })} />
            Enable this source
          </label>
        </div>
        <div className="flex gap-2">
          <button className="btn-primary" onClick={saveSource}>
            {editingId ? "Save changes" : "Add UPS source"}
          </button>
          {editingId && (
            <button className="btn-ghost" onClick={resetForm}>
              Cancel
            </button>
          )}
        </div>
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
            try {
              await api.post("/api/auth/password", pw);
              setMsg("Password changed.");
              setErr("");
            } catch (e) {
              setErr(e instanceof Error ? e.message : "Password change failed");
            }
          }}
        >
          Update password
        </button>
      </section>
    </div>
  );
}
