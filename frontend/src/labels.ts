export const TRIGGERS: { value: string; label: string; help: string }[] = [
  { value: "on_battery", label: "Grid power is lost", help: "Fires after the UPS has been on battery for the hold time." },
  { value: "on_online", label: "Grid power is back", help: "Fires after wall power has been stable for the hold time." },
  { value: "battery_lte", label: "Battery is at or below", help: "Percent remaining. Usually combined with being on battery." },
  { value: "battery_gte", label: "Battery is at or above", help: "Use this on restore so you do not spin disks up at 5%." },
  { value: "runtime_lte", label: "Runtime left is under", help: "Estimated minutes remaining on battery." },
  { value: "load_gte", label: "UPS load is at or above", help: "Percent of UPS capacity. EcoFlow often does not report this." },
  { value: "online_and_battery", label: "Grid is back and battery is at least", help: "Restore only after power is back and the pack has recovered." },
  { value: "always", label: "Whenever this playbook runs", help: "No extra condition — useful for a manual run." },
];

export const ACTIONS: { value: string; label: string; help: string }[] = [
  { value: "notify", label: "Send notification", help: "Uses the Discord / ntfy / webhook you configured in Settings." },
  { value: "wait", label: "Wait a few seconds", help: "Pause before the next step." },
  { value: "wait_tcp", label: "Wait until a host answers", help: "Poll a TCP port (DSM 5001, Proxmox 8006) until it is up." },
  { value: "wol", label: "Wake on LAN", help: "Send a magic packet. Put the MAC on the target." },
  { value: "proxmox_shutdown", label: "Proxmox: ACPI shutdown", help: "Ask the guest to shut down cleanly." },
  { value: "proxmox_stop", label: "Proxmox: force stop", help: "Hard stop. Prefer ACPI shutdown for disks." },
  { value: "proxmox_start", label: "Proxmox: start", help: "Start a VM or LXC." },
  { value: "proxmox_shutdown_node", label: "Proxmox: shut down the node", help: "Last resort. Do not do this to the node that runs Blackstart." },
  { value: "docker_stop", label: "Docker: stop container", help: "Stop one container by name." },
  { value: "docker_start", label: "Docker: start container", help: "Start one container by name." },
  { value: "docker_stop_stack", label: "Docker: stop compose stack", help: "Stop every container in a compose project." },
  { value: "docker_start_stack", label: "Docker: start compose stack", help: "Start every container in a compose project." },
  { value: "synology_shutdown", label: "Synology: shut down NAS", help: "DSM shutdown. Pair with Wake on LAN on restore." },
  { value: "synology_stop_container", label: "Synology: stop container", help: "Via the DSM Docker API." },
  { value: "synology_start_container", label: "Synology: start container", help: "Via the DSM Docker API." },
  { value: "ssh", label: "Run an SSH command", help: "Runs on the selected host with the SSH credentials you saved." },
  { value: "restore_auto", label: "Start everything this outage stopped", help: "Reverses only what Blackstart stopped this episode." },
];

export function triggerLabel(value?: string) {
  return TRIGGERS.find((t) => t.value === value)?.label || humanize(value);
}

export function actionLabel(value?: string) {
  return ACTIONS.find((t) => t.value === value)?.label || humanize(value);
}

export function humanize(value?: string) {
  if (!value) return "—";
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export const HOST_KIND_HELP: Record<string, string> = {
  proxmox: "Talks to the Proxmox API with a token. Discover imports VMs and LXCs.",
  synology: "Talks to DSM. Can shut the NAS down and list Container Manager containers.",
  docker: "Talks to Docker on another box. Prefer SSH unless you have exposed the Docker TCP socket.",
  ssh: "Generic Linux box. Use for custom commands, or Discover to list Docker over SSH.",
};
