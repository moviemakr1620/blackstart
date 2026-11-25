import { useEffect, useMemo, useState } from "react";
import { MultiSelect, type MultiSelectOption } from "./components/MultiSelect";
import { actionMeta } from "./labels";
import type { Action, Host, Target } from "./types";

export function matchingInventory(
  actionType: string,
  targets: Target[],
  hosts: Host[],
): { kind: "none" | "notify" | "target" | "host"; targets: Target[]; hosts: Host[] } {
  const meta = actionMeta(actionType);
  const want = meta?.target || "none";
  if (want === "none" || want === "notify") return { kind: want, targets: [], hosts: [] };
  if (want === "vm") {
    return { kind: "target", targets: targets.filter((t) => t.kind === "vm" || t.kind === "lxc"), hosts: [] };
  }
  if (want === "container") {
    const syno = actionType.startsWith("synology");
    return {
      kind: "target",
      targets: targets.filter(
        (t) => t.kind === "container" && (syno ? t.host_kind === "synology" : t.host_kind !== "synology"),
      ),
      hosts: [],
    };
  }
  if (want === "stack") return { kind: "target", targets: targets.filter((t) => t.kind === "stack"), hosts: [] };
  if (want === "nas") {
    return { kind: "target", targets: targets.filter((t) => t.kind === "nas" || t.host_kind === "synology"), hosts: [] };
  }
  if (want === "proxmox-host") return { kind: "host", targets: [], hosts: hosts.filter((h) => h.kind === "proxmox") };
  if (want === "pbs-host") return { kind: "host", targets: [], hosts: hosts.filter((h) => h.kind === "pbs") };
  if (want === "host") {
    if (actionType === "ssh") {
      return { kind: "host", targets: [], hosts: hosts.filter((h) => h.kind === "ssh" || h.kind === "docker") };
    }
    if (actionType === "synology_shutdown") {
      return { kind: "host", targets: [], hosts: hosts.filter((h) => h.kind === "synology") };
    }
    if (actionType === "wol") {
      return { kind: "host", targets: [], hosts };
    }
    return { kind: "host", targets: [], hosts };
  }
  if (want === "wol") {
    return { kind: "host", targets: [], hosts };
  }
  if (want === "k3s") return { kind: "target", targets: targets.filter((t) => t.kind === "k3s_node"), hosts: [] };
  if (want === "k3s-host") return { kind: "host", targets: [], hosts: hosts.filter((h) => h.kind === "k3s") };
  if (want === "omv") return { kind: "host", targets: [], hosts: hosts.filter((h) => h.kind === "omv") };
  return { kind: "target", targets, hosts: [] };
}

function alpha(a: string, b: string) {
  return a.localeCompare(b, undefined, { sensitivity: "base", numeric: true });
}

function byName<T extends { name: string }>(list: T[]): T[] {
  return [...list].sort((a, b) => alpha(a.name, b.name));
}

function stackOf(t: Target): string {
  const s = t.extra?.stack;
  return typeof s === "string" ? s.trim() : "";
}

function hostMacs(h: Host): string[] {
  const extra = h.extra || {};
  return [extra.mac, extra.mac_passive, extra.mac_active]
    .filter((v): v is string => typeof v === "string" && Boolean(v.trim()))
    .map((v) => v.trim());
}

function hostOptionLabel(h: Host, actionType: string): string {
  const extra = h.extra || {};
  const ha = Boolean(extra.ha);
  const macs = hostMacs(h);
  if (h.kind === "synology") {
    const mode = ha ? "HA cluster" : "DSM";
    if (actionType === "wol" && macs.length) return `${h.name} (${mode} · ${macs.join(" + ")})`;
    return `${h.name} (${mode})`;
  }
  if (actionType === "wol" && macs.length) return `${h.name} (${h.kind} · ${macs.join(" + ")})`;
  return `${h.name} (${h.kind})`;
}

const DOCKER_ACTIONS = new Set(["docker_stop", "docker_start", "docker_stop_stack", "docker_start_stack"]);

function selectedTargetIds(action: Action): number[] {
  if (Array.isArray(action.target_ids) && action.target_ids.length) {
    return action.target_ids.filter((n) => Number.isFinite(n));
  }
  return action.target_id ? [action.target_id] : [];
}

function selectedHostIds(action: Action): number[] {
  if (Array.isArray(action.host_ids) && action.host_ids.length) {
    return action.host_ids.filter((n) => Number.isFinite(n));
  }
  return action.host_id ? [action.host_id] : [];
}

function stackLabel(t: Target): string {
  return t.external_id || t.name.replace(/^stack:/, "");
}

export function TargetField({
  actionType,
  action,
  targets,
  hosts,
  onChange,
}: {
  actionType: string;
  action: Action;
  targets: Target[];
  hosts: Host[];
  onChange: (patch: Partial<Action>) => void;
}) {
  const match = matchingInventory(actionType, targets, hosts);
  const isDocker = DOCKER_ACTIONS.has(actionType);
  const isContainer = actionMeta(actionType)?.target === "container";
  const isStack = actionMeta(actionType)?.target === "stack";
  const pickedIds = selectedTargetIds(action);
  const selectedTargets = pickedIds.map((id) => targets.find((t) => t.id === id)).filter((t): t is Target => Boolean(t));

  const inferredHostId = action.host_id ?? selectedTargets[0]?.host_id ?? null;

  const dockerHosts = useMemo(() => {
    const ids = new Set(match.targets.map((t) => t.host_id).filter((id): id is number => Boolean(id)));
    return byName(hosts.filter((h) => h.kind === "docker" || h.kind === "ssh" || h.kind === "omv" || ids.has(h.id)));
  }, [hosts, match.targets]);

  const uniqueHostIds = useMemo(() => {
    const ids = new Set(match.targets.map((t) => t.host_id).filter((id): id is number => Boolean(id)));
    return ids;
  }, [match.targets]);

  const showHostFirst = isDocker || uniqueHostIds.size > 1;
  const hostChoices = isDocker ? dockerHosts : byName(hosts.filter((h) => uniqueHostIds.has(h.id)));

  const [stackFilter, setStackFilter] = useState("__all__");
  useEffect(() => {
    setStackFilter("__all__");
  }, [inferredHostId, actionType]);

  function patchTargets(ids: number[]) {
    const chosen = ids.map((id) => match.targets.find((t) => t.id === id)).filter((t): t is Target => Boolean(t));
    const first = chosen[0];
    const patch: Partial<Action> = {
      target_ids: ids,
      target_id: ids[0] ?? null,
      host_id: first?.host_id ?? inferredHostId,
    };
    if (isContainer) {
      const names = chosen.map((t) => t.external_id || t.name);
      patch.containers = names;
      patch.container = names[0];
      patch.name = names[0];
    }
    if (isStack) {
      const names = chosen.map((t) => stackLabel(t) || stackOf(t));
      patch.stacks = names;
      patch.stack = names[0];
      patch.name = names[0];
    }
    onChange(patch);
  }

  function patchHosts(ids: number[]) {
    onChange({
      host_ids: ids,
      host_id: ids[0] ?? null,
      target_id: null,
      target_ids: [],
    });
  }

  if (match.kind === "host") {
    const list =
      actionType === "wol"
        ? [...match.hosts].sort((a, b) => {
            const am = hostMacs(a).length ? 0 : 1;
            const bm = hostMacs(b).length ? 0 : 1;
            if (am !== bm) return am - bm;
            return alpha(a.name, b.name);
          })
        : byName(match.hosts);
    const options: MultiSelectOption[] = list.map((h) => ({
      value: String(h.id),
      label: hostOptionLabel(h, actionType),
    }));
    return (
      <MultiSelect
        options={options}
        values={selectedHostIds(action).map(String)}
        onChange={(vals) => patchHosts(vals.map(Number))}
        placeholder="— pick one or more hosts —"
        searchPlaceholder="Search hosts…"
      />
    );
  }

  if (match.kind !== "target") return null;

  const hostFiltered = inferredHostId
    ? match.targets.filter((t) => t.host_id === inferredHostId)
    : showHostFirst
      ? []
      : match.targets;

  const stacks = [...new Set(hostFiltered.map(stackOf).filter(Boolean))].sort(alpha);
  const afterStack =
    isContainer && stackFilter !== "__all__"
      ? hostFiltered.filter((t) => (stackFilter === "__none__" ? !stackOf(t) : stackOf(t) === stackFilter))
      : hostFiltered;

  const targetOptions: MultiSelectOption[] = (isStack ? byName(afterStack) : afterStack).map((t) => ({
    value: String(t.id),
    label:
      (isStack ? stackLabel(t) : t.name) + (!showHostFirst && t.host_name ? ` · ${t.host_name}` : ""),
    group: isContainer ? stackOf(t) || "Not in a stack" : undefined,
  }));

  // Keep a selected item visible even if the stack filter would hide it.
  const optionIds = new Set(targetOptions.map((o) => o.value));
  for (const t of selectedTargets) {
    if (!optionIds.has(String(t.id))) {
      targetOptions.unshift({
        value: String(t.id),
        label: isStack ? stackLabel(t) : t.name,
        group: isContainer ? stackOf(t) || "Not in a stack" : undefined,
      });
    }
  }

  const picker = (
    <MultiSelect
      options={targetOptions}
      values={pickedIds.map(String)}
      disabled={showHostFirst && !inferredHostId}
      onChange={(vals) => patchTargets(vals.map(Number))}
      placeholder={
        inferredHostId || !showHostFirst
          ? isStack
            ? "— pick one or more stacks —"
            : isContainer
              ? "— pick one or more containers —"
              : "— pick one or more targets —"
          : "— pick a host first —"
      }
      searchPlaceholder={isStack ? "Search stacks…" : isContainer ? "Search containers…" : "Search…"}
    />
  );

  if (!showHostFirst && !isContainer) {
    return picker;
  }

  return (
    <div className="space-y-2">
      {showHostFirst && (
        <select
          className="w-full"
          value={inferredHostId ?? ""}
          onChange={(e) => {
            const host_id = e.target.value ? Number(e.target.value) : null;
            const keep = selectedTargets.filter((t) => t.host_id === host_id).map((t) => t.id);
            onChange({
              host_id,
              host_ids: host_id ? [host_id] : [],
              target_id: keep[0] ?? null,
              target_ids: keep,
            });
          }}
        >
          <option value="">— pick a host —</option>
          {hostChoices.map((h) => (
            <option key={h.id} value={h.id}>
              {h.name} ({h.kind})
            </option>
          ))}
        </select>
      )}
      {isContainer && inferredHostId && stacks.length > 0 && (
        <select className="w-full" value={stackFilter} onChange={(e) => setStackFilter(e.target.value)}>
          <option value="__all__">All stacks</option>
          {stacks.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
          {hostFiltered.some((t) => t.kind === "container" && !stackOf(t)) && (
            <option value="__none__">Not in a stack</option>
          )}
        </select>
      )}
      {picker}
    </div>
  );
}
