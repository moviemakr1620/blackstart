from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    token: str
    username: str
    must_change_password: bool = False


class UserOut(BaseModel):
    username: str
    must_change_password: bool = False


class UpsSourceIn(BaseModel):
    name: str
    kind: str = Field(pattern="^(peanut|nut|simulator)$")
    enabled: bool = True
    host: str = ""
    port: int = 0
    base_url: str = ""
    device_name: str = "ecoflow"
    username: str = ""
    password: str | None = None
    poll_interval_sec: int = 5
    extra: dict[str, Any] = Field(default_factory=dict)


class UpsSourceOut(BaseModel):
    id: int
    name: str
    kind: str
    enabled: bool
    host: str
    port: int
    base_url: str
    device_name: str
    username: str
    has_password: bool
    poll_interval_sec: int
    extra: dict[str, Any] = Field(default_factory=dict)


class UpsStatus(BaseModel):
    source_id: int | None = None
    source_name: str = ""
    source_kind: str = ""
    connected: bool = False
    error: str = ""
    device: str = ""
    model: str = ""
    status_raw: str = ""
    status: str = "unknown"  # online | on_battery | low_battery | charging | unknown
    on_battery: bool = False
    charging: bool = False
    charge: float | None = None
    runtime_sec: float | None = None
    load_pct: float | None = None
    watts: float | None = None
    input_voltage: float | None = None
    output_voltage: float | None = None
    battery_voltage: float | None = None
    temperature: float | None = None
    ts: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class HostIn(BaseModel):
    name: str
    kind: str = Field(pattern="^(proxmox|pbs|synology|docker|ssh|k3s|omv)$")
    enabled: bool = True
    address: str
    port: int = 0
    verify_tls: bool = True
    credentials: dict[str, Any] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)


class HostOut(BaseModel):
    id: int
    name: str
    kind: str
    enabled: bool
    address: str
    port: int
    verify_tls: bool
    extra: dict[str, Any] = Field(default_factory=dict)
    last_ok_at: datetime | None = None
    last_error: str = ""
    credential_keys: list[str] = Field(default_factory=list)
    credentials_error: str = ""


class TargetIn(BaseModel):
    host_id: int | None = None
    kind: str
    name: str
    external_id: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)
    critical: bool = False
    enabled: bool = True
    notes: str = ""


class TargetOut(BaseModel):
    id: int
    host_id: int | None
    host_name: str | None = None
    host_kind: str | None = None
    kind: str
    name: str
    external_id: str
    extra: dict[str, Any] = Field(default_factory=dict)
    critical: bool
    enabled: bool
    notes: str
    last_status: str
    last_checked: datetime | None = None


class PolicyStepIn(BaseModel):
    id: int | None = None
    order: int = 10
    name: str
    enabled: bool = True
    trigger: dict[str, Any] = Field(default_factory=dict)
    action: dict[str, Any] = Field(default_factory=dict)
    delay_before_sec: int = 0
    timeout_sec: int = 120
    continue_on_error: bool = True


class PolicyIn(BaseModel):
    name: str
    kind: str = Field(pattern="^(shed|restore)$")
    description: str = ""
    enabled: bool = True
    ups_source_id: int | None = None
    priority: int = 100
    steps: list[PolicyStepIn] = Field(default_factory=list)


class PolicyStepOut(PolicyStepIn):
    id: int


class PolicyOut(BaseModel):
    id: int
    name: str
    kind: str
    description: str
    enabled: bool
    ups_source_id: int | None = None
    priority: int
    steps: list[PolicyStepOut] = Field(default_factory=list)
    updated_at: datetime | None = None
    # Plain-language reason this playbook will not run on a real outage, if any.
    binding_warning: str = ""


class RunOut(BaseModel):
    id: int
    policy_id: int | None
    episode_id: int | None
    kind: str
    trigger: str
    dry_run: bool
    status: str
    started_at: datetime
    finished_at: datetime | None


class RunEventOut(BaseModel):
    id: int
    ts: datetime
    level: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)


class RunDetailOut(RunOut):
    events: list[RunEventOut] = Field(default_factory=list)


class EpisodeOut(BaseModel):
    id: int
    started_at: datetime
    ended_at: datetime | None
    cause: str
    status: str
    min_charge: float | None


class AppSettingsOut(BaseModel):
    live_actions: bool
    engine_paused: bool
    sample_retention_hours: int
    run_retention_days: int
    default_restore_hold_sec: int
    default_shed_hold_sec: int
    protect_critical: bool
    primary_ups_id: int | None = None
    timezone: str = ""
    ups_stale_alert_sec: int
    heartbeat_url_set: bool = False


class AppSettingsIn(BaseModel):
    live_actions: bool | None = None
    engine_paused: bool | None = None
    sample_retention_hours: int | None = Field(default=None, ge=1, le=24 * 90)
    run_retention_days: int | None = Field(default=None, ge=1, le=3650)
    default_restore_hold_sec: int | None = Field(default=None, ge=0, le=86400)
    default_shed_hold_sec: int | None = Field(default=None, ge=0, le=86400)
    protect_critical: bool | None = None
    primary_ups_id: int | None = None
    timezone: str | None = None
    ups_stale_alert_sec: int | None = Field(default=None, ge=10, le=3600)
    # "" clears it; omit to keep the stored one.
    heartbeat_url: str | None = None


class SimulateIn(BaseModel):
    event: str  # pull_plug | restore_grid | set_charge | set_load | set_status
    charge: float | None = None
    load_watts: float | None = None
    status: str | None = None


class ManualRunIn(BaseModel):
    dry_run: bool = True
    step_ids: list[int] | None = None


class PasswordIn(BaseModel):
    current_password: str
    new_password: str
