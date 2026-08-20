export const TRIGGERS: { value: string; label: string; help: string }[] = [
  { value: "on_battery", label: "Grid power is lost", help: "Fires after the UPS has been on battery for the hold time." },
  { value: "on_online", label: "Grid power is back", help: "Fires after wall power has been stable for the hold time." },
  { value: "battery_lte", label: "Battery is at or below", help: "Percent remaining. Usually combined with being on battery." },
  { value: "battery_gte", label: "Battery is at or above", help: "Use this on restore so you do not spin disks up at 5%." },
  { value: "runtime_lte", label: "Runtime left is under", help: "Estimated minutes remaining on battery." },
  { value: "load_gte", label: "UPS load is at or above", help: "Percent of UPS capacity." },
  { value: "online_and_battery", label: "Grid is back and battery is at least", help: "Restore only after power is back and the pack has recovered." },
  { value: "always", label: "Whenever this playbook runs", help: "No extra condition — useful for a manual run." },
];

export const CATEGORIES: { id: string; label: string }[] = [
  { id: "notify", label: "Notify" },
  { id: "proxmox", label: "Proxmox" },
  { id: "docker", label: "Docker" },
  { id: "synology", label: "Synology" },
  { id: "network", label: "Network" },
  { id: "k3s", label: "k3s" },
  { id: "omv", label: "OpenMediaVault" },
  { id: "ssh", label: "SSH" },
  { id: "restore", label: "Restore" },
];

export const ACTIONS: {
  value: string;
  label: string;
  help: string;
  category: string;
  target: "none" | "notify" | "vm" | "container" | "stack" | "nas" | "proxmox-host" | "host" | "wol" | "k3s" | "k3s-host" | "omv";
}[] = [
  { value: "notify", label: "Send notification", help: "Picks a channel from Settings.", category: "notify", target: "notify" },
  { value: "proxmox_shutdown", label: "ACPI shutdown", help: "Ask the guest to shut down cleanly.", category: "proxmox", target: "vm" },
  { value: "proxmox_stop", label: "Force stop", help: "Hard stop. Prefer ACPI for disks.", category: "proxmox", target: "vm" },
  { value: "proxmox_start", label: "Start", help: "Start a VM or LXC.", category: "proxmox", target: "vm" },
  { value: "proxmox_shutdown_node", label: "Shut down the node", help: "Last resort. Never the node that runs Blackstart.", category: "proxmox", target: "proxmox-host" },
  { value: "docker_stop", label: "Stop container", help: "Stop one container.", category: "docker", target: "container" },
  { value: "docker_start", label: "Start container", help: "Start one container.", category: "docker", target: "container" },
  { value: "docker_stop_stack", label: "Stop compose stack", help: "Stop a compose project.", category: "docker", target: "stack" },
  { value: "docker_start_stack", label: "Start compose stack", help: "Start a compose project.", category: "docker", target: "stack" },
  { value: "synology_shutdown", label: "Shut down NAS / HA cluster", help: "Standalone DSM, or the whole SHA cluster if HA is enabled.", category: "synology", target: "nas" },
  { value: "synology_stop_container", label: "Stop container", help: "Via DSM Container Manager.", category: "synology", target: "container" },
  { value: "synology_start_container", label: "Start container", help: "Via DSM Container Manager.", category: "synology", target: "container" },
  { value: "k3s_drain", label: "Drain node", help: "kubectl drain so pods move off the node.", category: "k3s", target: "k3s" },
  { value: "k3s_uncordon", label: "Uncordon node", help: "Allow scheduling again after restore.", category: "k3s", target: "k3s" },
  { value: "k3s_shutdown_node", label: "Drain + shut down node", help: "Drain, then SSH shutdown -h now to that Pi/node.", category: "k3s", target: "k3s" },
  { value: "k3s_shutdown_cluster", label: "Shut down whole cluster", help: "Drain and power off every node, server last.", category: "k3s", target: "k3s-host" },
  { value: "omv_shutdown", label: "Shut down OMV", help: "RPC halt, SSH fallback.", category: "omv", target: "omv" },
  { value: "omv_reboot", label: "Reboot OMV", help: "RPC reboot.", category: "omv", target: "omv" },
  { value: "wol", label: "Wake on LAN", help: "Magic packet. HA sends both node MACs.", category: "network", target: "wol" },
  { value: "wait", label: "Wait a few seconds", help: "Pause before the next step.", category: "network", target: "none" },
  { value: "wait_tcp", label: "Wait until a host answers", help: "Poll DSM 5001 / Proxmox 8006 until it is up.", category: "network", target: "host" },
  { value: "ssh", label: "Run a command", help: "Uses the SSH credentials on that host.", category: "ssh", target: "host" },
  { value: "restore_auto", label: "Start everything this outage stopped", help: "Reverses only what Blackstart stopped.", category: "restore", target: "none" },
];

export function actionMeta(value?: string) {
  return ACTIONS.find((t) => t.value === value);
}

export function categoryOf(value?: string) {
  return actionMeta(value)?.category || "notify";
}

export function triggerLabel(value?: string) {
  return TRIGGERS.find((t) => t.value === value)?.label || humanize(value);
}

export function actionLabel(value?: string) {
  return actionMeta(value)?.label || humanize(value);
}

export function humanize(value?: string) {
  if (!value) return "—";
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export const HOST_KIND_HELP: Record<string, string> = {
  proxmox: "Talks to the Proxmox API with a token. Discover imports VMs and LXCs.",
  synology:
    "Talks to DSM. For SHA, use the cluster virtual IP, tick HA, and save both node MACs so shutdown/WOL hit the pair. Container Manager on the NAS is this host — not a Docker host.",
  docker:
    "Not a dedicated Docker appliance. This is how Blackstart talks to Docker: SSH into a Proxmox VM (recommended) or a TCP socket. Synology containers belong on the Synology host instead.",
  ssh: "Generic Linux box. Custom commands, or Discover to list Docker over SSH.",
  k3s: "SSH to the k3s control plane. Discover imports nodes. Shutdown drains, then poweroffs each node (workers first).",
  omv: "OpenMediaVault via its RPC API, with SSH fallback. Use the OMV web user. Discover also lists Docker if the SSH user can run docker.",
};
