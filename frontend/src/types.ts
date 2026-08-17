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
  output_voltage?: number | null;
  temperature?: number | null;
  ts?: string;
  power_mode?: string;
  running_run_id?: number | null;
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
  kind: "proxmox" | "synology" | "docker" | "ssh";
  enabled: boolean;
  address: string;
  port: number;
  verify_tls: boolean;
  extra: Record<string, unknown>;
  last_ok_at?: string | null;
  last_error: string;
  credential_keys: string[];
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
  host_id?: number | null;
  message?: string;
  command?: string;
  url?: string;
  mac?: string;
  seconds?: number;
  node?: string;
  port?: number;
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
  priority: number;
  steps: PolicyStep[];
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

export type AppSettings = {
  live_actions: boolean;
  engine_paused: boolean;
  restore_only_what_we_stopped: boolean;
  sample_retention_hours: number;
  notify_url: string;
  notify_kind: string;
  ntfy_topic: string;
  gotify_token: string;
  default_restore_hold_sec: number;
  default_shed_hold_sec: number;
  protect_critical: boolean;
};

export type Sample = {
  ts: string;
  status: string;
  charge: number | null;
  runtime_sec: number | null;
  load_pct: number | null;
  watts: number | null;
};
