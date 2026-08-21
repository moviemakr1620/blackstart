import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { ConfirmDialog } from "../confirm";
import { useFeedback } from "../feedback";
import { HOST_KIND_HELP } from "../labels";
import type { Host, Target } from "../types";

type SortKey = "name" | "kind" | "host" | "id";
type PendingDelete =
  | { kind: "host"; id: number; name: string; targetCount: number }
  | { kind: "target"; id: number; name: string };

const HOST_KINDS = ["proxmox", "synology", "docker", "k3s", "omv", "ssh"] as const;
const TARGET_KINDS = ["vm", "lxc", "container", "stack", "nas", "k3s_node", "host", "custom"];

const emptyHost = {
  name: "",
  kind: "proxmox" as Host["kind"],
  address: "",
  port: 8006,
  token_id: "",
  token_secret: "",
  username: "",
  password: "",
  private_key: "",
  access: "ssh",
  ha: false,
  mac: "",
  mac_passive: "",
  node_a: "",
  node_b: "",
};

const emptyTarget = {
  name: "",
  kind: "vm",
  host_id: 0,
  external_id: "",
  critical: false,
  enabled: true,
  notes: "",
  mac: "",
  node: "",
};

export function Inventory() {
  const fb = useFeedback();
  const [hosts, setHosts] = useState<Host[]>([]);
  const [targets, setTargets] = useState<Target[]>([]);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [hostForm, setHostForm] = useState(emptyHost);
  const [targetForm, setTargetForm] = useState(emptyTarget);
  const [editingHostId, setEditingHostId] = useState<number | null>(null);
  const [editingTargetId, setEditingTargetId] = useState<number | null>(null);
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [targetQuery, setTargetQuery] = useState("");
  const [hostFilter, setHostFilter] = useState<number | "all">("all");
  const [kindFilter, setKindFilter] = useState("all");
  const [sortKey, setSortKey] = useState<SortKey>("host");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  async function load() {
    const [h, t] = await Promise.all([api.get<Host[]>("/api/hosts"), api.get<Target[]>("/api/targets")]);
    setHosts(h);
    setTargets(t);
    setTargetForm((f) => (f.host_id || !h[0] ? f : { ...f, host_id: h[0].id }));
  }

  useEffect(() => {
    load().catch((e) => setErr(e instanceof Error ? e.message : "Failed to load"));
  }, []);

  function editHost(h: Host) {
    setEditingHostId(h.id);
    setHostForm({
      name: h.name,
      kind: h.kind,
      address: h.address,
      port: h.port,
      token_id: "",
      token_secret: "",
      username: "",
      password: "",
      private_key: "",
      access: String(h.extra?.access || (h.kind === "docker" ? "ssh" : "api")),
      ha: Boolean(h.extra?.ha),
      mac: String(h.extra?.mac || ""),
      mac_passive: String(h.extra?.mac_passive || ""),
      node_a: String(h.extra?.node_a || ""),
      node_b: String(h.extra?.node_b || ""),
    });
    setMsg(`Editing host “${h.name}”. Leave secrets blank to keep the current ones.`);
    setErr("");
  }

  function editTarget(t: Target) {
    const extra = t.extra || {};
    setEditingTargetId(t.id);
    setTargetForm({
      name: t.name,
      kind: t.kind,
      host_id: t.host_id || 0,
      external_id: t.external_id,
      critical: t.critical,
      enabled: t.enabled,
      notes: t.notes || "",
      mac: typeof extra.mac === "string" ? extra.mac : "",
      node: typeof extra.node === "string" ? extra.node : "",
    });
    setMsg(`Editing target “${t.name}”.`);
    setErr("");
  }

  async function saveHost() {
    setErr("");
    const creds: Record<string, string> = {};
    if (hostForm.kind === "proxmox") {
      if (hostForm.token_id) creds.token_id = hostForm.token_id;
      if (hostForm.token_secret) creds.token_secret = hostForm.token_secret;
    } else {
      if (hostForm.username) creds.username = hostForm.username;
      if (hostForm.password) creds.password = hostForm.password;
      if (hostForm.private_key) creds.private_key = hostForm.private_key;
    }
    const existing = editingHostId ? hosts.find((h) => h.id === editingHostId) : undefined;
    const extra: Record<string, unknown> = { ...(existing?.extra || {}) };
    if (hostForm.kind === "docker") {
      extra.access = hostForm.access;
      if (hostForm.address.startsWith("unix")) extra.socket = hostForm.address;
    }
    if (hostForm.kind === "synology") {
      extra.ha = hostForm.ha;
      extra.mac = hostForm.mac;
      extra.mac_passive = hostForm.mac_passive;
      extra.node_a = hostForm.node_a;
      extra.node_b = hostForm.node_b;
    }
    const body = {
      name: hostForm.name,
      kind: hostForm.kind,
      enabled: true,
      address: hostForm.address,
      port: Number(hostForm.port),
      verify_tls: false,
      credentials: creds,
      extra,
    };
    try {
      if (editingHostId) {
        await api.put(`/api/hosts/${editingHostId}`, body);
        setMsg("Host updated.");
      } else {
        await api.post("/api/hosts", body);
        setMsg("Host saved.");
      }
      setEditingHostId(null);
      setHostForm(emptyHost);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not save host");
    }
  }

  async function saveTarget() {
    setErr("");
    const existing = editingTargetId ? targets.find((t) => t.id === editingTargetId) : null;
    const extra = { ...(existing?.extra || {}), mac: targetForm.mac, node: targetForm.node };
    const body = {
      name: targetForm.name,
      kind: targetForm.kind,
      host_id: targetForm.host_id || null,
      external_id: targetForm.external_id,
      critical: targetForm.critical,
      enabled: targetForm.enabled,
      notes: targetForm.notes,
      extra,
    };
    try {
      if (editingTargetId) {
        await api.put(`/api/targets/${editingTargetId}`, body);
        setMsg("Target updated.");
      } else {
        await api.post("/api/targets", body);
        setMsg("Target saved.");
      }
      setEditingTargetId(null);
      setTargetForm({ ...emptyTarget, host_id: hosts[0]?.id || 0 });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not save target");
    }
  }

  async function testHost(id: number) {
    setErr("");
    try {
      const res = await api.post<{ ok: boolean }>(`/api/hosts/${id}/test`);
      setMsg(res.ok ? "Connection OK." : "Test failed.");
      const host = hosts.find((h) => h.id === id);
      if (res.ok && host && ["docker", "synology", "k3s", "omv", "ssh"].includes(host.kind)) {
        try {
          await api.post(`/api/hosts/${id}/discover`);
          setMsg("Connection OK. Inventory refreshed from that host.");
        } catch {
          /* discover is best-effort after test */
        }
      }
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Test failed");
    }
  }

  async function discover(id: number) {
    setErr("");
    try {
      await api.post(`/api/hosts/${id}/discover`);
      setMsg("Discover complete — new targets imported.");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Discover failed");
    }
  }

  function requestDeleteHost(h: Host) {
    setPendingDelete({
      kind: "host",
      id: h.id,
      name: h.name,
      targetCount: targets.filter((t) => t.host_id === h.id).length,
    });
  }

  function requestDeleteTarget(t: Target) {
    setPendingDelete({ kind: "target", id: t.id, name: t.name });
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleting(true);
    setErr("");
    try {
      if (pendingDelete.kind === "host") {
        await api.del(`/api/hosts/${pendingDelete.id}`);
        if (editingHostId === pendingDelete.id) {
          setEditingHostId(null);
          setHostForm(emptyHost);
        }
        setMsg(`Deleted host “${pendingDelete.name}”.`);
      } else {
        await api.del(`/api/targets/${pendingDelete.id}`);
        if (editingTargetId === pendingDelete.id) {
          setEditingTargetId(null);
          setTargetForm({ ...emptyTarget, host_id: hosts[0]?.id || 0 });
        }
        setMsg(`Deleted target “${pendingDelete.name}”.`);
      }
      setPendingDelete(null);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setDeleting(false);
    }
  }

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  const targetKinds = useMemo(() => {
    const set = new Set(targets.map((t) => t.kind));
    return [...set].sort();
  }, [targets]);

  const visibleTargets = useMemo(() => {
    const q = targetQuery.trim().toLowerCase();
    const filtered = targets.filter((t) => {
      if (hostFilter !== "all" && t.host_id !== hostFilter) return false;
      if (kindFilter !== "all" && t.kind !== kindFilter) return false;
      if (!q) return true;
      const hay = `${t.name} ${t.kind} ${t.host_name || ""} ${t.external_id} ${t.notes || ""}`.toLowerCase();
      return hay.includes(q);
    });
    const dir = sortDir === "asc" ? 1 : -1;
    const value = (t: Target) => {
      if (sortKey === "kind") return t.kind;
      if (sortKey === "host") return t.host_name || "";
      if (sortKey === "id") return t.external_id;
      return t.name;
    };
    return [...filtered].sort((a, b) => {
      const av = value(a);
      const bv = value(b);
      const cmp = av.localeCompare(bv, undefined, { sensitivity: "base", numeric: true });
      if (cmp !== 0) return cmp * dir;
      return a.name.localeCompare(b.name, undefined, { sensitivity: "base" }) * dir;
    });
  }, [targets, targetQuery, hostFilter, kindFilter, sortKey, sortDir]);

  async function toggleCritical(t: Target) {
    setErr("");
    try {
      await api.put(`/api/targets/${t.id}`, { ...t, extra: t.extra, critical: !t.critical });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Update failed");
    }
  }

  async function act(id: number, action: string, dry = true) {
    setErr("");
    try {
      const res = await api.post<{ result: unknown }>(`/api/targets/${id}/action?action_type=${action}&dry_run=${dry}`);
      const text = `${dry ? "Dry" : "Live"} ${action}`;
      setMsg(text);
      fb.showResult(text, res.result, true);
      fb.toast(text);
    } catch (e) {
      const text = e instanceof Error ? e.message : "Action failed";
      setErr(text);
      fb.toast(text, "err");
    }
  }

  return (
    <div className="p-6 space-y-6">
      <header>
        <div className="text-[11px] uppercase tracking-[0.22em] text-slate-500">Hosts and workloads</div>
        <h1 className="text-2xl font-semibold">Inventory</h1>
        <p className="text-sm text-slate-500 mt-1 max-w-3xl">
          A <strong className="text-slate-300">host</strong> is a machine Blackstart logs into (Proxmox, the NAS, a
          Docker box). A <strong className="text-slate-300">target</strong> is one thing on that machine — VM 204, the
          plex container, the NAS itself. Kind is the API we speak. The host dropdown on a target is “which machine
          does this live on?” Credentials live on the host, not the target.
        </p>
      </header>
      {msg && <div className="text-sm text-mint">{msg}</div>}
      {err && <div className="text-sm text-rose">{err}</div>}

      <section className="card p-5 space-y-4">
        <h2 className="font-medium">{editingHostId ? `Edit host #${editingHostId}` : "Add host"}</h2>
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
                const port =
                  kind === "proxmox" ? 8006 : kind === "synology" ? 5001 : kind === "omv" ? 443 : kind === "docker" ? 22 : 22;
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
        {HOST_KIND_HELP[hostForm.kind] && (
          <p className="text-xs text-slate-500">{HOST_KIND_HELP[hostForm.kind]}</p>
        )}
        {hostForm.kind === "synology" && (
          <div className="space-y-3 rounded-xl bg-ink-900/60 p-4">
            <label className="flex items-center gap-2 text-sm normal-case tracking-normal">
              <input type="checkbox" checked={hostForm.ha} onChange={(e) => setHostForm({ ...hostForm, ha: e.target.checked })} />
              This is a Synology High Availability cluster
            </label>
            <p className="text-xs text-slate-500">
              Address should be the <strong>cluster virtual IP</strong>. Tick HA so shutdown uses the cluster API (and
              both nodes if you fill physical IPs), and Wake on LAN sends a packet to each MAC.
            </p>
            <div className="grid md:grid-cols-2 gap-3">
              <div className="space-y-1">
                <label>{hostForm.ha ? "Active node MAC" : "MAC (for WOL)"}</label>
                <input className="w-full" value={hostForm.mac} onChange={(e) => setHostForm({ ...hostForm, mac: e.target.value })} />
              </div>
              {hostForm.ha && (
                <>
                  <div className="space-y-1">
                    <label>Passive node MAC</label>
                    <input className="w-full" value={hostForm.mac_passive} onChange={(e) => setHostForm({ ...hostForm, mac_passive: e.target.value })} />
                  </div>
                  <div className="space-y-1">
                    <label>Active node IP (optional)</label>
                    <input className="w-full" value={hostForm.node_a} onChange={(e) => setHostForm({ ...hostForm, node_a: e.target.value })} placeholder="physical IP of node A" />
                  </div>
                  <div className="space-y-1">
                    <label>Passive node IP (optional)</label>
                    <input className="w-full" value={hostForm.node_b} onChange={(e) => setHostForm({ ...hostForm, node_b: e.target.value })} placeholder="physical IP of node B" />
                  </div>
                </>
              )}
            </div>
          </div>
        )}
        {hostForm.kind === "docker" && (
          <div className="space-y-1">
            <label>How to reach Docker</label>
            <select
              className="w-full max-w-md"
              value={hostForm.access}
              onChange={(e) =>
                setHostForm({
                  ...hostForm,
                  access: e.target.value,
                  port: e.target.value === "ssh" ? 22 : 2375,
                })
              }
            >
              <option value="ssh">SSH and run docker commands (recommended)</option>
              <option value="api">Docker TCP socket / unix socket</option>
            </select>
          </div>
        )}
        {hostForm.kind === "k3s" && (
          <p className="text-xs text-slate-500">
            SSH to one control-plane Pi (port 22) — you do not add every node as a host. Discover imports the rest of
            the cluster as k3s_node targets. The same SSH user/key is used to drain and <code>shutdown -h now</code>{" "}
            each node via its InternalIP. PoE hats typically power the Pi whenever the switch is up, so Wake on LAN is
            optional: put a MAC on a node target only if you actually WOL it.
          </p>
        )}
        {hostForm.kind === "proxmox" ? (
          <div className="grid md:grid-cols-2 gap-3">
            <div className="space-y-1">
              <label>API token id (user@pam!name){editingHostId ? " — blank keeps current" : ""}</label>
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
              <label>Username{editingHostId ? " — blank keeps current" : ""}</label>
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
        <div className="flex gap-2">
          <button className="btn-primary" onClick={saveHost}>
            {editingHostId ? "Save host changes" : "Save host"}
          </button>
          {editingHostId && (
            <button
              className="btn-ghost"
              onClick={() => {
                setEditingHostId(null);
                setHostForm(emptyHost);
              }}
            >
              Cancel
            </button>
          )}
        </div>
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
            <div className="flex flex-wrap gap-2">
              <button className="btn-ghost" onClick={() => editHost(h)}>
                Edit
              </button>
              <button className="btn-ghost" onClick={() => testHost(h.id)}>
                Test
              </button>
              {h.kind !== "ssh" && (
                <button className="btn-ghost" onClick={() => discover(h.id)}>
                  Discover
                </button>
              )}
              <button className="btn-danger" onClick={() => requestDeleteHost(h)}>
                Delete
              </button>
            </div>
          </article>
        ))}
      </section>

      <section className="card p-5 space-y-4">
        <h2 className="font-medium">{editingTargetId ? `Edit target #${editingTargetId}` : "Add target"}</h2>
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
            <label>MAC (for WOL — optional on PoE)</label>
            <input className="w-full" value={targetForm.mac} onChange={(e) => setTargetForm({ ...targetForm, mac: e.target.value })} />
          </div>
          <label className="flex items-center gap-2 text-sm normal-case tracking-normal mt-6">
            <input type="checkbox" checked={targetForm.critical} onChange={(e) => setTargetForm({ ...targetForm, critical: e.target.checked })} />
            Critical (never shed)
          </label>
          <label className="flex items-center gap-2 text-sm normal-case tracking-normal mt-6">
            <input type="checkbox" checked={targetForm.enabled} onChange={(e) => setTargetForm({ ...targetForm, enabled: e.target.checked })} />
            Enabled
          </label>
        </div>
        <div className="flex gap-2">
          <button className="btn-primary" onClick={saveTarget}>
            {editingTargetId ? "Save target changes" : "Save target"}
          </button>
          {editingTargetId && (
            <button
              className="btn-ghost"
              onClick={() => {
                setEditingTargetId(null);
                setTargetForm({ ...emptyTarget, host_id: hosts[0]?.id || 0 });
              }}
            >
              Cancel
            </button>
          )}
        </div>
      </section>

      <section className="card overflow-hidden">
        <div className="flex flex-wrap items-end gap-3 p-4 border-b border-white/5">
          <div className="space-y-1 flex-1 min-w-[180px]">
            <label>Filter</label>
            <input
              className="w-full"
              placeholder="Name, host, id…"
              value={targetQuery}
              onChange={(e) => setTargetQuery(e.target.value)}
            />
          </div>
          <div className="space-y-1 min-w-[160px]">
            <label>Host</label>
            <select
              className="w-full"
              value={hostFilter === "all" ? "all" : String(hostFilter)}
              onChange={(e) => setHostFilter(e.target.value === "all" ? "all" : Number(e.target.value))}
            >
              <option value="all">All hosts</option>
              {hosts.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.name}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1 min-w-[140px]">
            <label>Kind</label>
            <select className="w-full" value={kindFilter} onChange={(e) => setKindFilter(e.target.value)}>
              <option value="all">All kinds</option>
              {targetKinds.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </div>
          <div className="text-xs text-slate-500 pb-2">
            {visibleTargets.length} of {targets.length}
          </div>
        </div>
        <table className="w-full text-sm">
          <thead className="text-left text-[11px] uppercase tracking-[0.16em] text-slate-500 bg-ink-900/60">
            <tr>
              <th className="px-4 py-3 sortable" onClick={() => toggleSort("name")}>
                Target{sortKey === "name" ? (sortDir === "asc" ? " ↑" : " ↓") : ""}
              </th>
              <th className="sortable" onClick={() => toggleSort("kind")}>
                Kind{sortKey === "kind" ? (sortDir === "asc" ? " ↑" : " ↓") : ""}
              </th>
              <th className="sortable" onClick={() => toggleSort("host")}>
                Host{sortKey === "host" ? (sortDir === "asc" ? " ↑" : " ↓") : ""}
              </th>
              <th className="sortable" onClick={() => toggleSort("id")}>
                Id{sortKey === "id" ? (sortDir === "asc" ? " ↑" : " ↓") : ""}
              </th>
              <th>Flags</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {visibleTargets.length === 0 && (
              <tr>
                <td className="px-4 py-6 text-slate-500" colSpan={6}>
                  {targets.length === 0 ? "No targets yet. Add a host and click Discover." : "No targets match these filters."}
                </td>
              </tr>
            )}
            {visibleTargets.map((t) => (
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
                    <button className="btn-ghost" onClick={() => editTarget(t)}>
                      Edit
                    </button>
                    <button className="btn-ghost" onClick={() => toggleCritical(t)}>
                      {t.critical ? "Unprotect" : "Protect"}
                    </button>
                    <button className="btn-ghost" onClick={() => act(t.id, guessStop(t), true)}>
                      Dry stop
                    </button>
                    <button className="btn-ghost" onClick={() => act(t.id, guessStart(t), true)}>
                      Dry start
                    </button>
                    <button className="btn-danger" onClick={() => requestDeleteTarget(t)}>
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {pendingDelete && (
        <ConfirmDialog
          title={pendingDelete.kind === "host" ? `Delete host “${pendingDelete.name}”?` : `Delete target “${pendingDelete.name}”?`}
          body={
            pendingDelete.kind === "host"
              ? pendingDelete.targetCount
                ? `This also deletes ${pendingDelete.targetCount} target${pendingDelete.targetCount === 1 ? "" : "s"} on this host. Playbook steps that pointed here will need a new target. This cannot be undone.`
                : "Playbook steps that pointed at this host will need a new host. This cannot be undone."
              : "Playbook steps that pointed at this target will need a new one. This cannot be undone."
          }
          busy={deleting}
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => void confirmDelete()}
        />
      )}
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
