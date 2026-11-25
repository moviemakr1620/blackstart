import { Copy, Plus, Play, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { ConfirmDialog } from "../confirm";
import { useFeedback } from "../feedback";
import { ACTIONS, actionLabel, actionMeta, CATEGORIES, categoryOf, TRIGGERS, triggerLabel } from "../labels";
import { matchingInventory, TargetField } from "../targetPick";
import type { Host, NotifyChannel, Policy, PolicyStep, Target, UpsSource } from "../types";


function NameListField({
  values,
  onChange,
  placeholder,
}: {
  values: string[];
  onChange: (values: string[]) => void;
  placeholder: string;
}) {
  const [draft, setDraft] = useState("");
  function add(raw: string) {
    const parts = raw.split(",").map((p) => p.trim()).filter(Boolean);
    if (!parts.length) return;
    const seen = new Set(values.map((v) => v.toLowerCase()));
    const next = [...values];
    for (const p of parts) {
      if (!seen.has(p.toLowerCase())) {
        seen.add(p.toLowerCase());
        next.push(p);
      }
    }
    onChange(next);
    setDraft("");
  }
  return (
    <div>
      {values.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {values.map((name) => (
            <span key={name} className="inline-flex items-center gap-1 rounded-full bg-mint/10 text-mint border border-mint/25 px-2 py-0.5 text-xs">
              {name}
              <button type="button" onClick={() => onChange(values.filter((v) => v !== name))}>
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <input
        className="w-full"
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            add(draft);
          } else if (e.key === "Backspace" && !draft && values.length) {
            onChange(values.slice(0, -1));
          }
        }}
        onBlur={() => add(draft)}
      />
    </div>
  );
}

function triggerSelectValue(step: PolicyStep) {
  if (step.trigger.all) return "online_and_battery";
  return step.trigger.type || "on_battery";
}

function applyTriggerType(type: string, prev: PolicyStep["trigger"]): PolicyStep["trigger"] {
  if (type === "online_and_battery") {
    return {
      all: [{ type: "on_online" }, { type: "battery_gte", value: prev.value ?? 50, require_online: true }],
      hold_seconds: prev.hold_seconds ?? 60,
      value: prev.value ?? 50,
    };
  }
  return { ...prev, type, all: undefined };
}

const emptyStep = (order: number): PolicyStep => ({
  order,
  name: "New step",
  enabled: true,
  trigger: { type: "battery_lte", value: 50, hold_seconds: 15, require_on_battery: true },
  action: { type: "notify", message: "" },
  delay_before_sec: 0,
  timeout_sec: 120,
  continue_on_error: true,
});

export function Playbooks() {
  const fb = useFeedback();
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [targets, setTargets] = useState<Target[]>([]);
  const [hosts, setHosts] = useState<Host[]>([]);
  const [channels, setChannels] = useState<NotifyChannel[]>([]);
  const [upsSources, setUpsSources] = useState<UpsSource[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [draft, setDraft] = useState<Policy | null>(null);
  const [tab, setTab] = useState<"shed" | "restore">("shed");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmSave, setConfirmSave] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  async function load() {
    const [p, t, h, c, u] = await Promise.all([
      api.get<Policy[]>("/api/policies"),
      api.get<Target[]>("/api/targets"),
      api.get<Host[]>("/api/hosts"),
      api.get<NotifyChannel[]>("/api/notify-channels"),
      api.get<UpsSource[]>("/api/ups/sources"),
    ]);
    setPolicies(p);
    setTargets(t);
    setHosts(h);
    setChannels(c);
    setUpsSources(u);
    const first = p.find((x) => x.kind === tab) || p[0];
    if (first && (!activeId || !p.some((x) => x.id === activeId))) {
      setActiveId(first.id);
      setDraft(structuredClone(first));
    } else if (activeId) {
      const cur = p.find((x) => x.id === activeId);
      if (cur) setDraft(structuredClone(cur));
    }
  }

  useEffect(() => {
    load().catch((e) => fb.toast(e instanceof Error ? e.message : "Load failed", "err"));
  }, []);

  useEffect(() => {
    const first = policies.find((x) => x.kind === tab);
    if (first) {
      setActiveId(first.id);
      setDraft(structuredClone(first));
    } else {
      setActiveId(null);
      setDraft(null);
    }
  }, [tab]);

  function select(p: Policy) {
    setActiveId(p.id);
    setDraft(structuredClone(p));
    setMsg("");
  }

  function upsConflict(p: Policy): Policy | undefined {
    if (!p.enabled || !p.ups_source_id) return undefined;
    return policies.find(
      (other) =>
        other.id !== p.id &&
        other.enabled &&
        other.kind === p.kind &&
        other.ups_source_id === p.ups_source_id,
    );
  }

  function requestSave() {
    if (!draft) return;
    const conflict = upsConflict(draft);
    if (conflict) {
      const kindLabel = draft.kind === "shed" ? "load-shed" : "restore";
      const upsName = upsSources.find((s) => s.id === draft.ups_source_id)?.name || "this UPS";
      setConfirmSave(
        `${upsName} already has an active ${kindLabel} playbook “${conflict.name}”. Saving this as active will turn that playbook off.`,
      );
      return;
    }
    void save();
  }

  async function save() {
    if (!draft) return;
    setConfirmSave(null);
    setBusy(true);
    setMsg("");
    try {
      const saved = await api.put<Policy>(`/api/policies/${draft.id}`, draft);
      setDraft(saved);
      await load();
      setMsg(saved.enabled ? "Saved. This is the active playbook for this tab." : "Saved (disabled).");
      fb.toast("Playbook saved");
    } catch (e) {
      const text = e instanceof Error ? e.message : "Save failed";
      setMsg(text);
      fb.toast(text, "err");
    } finally {
      setBusy(false);
    }
  }

  async function createPolicy() {
    try {
      const created = await api.post<Policy>("/api/policies", {
        name: tab === "shed" ? "New shed playbook" : "New restore playbook",
        kind: tab,
        description: "",
        enabled: false,
        ups_source_id: upsSources.find((s) => s.enabled)?.id ?? upsSources[0]?.id ?? null,
        priority: 100,
        steps: [emptyStep(10)],
      });
      await load();
      select(created);
      fb.toast("Playbook created (disabled until you turn it on)");
    } catch (e) {
      fb.toast(e instanceof Error ? e.message : "Create failed", "err");
    }
  }

  async function clonePolicy() {
    if (!draft) return;
    try {
      const created = await api.post<Policy>(`/api/policies/${draft.id}/clone`, {});
      await load();
      select(created);
      fb.toast("Cloned. The copy is disabled until you enable it.");
    } catch (e) {
      fb.toast(e instanceof Error ? e.message : "Clone failed", "err");
    }
  }

  async function deletePolicy() {
    if (!draft) return;
    try {
      await api.del(`/api/policies/${draft.id}`);
      setConfirmDelete(false);
      setDraft(null);
      setActiveId(null);
      await load();
      fb.toast("Playbook deleted");
    } catch (e) {
      fb.toast(e instanceof Error ? e.message : "Delete failed", "err");
    }
  }

  async function run(dry: boolean) {
    if (!draft) return;
    setBusy(true);
    try {
      const res = await api.post<{ run_id: number; status: string; dry_run: boolean }>(
        `/api/policies/${draft.id}/run`,
        { dry_run: dry },
      );
      fb.watchRun(res.run_id, `${dry ? "Dry run" : "Live run"} — ${draft.name}`);
      fb.toast(dry ? "Dry run started" : "Live run started", dry ? "info" : "ok");
    } catch (e) {
      fb.toast(e instanceof Error ? e.message : "Run failed", "err");
    } finally {
      setBusy(false);
    }
  }

  function updateStep(idx: number, patch: Partial<PolicyStep>) {
    if (!draft) return;
    const steps = draft.steps.map((s, i) => (i === idx ? { ...s, ...patch } : s));
    setDraft({ ...draft, steps });
  }

  function move(idx: number, dir: -1 | 1) {
    if (!draft) return;
    const j = idx + dir;
    if (j < 0 || j >= draft.steps.length) return;
    const steps = [...draft.steps];
    [steps[idx], steps[j]] = [steps[j], steps[idx]];
    setDraft({ ...draft, steps: steps.map((s, i) => ({ ...s, order: (i + 1) * 10 })) });
  }

  const list = policies.filter((p) => p.kind === tab);
  const dockerish = (t: string | undefined) =>
    ["docker_stop", "docker_start", "docker_stop_stack", "docker_start_stack"].includes(t || "");

  function setCategory(idx: number, category: string, step: PolicyStep) {
    const first = ACTIONS.find((a) => a.category === category);
    if (!first) return;
    if (actionMeta(step.action.type)?.category === category) return;
    updateStep(idx, { action: { type: first.value, message: step.action.message } });
  }

  return (
    <div className="p-6 space-y-5">
      <header className="flex flex-wrap justify-between gap-3">
        <div>
          <div className="text-[11px] uppercase tracking-[0.22em] text-slate-500">What happens, and when</div>
          <h1 className="text-2xl font-semibold">Playbooks</h1>
          <p className="text-sm text-slate-500 mt-1 max-w-2xl">
            Pick the UPS this playbook applies to. A step can target several containers, stacks, or guests at once.
            <strong className="text-slate-300"> Dry run</strong> logs what would happen.{" "}
            <strong className="text-slate-300">Run live</strong> actually calls hosts if live actions are on.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn-ghost" onClick={() => run(true)} disabled={!draft || busy}>
            <Play size={14} /> Dry run
          </button>
          <button className="btn-warn" onClick={() => run(false)} disabled={!draft || busy}>
            <Play size={14} /> Run live
          </button>
          <button className="btn-ghost" onClick={clonePolicy} disabled={!draft || busy}>
            <Copy size={14} /> Clone
          </button>
          <button className="btn-danger" onClick={() => setConfirmDelete(true)} disabled={!draft || busy}>
            <Trash2 size={14} /> Delete
          </button>
          <button
            className="btn-ghost"
            onClick={async () => {
              try {
                const kinds = new Set(["proxmox", "pbs", "docker", "synology", "k3s", "omv", "ssh"]);
                for (const h of hosts.filter((x) => kinds.has(x.kind))) {
                  await api.post(`/api/hosts/${h.id}/discover`);
                }
                await load();
                fb.toast("Refreshed stacks, containers, and nodes");
              } catch (e) {
                fb.toast(e instanceof Error ? e.message : "Refresh failed", "err");
              }
            }}
          >
            Refresh inventory
          </button>
          <button className="btn-primary" onClick={requestSave} disabled={!draft || busy}>
            Save playbook
          </button>
        </div>
      </header>

      <div className="flex gap-2">
        {(["shed", "restore"] as const).map((k) => (
          <button
            key={k}
            className={`px-4 py-2 rounded-xl text-sm ${tab === k ? "bg-mint/15 text-mint" : "bg-white/5 text-slate-400"}`}
            onClick={() => setTab(k)}
          >
            {k === "shed" ? "Load shed" : "Restore"}
          </button>
        ))}
      </div>

      <div className="grid lg:grid-cols-[260px_1fr] gap-5">
        <aside className="space-y-2">
          {list.map((p) => (
            <button
              key={p.id}
              onClick={() => select(p)}
              className={`w-full text-left card p-4 ${activeId === p.id ? "ring-1 ring-mint/40" : ""}`}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="font-medium">{p.name}</div>
                <span className={`text-[10px] px-2 py-0.5 rounded-full ${p.enabled ? "bg-mint/15 text-mint" : "bg-white/5 text-slate-500"}`}>
                  {p.enabled ? "ACTIVE" : "off"}
                </span>
              </div>
              <div className="text-[11px] text-slate-500">
                {p.steps.length} steps
                {p.ups_source_id
                  ? ` · ${upsSources.find((s) => s.id === p.ups_source_id)?.name || "UPS #" + p.ups_source_id}`
                  : ""}
              </div>
              {p.enabled && p.binding_warning && <div className="text-[11px] text-amber">{p.binding_warning}</div>}
            </button>
          ))}
          <button className="btn-ghost w-full" onClick={createPolicy}>
            <Plus size={14} /> New playbook
          </button>
        </aside>

        {draft && (
          <section className="space-y-4">
            <div className="card p-5 space-y-4">
              <div className="flex flex-wrap items-end justify-between gap-4">
                <label className="flex items-center gap-3 text-base normal-case tracking-normal text-slate-100">
                  <input
                    type="checkbox"
                    className="h-5 w-5"
                    checked={draft.enabled}
                    onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
                  />
                  Active
                </label>
                <div className="space-y-1 min-w-[240px] flex-1">
                  <label>This playbook applies to UPS</label>
                  <select
                    className="w-full text-base"
                    value={draft.ups_source_id ?? ""}
                    onChange={(e) => {
                      const id = e.target.value ? Number(e.target.value) : null;
                      setDraft({ ...draft, ups_source_id: id });
                    }}
                  >
                    <option value="">— pick a UPS —</option>
                    {upsSources.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                        {s.enabled ? "" : " (disabled)"}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <div className="grid md:grid-cols-2 gap-4">
                <div className="space-y-1">
                  <label>Name</label>
                  <input className="w-full" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                </div>
                <div className="space-y-1">
                  <label>Priority (lower runs first)</label>
                  <input
                    className="w-full"
                    type="number"
                    value={draft.priority}
                    onChange={(e) => setDraft({ ...draft, priority: Number(e.target.value) })}
                  />
                </div>
                <div className="md:col-span-2 space-y-1">
                  <label>Description</label>
                  <textarea
                    className="w-full"
                    rows={2}
                    value={draft.description}
                    onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                  />
                </div>
              </div>
            </div>

            <Ladder steps={draft.steps} kind={draft.kind} />

            {draft.steps.map((step, idx) => {
              const tsel = triggerSelectValue(step);
              const actionHelp = ACTIONS.find((a) => a.value === step.action.type)?.help;
              const triggerHelp = TRIGGERS.find((t) => t.value === tsel)?.help;
              return (
                <article key={step.id ?? `new-${idx}`} className="card p-5 space-y-4">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                      <span className="mono text-xs text-slate-500">#{(idx + 1) * 10}</span>
                      <input
                        className="font-medium bg-transparent border-0 focus:ring-0 px-0"
                        value={step.name}
                        onChange={(e) => updateStep(idx, { name: e.target.value })}
                      />
                    </div>
                    <div className="flex gap-2">
                      <button className="btn-ghost" onClick={() => move(idx, -1)}>
                        Up
                      </button>
                      <button className="btn-ghost" onClick={() => move(idx, 1)}>
                        Down
                      </button>
                      <button
                        className="btn-danger"
                        onClick={() => setDraft({ ...draft, steps: draft.steps.filter((_, i) => i !== idx) })}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>

                  <div className="grid md:grid-cols-3 gap-3">
                    <div className="space-y-1">
                      <label>When</label>
                      <select
                        className="w-full"
                        value={tsel}
                        onChange={(e) => updateStep(idx, { trigger: applyTriggerType(e.target.value, step.trigger) })}
                      >
                        {TRIGGERS.map((t) => (
                          <option key={t.value} value={t.value}>
                            {t.label}
                          </option>
                        ))}
                      </select>
                      {triggerHelp && <p className="text-[11px] text-slate-500">{triggerHelp}</p>}
                    </div>
                    {(tsel === "battery_lte" || tsel === "battery_gte" || tsel === "load_gte" || tsel === "online_and_battery") && (
                      <div className="space-y-1">
                        <label>{tsel === "load_gte" ? "Load percent" : "Battery percent"}</label>
                        <input
                          className="w-full"
                          type="number"
                          value={
                            tsel === "online_and_battery"
                              ? (step.trigger.all?.find((c) => c.type === "battery_gte")?.value ?? step.trigger.value ?? 50)
                              : (step.trigger.value ?? 0)
                          }
                          onChange={(e) => {
                            const value = Number(e.target.value);
                            if (tsel === "online_and_battery") {
                              updateStep(idx, {
                                trigger: {
                                  ...step.trigger,
                                  value,
                                  all: [{ type: "on_online" }, { type: "battery_gte", value, require_online: true }],
                                },
                              });
                            } else {
                              updateStep(idx, { trigger: { ...step.trigger, value } });
                            }
                          }}
                        />
                      </div>
                    )}
                    {tsel === "runtime_lte" && (
                      <div className="space-y-1">
                        <label>Minutes remaining</label>
                        <input
                          className="w-full"
                          type="number"
                          value={step.trigger.minutes ?? 15}
                          onChange={(e) =>
                            updateStep(idx, { trigger: { ...step.trigger, minutes: Number(e.target.value) } })
                          }
                        />
                      </div>
                    )}
                    <div className="space-y-1">
                      <label>Hold still for (seconds)</label>
                      <input
                        className="w-full"
                        type="number"
                        value={step.trigger.hold_seconds ?? ""}
                        placeholder={draft.kind === "shed" ? "default (Settings, 15s)" : "default (Settings, 120s)"}
                        onChange={(e) =>
                          updateStep(idx, {
                            trigger: {
                              ...step.trigger,
                              hold_seconds: e.target.value === "" ? undefined : Number(e.target.value),
                            },
                          })
                        }
                      />
                      <p className="text-[11px] text-slate-500">
                        Condition must stay true this long so a 2-second flicker does not fire the step.
                      </p>
                    </div>
                  </div>

                  <div className="grid md:grid-cols-4 gap-3">
                    <div className="space-y-1">
                      <label>Category</label>
                      <select
                        className="w-full"
                        value={categoryOf(step.action.type)}
                        onChange={(e) => setCategory(idx, e.target.value, step)}
                      >
                        {CATEGORIES.map((c) => (
                          <option key={c.id} value={c.id}>
                            {c.label}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="space-y-1">
                      <label>Action</label>
                      <select
                        className="w-full"
                        value={step.action.type || "notify"}
                        onChange={(e) => updateStep(idx, { action: { type: e.target.value, message: step.action.message } })}
                      >
                        {ACTIONS.filter((a) => a.category === categoryOf(step.action.type)).map((t) => (
                          <option key={t.value} value={t.value}>
                            {t.label}
                          </option>
                        ))}
                      </select>
                      {actionHelp && <p className="text-[11px] text-slate-500">{actionHelp}</p>}
                    </div>
                    <div className="space-y-1 md:col-span-2">
                      <label>Target(s)</label>
                      {actionMeta(step.action.type)?.target === "none" && (
                        <div className="text-sm text-slate-500 py-2">No target needed</div>
                      )}
                      {actionMeta(step.action.type)?.target === "notify" && (
                        <select
                          className="w-full"
                          value={step.action.channel_id ?? "all"}
                          onChange={(e) => {
                            const v = e.target.value;
                            updateStep(idx, {
                              action: {
                                ...step.action,
                                channel_id: v === "all" ? "all" : Number(v),
                                target_id: null,
                              },
                            });
                          }}
                        >
                          <option value="all">All enabled channels</option>
                          {channels.map((ch) => (
                            <option key={ch.id} value={ch.id}>
                              {ch.name} ({ch.kind})
                            </option>
                          ))}
                        </select>
                      )}
                      <TargetField
                        actionType={step.action.type || ""}
                        action={step.action}
                        targets={targets}
                        hosts={hosts}
                        onChange={(patch) => updateStep(idx, { action: { ...step.action, ...patch } })}
                      />
                    </div>
                    <div className="space-y-1">
                      <label>Delay before this step (seconds)</label>
                      <input
                        className="w-full"
                        type="number"
                        value={step.delay_before_sec}
                        onChange={(e) => updateStep(idx, { delay_before_sec: Number(e.target.value) })}
                      />
                      <p className="text-[11px] text-slate-500">Wait after the trigger, before doing the action.</p>
                    </div>
                  </div>

                  {step.action.type === "notify" && (
                    <div className="space-y-1">
                      <label>Message (sent via Settings → Notifications)</label>
                      <input
                        className="w-full"
                        value={step.action.message || ""}
                        onChange={(e) => updateStep(idx, { action: { ...step.action, message: e.target.value } })}
                        placeholder="Grid down. Shedding media stack."
                      />
                    </div>
                  )}
                  {step.action.type === "ssh" && (
                    <div className="space-y-1">
                      <label>Command</label>
                      <input
                        className="w-full"
                        value={step.action.command || ""}
                        onChange={(e) => updateStep(idx, { action: { ...step.action, command: e.target.value } })}
                      />
                    </div>
                  )}
                  {dockerish(step.action.type) && matchingInventory(step.action.type || "", targets, hosts).targets.length === 0 && (
                    <div className="grid md:grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <label>
                          {step.action.type?.includes("stack")
                            ? "Compose stack names (comma or Enter)"
                            : "Container names (comma or Enter)"}
                        </label>
                        <NameListField
                          values={
                            step.action.type?.includes("stack")
                              ? step.action.stacks || (step.action.stack || step.action.name ? [String(step.action.stack || step.action.name)] : [])
                              : step.action.containers || (step.action.container || step.action.name ? [String(step.action.container || step.action.name)] : [])
                          }
                          placeholder={step.action.type?.includes("stack") ? "media, arr" : "plex, sonarr"}
                          onChange={(names) => {
                            if (step.action.type?.includes("stack")) {
                              updateStep(idx, { action: { ...step.action, stacks: names, stack: names[0], name: names[0] } });
                            } else {
                              updateStep(idx, { action: { ...step.action, containers: names, container: names[0], name: names[0] } });
                            }
                          }}
                        />
                        <p className="text-[11px] text-slate-500">
                          Inventory is empty for this action. Add a Docker (or Synology) host, click Discover / Refresh
                          inventory, then pick from the Target dropdown. You can still type several names here.
                        </p>
                      </div>
                      <div className="space-y-1">
                        <label>Docker / SSH host</label>
                        <select
                          className="w-full"
                          value={step.action.host_id ?? ""}
                          onChange={(e) =>
                            updateStep(idx, {
                              action: { ...step.action, host_id: e.target.value ? Number(e.target.value) : null },
                            })
                          }
                        >
                          <option value="">Use the target&apos;s host</option>
                          {hosts
                            .filter((h) => h.kind === "docker" || h.kind === "ssh")
                            .map((h) => (
                              <option key={h.id} value={h.id}>
                                {h.name}
                              </option>
                            ))}
                        </select>
                      </div>
                    </div>
                  )}
                  {step.action.type === "wol" && (
                    <div className="space-y-1">
                      <label>MAC override (optional)</label>
                      <input
                        className="w-full"
                        value={step.action.mac || ""}
                        onChange={(e) => updateStep(idx, { action: { ...step.action, mac: e.target.value } })}
                        placeholder="Leave blank to use the host MAC(s)"
                      />
                      <p className="text-[11px] text-slate-500">
                        Blank uses the host record. Synology HA sends both node MACs. Standalone DSM sends the one MAC
                        on that host.
                      </p>
                    </div>
                  )}
                  {step.action.type === "wait" && (
                    <div className="space-y-1">
                      <label>Wait seconds</label>
                      <input
                        className="w-full"
                        type="number"
                        value={step.action.seconds ?? 10}
                        onChange={(e) => updateStep(idx, { action: { ...step.action, seconds: Number(e.target.value) } })}
                      />
                    </div>
                  )}
                  {step.action.type === "wait_tcp" && (
                    <div className="grid md:grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <label>TCP port to wait for</label>
                        <input
                          className="w-full"
                          type="number"
                          value={step.action.port ?? 5001}
                          onChange={(e) =>
                            updateStep(idx, { action: { ...step.action, port: Number(e.target.value) } })
                          }
                        />
                      </div>
                      <p className="text-xs text-slate-500 self-end pb-2">
                        Uses the host address you picked. Typical: Synology 5001, Proxmox 8006.
                      </p>
                    </div>
                  )}

                  <div className="flex flex-wrap gap-4 text-sm text-slate-400">
                    <label className="flex items-center gap-2 normal-case tracking-normal">
                      <input
                        type="checkbox"
                        checked={step.enabled}
                        onChange={(e) => updateStep(idx, { enabled: e.target.checked })}
                      />
                      Enabled
                    </label>
                    <label className="flex items-center gap-2 normal-case tracking-normal">
                      <input
                        type="checkbox"
                        checked={step.continue_on_error}
                        onChange={(e) => updateStep(idx, { continue_on_error: e.target.checked })}
                      />
                      Continue if this step fails
                    </label>
                    <label className="flex items-center gap-2 normal-case tracking-normal">
                      Give up after (seconds)
                      <input
                        className="w-24"
                        type="number"
                        value={step.timeout_sec}
                        onChange={(e) => updateStep(idx, { timeout_sec: Number(e.target.value) })}
                      />
                    </label>
                  </div>
                  <p className="text-[11px] text-slate-500">
                    Timeout is how long Blackstart waits for Proxmox/Docker/SSH to finish. It is not the same as hold
                    (how long the UPS condition must last) or delay (a pause you asked for).
                  </p>
                </article>
              );
            })}

            <button
              className="btn-ghost"
              onClick={() =>
                setDraft({
                  ...draft,
                  steps: [...draft.steps, emptyStep((draft.steps.length + 1) * 10)],
                })
              }
            >
              <Plus size={14} /> Add step
            </button>
            {msg && <div className="text-sm text-slate-300">{msg}</div>}
          </section>
        )}
      </div>
      {confirmSave && (
        <ConfirmDialog
          title="This UPS already has a playbook"
          body={confirmSave}
          confirmLabel="Save anyway"
          danger={false}
          busy={busy}
          onCancel={() => setConfirmSave(null)}
          onConfirm={() => void save()}
        />
      )}
      {confirmDelete && draft && (
        <ConfirmDialog
          title={`Delete playbook “${draft.name}”?`}
          body="Steps, triggers, and the assignment to this UPS are removed. This cannot be undone."
          busy={busy}
          onCancel={() => setConfirmDelete(false)}
          onConfirm={() => void deletePolicy()}
        />
      )}
    </div>
  );
}

function Ladder({ steps, kind }: { steps: PolicyStep[]; kind: string }) {
  return (
    <div className="card p-4 overflow-x-auto">
      <div className="text-[11px] uppercase tracking-[0.16em] text-slate-500 mb-3">
        {kind === "shed" ? "Shed ladder" : "Restore sequence"}
      </div>
      <div className="flex gap-3 min-w-max">
        {steps.map((s, i) => (
          <div key={s.id ?? i} className="w-44 rounded-xl bg-ink-900/80 p-3 border border-white/5">
            <div className="text-[10px] text-mint">
              {s.trigger.type === "battery_lte"
                ? `≤ ${s.trigger.value}%`
                : s.trigger.type === "battery_gte"
                  ? `≥ ${s.trigger.value}%`
                  : s.trigger.all
                    ? "Grid back + battery"
                    : triggerLabel(s.trigger.type)}
            </div>
            <div className="text-sm mt-1 leading-tight">{s.name}</div>
            <div className="text-[10px] text-slate-500 mt-2">{actionLabel(s.action.type)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
