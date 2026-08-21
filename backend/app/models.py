from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, default="null")


class NotifyChannel(Base):
    __tablename__ = "notify_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), default="discord")  # discord | ntfy | gotify | webhook
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    url: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[str] = mapped_column(String(160), default="")
    token_enc: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UpsSource(Base):
    __tablename__ = "ups_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))  # peanut | nut | simulator
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    host: Mapped[str] = mapped_column(String(255), default="")
    port: Mapped[int] = mapped_column(Integer, default=0)
    base_url: Mapped[str] = mapped_column(String(255), default="")
    device_name: Mapped[str] = mapped_column(String(120), default="ecoflow")
    username: Mapped[str] = mapped_column(String(120), default="")
    password_enc: Mapped[str] = mapped_column(Text, default="")
    poll_interval_sec: Mapped[int] = mapped_column(Integer, default=5)
    extra_json: Mapped[str] = mapped_column(Text, default="{}")


class Host(Base):
    __tablename__ = "hosts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))  # proxmox | synology | docker | ssh
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    address: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=0)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=False)
    credentials_enc: Mapped[str] = mapped_column(Text, default="")
    extra_json: Mapped[str] = mapped_column(Text, default="{}")
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    targets: Mapped[list[Target]] = relationship(back_populates="host", cascade="all, delete-orphan")


class Target(Base):
    __tablename__ = "targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host_id: Mapped[int | None] = mapped_column(ForeignKey("hosts.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(30))  # vm | lxc | container | stack | nas | host | custom
    name: Mapped[str] = mapped_column(String(160))
    external_id: Mapped[str] = mapped_column(String(160), default="")
    extra_json: Mapped[str] = mapped_column(Text, default="{}")
    critical: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    last_status: Mapped[str] = mapped_column(String(40), default="unknown")
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    host: Mapped[Host | None] = relationship(back_populates="targets")


class Policy(Base):
    __tablename__ = "policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(20))  # shed | restore
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    ups_source_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    steps: Mapped[list[PolicyStep]] = relationship(
        back_populates="policy",
        cascade="all, delete-orphan",
        order_by="PolicyStep.order",
    )


class PolicyStep(Base):
    __tablename__ = "policy_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("policies.id", ondelete="CASCADE"), index=True)
    order: Mapped[int] = mapped_column(Integer, default=10)
    name: Mapped[str] = mapped_column(String(160))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    trigger_json: Mapped[str] = mapped_column(Text, default="{}")
    action_json: Mapped[str] = mapped_column(Text, default="{}")
    delay_before_sec: Mapped[int] = mapped_column(Integer, default=0)
    timeout_sec: Mapped[int] = mapped_column(Integer, default=120)
    continue_on_error: Mapped[bool] = mapped_column(Boolean, default=True)

    policy: Mapped[Policy] = relationship(back_populates="steps")


class Episode(Base):
    __tablename__ = "episodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cause: Mapped[str] = mapped_column(String(80), default="on_battery")
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | closed
    min_charge: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class EpisodeAction(Base):
    __tablename__ = "episode_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    host_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    step_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(60))
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    restore_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    detail: Mapped[str] = mapped_column(Text, default="")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    policy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="shed")
    trigger: Mapped[str] = mapped_column(String(80), default="manual")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | ok | error | aborted
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    events: Mapped[list[RunEvent]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="RunEvent.id",
    )


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    level: Mapped[str] = mapped_column(String(20), default="info")
    message: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")

    run: Mapped[Run] = relationship(back_populates="events")


class UpsSample(Base):
    __tablename__ = "ups_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="unknown")
    charge: Mapped[float | None] = mapped_column(Float, nullable=True)
    runtime_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    load_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    watts: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_voltage: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(80), default="system")
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(Text, default="")
