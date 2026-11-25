export type UpsStatus = {
  source_id?: number;
  source_name?: string;
  source_kind?: string;
  connected?: boolean;
  error?: string;
  device?: string;
  model?: string;
  status_raw?: string;
  status?: string;
  on_battery?: boolean;
  charging?: boolean;
  charge?: number | null;
  runtime_sec?: number | null;
  load_pct?: number | null;
  watts?: number | null;
  input_voltage?: number | null;
  input_watts?: number | null;
  output_voltage?: number | null;
  temperature?: number | null;
  ts?: string;
  power_mode?: string;
  running_run_id?: number | null;
  reported_keys?: string[];
  primary_id?: number | null;
  sources?: UpsStatus[];
};

export type UpsSource = {
  id: number;
  name: string;
  kind: "peanut" | "nut" | "simulator";
  enabled: boolean;
  host: string;
  port: number;
  base_url: string;
  device_name: string;
  username: string;
  has_password: boolean;
  poll_interval_sec: number;
};

export type Host = {
  id: number;
  name: string;
  kind: "proxmox" | "pbs" | "synology" | "docker" | "ssh" | "k3s" | "omv";
  enabled: boolean;
  address: string;
  port: number;
  verify_tls: boolean;
  extra: Record<string, unknown>;
  last_ok_at?: string | null;
  last_error: string;
  credential_keys: string[];
  credentials_error?: string;
};

export type Target = {
  id: number;
  host_id: number | null;
  host_name?: string | null;
  host_kind?: string | null;
  kind: string;
  name: string;
  external_id: string;
  extra: Record<string, unknown>;
  critical: boolean;
  enabled: boolean;
  notes: string;
  last_status: string;
};

export type Trigger = {
  type?: string;
  status?: string;
  value?: number;
  minutes?: number;
  hold_seconds?: number;
  require_on_battery?: boolean;
  require_online?: boolean;
  all?: Trigger[];
  any?: Trigger[];
};

export type Action = {
  type?: string;
  target_id?: number | null;
  target_ids?: number[];
  host_id?: number | null;
  host_ids?: number[];
  message?: string;
  command?: string;
  url?: string;
  mac?: string;
  seconds?: number;
  node?: string;
  port?: number;
  container?: string;
  containers?: string[];
  stack?: string;
  stacks?: string[];
  name?: string;
  channel_id?: number | "all" | null;
};

export type PolicyStep = {
  id?: number;
  order: number;
  name: string;
  enabled: boolean;
  trigger: Trigger;
  action: Action;
  delay_before_sec: number;
  timeout_sec: number;
  continue_on_error: boolean;
};

export type Policy = {
  id: number;
  name: string;
  kind: "shed" | "restore";
  description: string;
  enabled: boolean;
  ups_source_id: number | null;
  priority: number;
  steps: PolicyStep[];
  binding_warning?: string;
};

export type Run = {
  id: number;
  policy_id: number | null;
  episode_id: number | null;
  kind: string;
  trigger: string;
  dry_run: boolean;
  status: string;
  started_at: string;
  finished_at?: string | null;
};

export type RunEvent = {
  id: number;
  ts: string;
  level: string;
  message: string;
  payload?: Record<string, unknown>;
};

export type Episode = {
  id: number;
  started_at: string;
  ended_at?: string | null;
  cause: string;
  status: string;
  min_charge?: number | null;
};

export type NotifyChannel = {
  id: number;
  name: string;
  kind: "discord" | "ntfy" | "gotify" | "webhook";
  enabled: boolean;
  url_hint: string;
  has_url: boolean;
  topic: string;
  has_token: boolean;
  error?: string;
};

export type AppSettings = {
  live_actions: boolean;
  engine_paused: boolean;
  sample_retention_hours: number;
  run_retention_days: number;
  ups_stale_alert_sec: number;
  heartbeat_url_set: boolean;
  heartbeat_url?: string;
  default_restore_hold_sec: number;
  default_shed_hold_sec: number;
  protect_critical: boolean;
  primary_ups_id?: number | null;
  timezone?: string;
};

export type Sample = {
  ts: string;
  status: string;
  charge: number | null;
  runtime_sec: number | null;
  load_pct: number | null;
  watts: number | null;
};
