import { useEffect, useState } from "react";
import { api } from "../api";
import type { Host, Target } from "../types";

const HOST_KINDS = ["proxmox", "synology", "docker", "ssh"] as const;
const TARGET_KINDS = ["vm", "lxc", "container", "stack", "nas", "host", "custom"];

export function Inventory() {
  const [hosts, setHosts] = useState<Host[]>([]);
  const [targets, setTargets] = useState<Target[]>([]);
  const [msg, setMsg] = useState("");
  const [hostForm, setHostForm] = useState({
    name: "",
    kind: "proxmox" as Host["kind"],
    address: "",
    port: 8006,
    token_id: "",
    token_secret: "",
    username: "",
    password: "",
    private_key: "",
  });
  const [targetForm, setTargetForm] = useState({
    name: "",
    kind: "vm",
    host_id: 0,
    external_id: "",
    critical: false,
    notes: "",
    mac: "",
    node: "",
  });

  async function load() {
    const [h, t] = await Promise.all([api.get<Host[]>("/api/hosts"), api.get<Target[]>("/api/targets")]);
    setHosts(h);
    setTargets(t);
    if (!targetForm.host_id && h[0]) setTargetForm((f) => ({ ...f, host_id: h[0].id }));
  }

  useEffect(() => {
    load().catch(console.error);
  }, []);

  async function addHost() {
    const creds: Record<string, string> = {};
    if (hostForm.kind === "proxmox") {
      creds.token_id = hostForm.token_id;
      creds.token_secret = hostForm.token_secret;
    } else {
      creds.username = hostForm.username;
      creds.password = hostForm.password;
      if (hostForm.private_key) creds.private_key = hostForm.private_key;
    }
    await api.post("/api/hosts", {
      name: hostForm.name,
      kind: hostForm.kind,
      enabled: true,
      address: hostForm.address,
      port: Number(hostForm.port),
      verify_tls: false,
      credentials: creds,
      extra: hostForm.kind === "docker" && hostForm.address.startsWith("unix") ? { socket: hostForm.address } : {},
    });
    setMsg("Host saved.");
    await load();
  }

  async function testHost(id: number) {
    try {
      const res = await api.post<{ ok: boolean }>(`/api/hosts/${id}/test`);
      setMsg(res.ok ? "Connection OK." : "Test failed.");
      await load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Test failed");
    }
  }

  async function discover(id: number) {
    try {
      await api.post(`/api/hosts/${id}/discover`);
      setMsg("Discover complete — new targets imported.");
      await load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Discover failed");
    }
  }

  async function addTarget() {
    await api.post("/api/targets", {
      name: targetForm.name,
      kind: targetForm.kind,
      host_id: targetForm.host_id || null,
      external_id: targetForm.external_id,
      critical: targetForm.critical,
      enabled: true,
      notes: targetForm.notes,
      extra: { mac: targetForm.mac, node: targetForm.node },
    });
    setMsg("Target saved.");
    await load();
  }

  async function toggleCritical(t: Target) {
    await api.put(`/api/targets/${t.id}`, { ...t, extra: t.extra, critical: !t.critical });
    await load();
  }

  async function removeTarget(id: number) {
    await api.del(`/api/targets/${id}`);
    await load();
  }

  async function act(id: number, action: string, dry = true) {
    try {
      const res = await api.post<{ result: unknown }>(`/api/targets/${id}/action?action_type=${action}&dry_run=${dry}`);
      setMsg(`${action}: ${JSON.stringify(res.result)}`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Action failed");
    }
  }

  return (
    <div className="p-6 space-y-6">
      <header>
        <div className="text-[11px] uppercase tracking-[0.22em] text-slate-500">Hosts and workloads</div>
        <h1 className="text-2xl font-semibold">Inventory</h1>
        <p className="text-sm text-slate-500 mt-1">
          Point Blackstart at Proxmox, Synology, Docker, and SSH. Discover VMs, LXCs, and compose stacks, then mark
          peanut / this app as critical so they never get shed.
        </p>
      </header>
      {msg && <div className="text-sm text-mint">{msg}</div>}

      <section className="card p-5 space-y-4">
        <h2 className="font-medium">Add host</h2>
        <div className="grid md:grid-cols-4 gap-3">
          <div className="space-y-1">
            <label>Name</label>
            <input className="w-full" value={hostForm.name} onChange={(e) => setHostForm({ ...hostForm, name: e.target.value })} />
          </div>
          <div className="space-y-1">
            <label>Kind</label>
            <select
              className="w-full"
              value={hostForm.kind}
              onChange={(e) => {
                const kind = e.target.value as Host["kind"];
                const port = kind === "proxmox" ? 8006 : kind === "synology" ? 5001 : kind === "docker" ? 2375 : 22;
                setHostForm({ ...hostForm, kind, port });
              }}
            >
              {HOST_KINDS.map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <label>Address / socket</label>
            <input
              className="w-full"
              placeholder="192.168.1.10 or unix:///var/run/docker.sock"
              value={hostForm.address}
              onChange={(e) => setHostForm({ ...hostForm, address: e.target.value })}
            />
          </div>
          <div className="space-y-1">
            <label>Port</label>
            <input
              className="w-full"
              type="number"
              value={hostForm.port}
              onChange={(e) => setHostForm({ ...hostForm, port: Number(e.target.value) })}
            />
          </div>
        </div>
        {hostForm.kind === "proxmox" ? (
          <div className="grid md:grid-cols-2 gap-3">
            <div className="space-y-1">
              <label>API token id (user@pam!name)</label>
              <input className="w-full" value={hostForm.token_id} onChange={(e) => setHostForm({ ...hostForm, token_id: e.target.value })} />
            </div>
            <div className="space-y-1">
              <label>API token secret</label>
              <input className="w-full" type="password" value={hostForm.token_secret} onChange={(e) => setHostForm({ ...hostForm, token_secret: e.target.value })} />
            </div>
          </div>
        ) : (
          <div className="grid md:grid-cols-2 gap-3">
            <div className="space-y-1">
              <label>Username</label>
              <input className="w-full" value={hostForm.username} onChange={(e) => setHostForm({ ...hostForm, username: e.target.value })} />
            </div>
            <div className="space-y-1">
              <label>Password</label>
              <input className="w-full" type="password" value={hostForm.password} onChange={(e) => setHostForm({ ...hostForm, password: e.target.value })} />
            </div>
            {hostForm.kind === "ssh" && (
              <div className="md:col-span-2 space-y-1">
                <label>Private key (optional)</label>
                <textarea className="w-full" rows={3} value={hostForm.private_key} onChange={(e) => setHostForm({ ...hostForm, private_key: e.target.value })} />
              </div>
            )}
          </div>
        )}
        <button className="btn-primary" onClick={addHost}>
          Save host
        </button>
      </section>

      <section className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
        {hosts.map((h) => (
          <article key={h.id} className="card p-4 space-y-2">
            <div className="flex justify-between">
              <div>
                <div className="font-medium">{h.name}</div>
                <div className="text-xs text-slate-500 mono">
                  {h.kind} · {h.address}:{h.port}
                </div>
              </div>
              <span className={`text-[11px] ${h.last_error ? "text-rose" : "text-mint"}`}>
                {h.last_error ? "error" : h.last_ok_at ? "ok" : "idle"}
              </span>
            </div>
            {h.last_error && <div className="text-xs text-rose break-all">{h.last_error}</div>}
            <div className="flex gap-2">
              <button className="btn-ghost" onClick={() => testHost(h.id)}>
                Test
              </button>
              {h.kind !== "ssh" && (
                <button className="btn-ghost" onClick={() => discover(h.id)}>
                  Discover
                </button>
              )}
              <button
                className="btn-danger"
                onClick={async () => {
                  await api.del(`/api/hosts/${h.id}`);
                  await load();
                }}
              >
                Delete
              </button>
            </div>
          </article>
        ))}
      </section>

      <section className="card p-5 space-y-4">
        <h2 className="font-medium">Add target</h2>
        <div className="grid md:grid-cols-4 gap-3">
          <div className="space-y-1">
            <label>Name</label>
            <input className="w-full" value={targetForm.name} onChange={(e) => setTargetForm({ ...targetForm, name: e.target.value })} />
          </div>
          <div className="space-y-1">
            <label>Kind</label>
            <select className="w-full" value={targetForm.kind} onChange={(e) => setTargetForm({ ...targetForm, kind: e.target.value })}>
              {TARGET_KINDS.map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <label>Host</label>
            <select className="w-full" value={targetForm.host_id} onChange={(e) => setTargetForm({ ...targetForm, host_id: Number(e.target.value) })}>
              {hosts.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.name}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <label>External id (vmid / name)</label>
            <input className="w-full" value={targetForm.external_id} onChange={(e) => setTargetForm({ ...targetForm, external_id: e.target.value })} />
          </div>
          <div className="space-y-1">
            <label>Proxmox node</label>
            <input className="w-full" value={targetForm.node} onChange={(e) => setTargetForm({ ...targetForm, node: e.target.value })} />
          </div>
          <div className="space-y-1">
            <label>MAC (for WOL)</label>
            <input className="w-full" value={targetForm.mac} onChange={(e) => setTargetForm({ ...targetForm, mac: e.target.value })} />
          </div>
          <label className="flex items-center gap-2 text-sm normal-case tracking-normal mt-6">
            <input type="checkbox" checked={targetForm.critical} onChange={(e) => setTargetForm({ ...targetForm, critical: e.target.checked })} />
            Critical (never shed)
          </label>
        </div>
        <button className="btn-primary" onClick={addTarget}>
          Save target
        </button>
      </section>

      <section className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="text-left text-[11px] uppercase tracking-[0.16em] text-slate-500 bg-ink-900/60">
            <tr>
              <th className="px-4 py-3">Target</th>
              <th>Kind</th>
              <th>Host</th>
              <th>Id</th>
              <th>Flags</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {targets.map((t) => (
              <tr key={t.id} className="border-t border-white/5">
                <td className="px-4 py-3">
                  <div>{t.name}</div>
                  {t.notes && <div className="text-[11px] text-slate-500">{t.notes}</div>}
                </td>
                <td>{t.kind}</td>
                <td className="text-slate-400">{t.host_name}</td>
                <td className="mono text-xs">{t.external_id}</td>
                <td>
                  {t.critical && <span className="text-amber text-xs">critical</span>}
                  {!t.enabled && <span className="text-slate-500 text-xs"> disabled</span>}
                </td>
                <td className="px-3 py-2">
                  <div className="flex flex-wrap gap-1 justify-end">
                    <button className="btn-ghost" onClick={() => toggleCritical(t)}>
                      {t.critical ? "Unprotect" : "Protect"}
                    </button>
                    <button className="btn-ghost" onClick={() => act(t.id, guessStop(t), true)}>
                      Dry stop
                    </button>
                    <button className="btn-ghost" onClick={() => act(t.id, guessStart(t), true)}>
                      Dry start
                    </button>
                    <button className="btn-danger" onClick={() => removeTarget(t.id)}>
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function guessStop(t: Target) {
  if (t.kind === "stack") return "docker_stop_stack";
  if (t.kind === "container" && t.host_kind === "synology") return "synology_stop_container";
  if (t.kind === "container") return "docker_stop";
  if (t.kind === "nas") return "synology_shutdown";
  if (t.kind === "lxc" || t.kind === "vm") return "proxmox_shutdown";
  return "notify";
}

function guessStart(t: Target) {
  if (t.kind === "stack") return "docker_start_stack";
  if (t.kind === "container" && t.host_kind === "synology") return "synology_start_container";
  if (t.kind === "container") return "docker_start";
  if (t.kind === "nas") return "wol";
  if (t.kind === "lxc" || t.kind === "vm") return "proxmox_start";
  return "notify";
}
