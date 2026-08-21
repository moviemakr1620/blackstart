import { useEffect, useMemo, useState } from "react";
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

function containerGroups(list: Target[]): { stack: string; items: Target[] }[] {
  const map = new Map<string, Target[]>();
  for (const t of list) {
    const stack = stackOf(t) || "Not in a stack";
    const bucket = map.get(stack);
    if (bucket) bucket.push(t);
    else map.set(stack, [t]);
  }
  const keys = [...map.keys()].sort((a, b) => {
    if (a === "Not in a stack") return 1;
    if (b === "Not in a stack") return -1;
    return alpha(a, b);
  });
  return keys.map((stack) => ({ stack, items: byName(map.get(stack) || []) }));
}

const DOCKER_ACTIONS = new Set(["docker_stop", "docker_start", "docker_stop_stack", "docker_start_stack"]);

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

  const selectedTarget = targets.find((t) => t.id === action.target_id) || null;
  const inferredHostId = action.host_id ?? selectedTarget?.host_id ?? null;

  const dockerHosts = useMemo(() => {
    const ids = new Set(match.targets.map((t) => t.host_id).filter((id): id is number => Boolean(id)));
    return byName(
      hosts.filter(
        (h) => h.kind === "docker" || h.kind === "ssh" || h.kind === "omv" || ids.has(h.id),
      ),
    );
  }, [hosts, match.targets]);

  const uniqueHostIds = useMemo(() => {
    const ids = new Set(match.targets.map((t) => t.host_id).filter((id): id is number => Boolean(id)));
    return ids;
  }, [match.targets]);

  const showHostFirst = isDocker || uniqueHostIds.size > 1;
  const hostChoices = isDocker
    ? dockerHosts
    : byName(hosts.filter((h) => uniqueHostIds.has(h.id)));

  const [stackFilter, setStackFilter] = useState("__all__");
  useEffect(() => {
    setStackFilter("__all__");
  }, [inferredHostId, actionType]);

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
    return (
      <select
        className="w-full"
        value={action.host_id ?? ""}
        onChange={(e) =>
          onChange({
            host_id: e.target.value ? Number(e.target.value) : null,
            target_id: null,
          })
        }
      >
        <option value="">— pick a host —</option>
        {list.map((h) => (
          <option key={h.id} value={h.id}>
            {hostOptionLabel(h, actionType)}
          </option>
        ))}
      </select>
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

  const targetSelect = isContainer ? (
    <select
      className="w-full"
      value={action.target_id ?? ""}
      disabled={showHostFirst && !inferredHostId}
      onChange={(e) => {
        const id = e.target.value ? Number(e.target.value) : null;
        const t = match.targets.find((x) => x.id === id);
        onChange({
          target_id: id,
          host_id: t?.host_id ?? inferredHostId,
          container: t?.external_id || t?.name || action.container,
        });
      }}
    >
      <option value="">{inferredHostId || !showHostFirst ? "— pick a container —" : "— pick a host first —"}</option>
      {containerGroups(afterStack.filter((t) => t.kind === "container")).map((g) => (
        <optgroup key={g.stack} label={g.stack}>
          {g.items.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  ) : (
    <select
      className="w-full"
      value={action.target_id ?? ""}
      disabled={showHostFirst && !inferredHostId}
      onChange={(e) => {
        const id = e.target.value ? Number(e.target.value) : null;
        const t = match.targets.find((x) => x.id === id);
        onChange({
          target_id: id,
          host_id: t?.host_id ?? inferredHostId,
          stack: isStack ? t?.external_id || (t ? stackOf(t) : "") || action.stack : action.stack,
        });
      }}
    >
      <option value="">
        {inferredHostId || !showHostFirst
          ? isStack
            ? "— pick a stack —"
            : "— pick a target —"
          : "— pick a host first —"}
      </option>
      {byName(afterStack).map((t) => (
        <option key={t.id} value={t.id}>
          {isStack ? (t.external_id || t.name.replace(/^stack:/, "")) : t.name}
          {!showHostFirst && t.host_name ? ` · ${t.host_name}` : ""}
        </option>
      ))}
    </select>
  );

  if (!showHostFirst && !isContainer) {
    return targetSelect;
  }

  return (
    <div className="space-y-2">
      {showHostFirst && (
        <select
          className="w-full"
          value={inferredHostId ?? ""}
          onChange={(e) => {
            const host_id = e.target.value ? Number(e.target.value) : null;
            const keep =
              selectedTarget && host_id && selectedTarget.host_id === host_id ? selectedTarget.id : null;
            onChange({ host_id, target_id: keep });
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
      {targetSelect}
    </div>
  );
}
