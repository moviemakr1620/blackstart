from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.nut import NutClient, snapshot_from_vars
from app.adapters.peanut import PeanutClient
from app.db import SessionLocal
from app.engine.bus import bus
from app.engine.evaluator import ConditionState, mark_fired, trigger_ready
from app.engine.executor import execute_action, inverse_action
from app.engine.simulator import Simulator
from app.models import (
    AuditLog,
    Episode,
    EpisodeAction,
    Host as HostModel,
    Policy,
    PolicyStep,
    Run,
    RunEvent,
    Target,
    UpsSample,
    UpsSource,
    utcnow,
)
from app.store import get_settings_map


def _loads(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


class Engine:
    def __init__(self) -> None:
        self.simulator = Simulator()
        self.conditions = ConditionState()
        self.last_status: dict[str, Any] = {}
        self.snapshots: dict[int, dict[str, Any]] = {}
        self.primary_id: int | None = None
        self.last_error: str = ""
        self.power_mode: str = "unknown"  # online | on_battery | shedding | restoring
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self._abort = False
        self.running_run_id: int | None = None
        self._prev_on_battery: bool | None = None
        self._prev_ob: dict[int, bool | None] = {}
        self._online_since: float | None = None

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="blackstart-engine")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def abort(self) -> None:
        self._abort = True

    async def _loop(self) -> None:
        while not self._stop.is_set():
            interval = 5
            try:
                async with SessionLocal() as session:
                    interval = await self.tick(session)
                    await session.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — engine must never die
                self.last_error = str(exc)
                await bus.publish("engine.error", {"error": str(exc)})
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(2, interval))
            except asyncio.TimeoutError:
                pass

    async def read_ups(self, session: AsyncSession) -> tuple[dict[str, Any], UpsSource]:
        source = (
            await session.execute(select(UpsSource).where(UpsSource.enabled.is_(True)).order_by(UpsSource.id).limit(1))
        ).scalar_one_or_none()
        if source is None:
            raise RuntimeError("No enabled UPS source. Add PeaNUT, NUT, or the simulator in Settings.")
        snap = await self.read_one(session, source)
        return snap, source

    async def read_one(self, session: AsyncSession, source: UpsSource) -> dict[str, Any]:
        if source.kind == "simulator":
            snap = self.simulator.snapshot()
        elif source.kind == "peanut":
            from app.security import decrypt_secret

            client = PeanutClient(
                base_url=source.base_url or f"http://{source.host}:{source.port or 8080}",
                username=source.username,
                password=decrypt_secret(source.password_enc),
            )
            snap = await client.snapshot(source.device_name)
        elif source.kind == "nut":
            from app.security import decrypt_secret

            client = NutClient(
                host=source.host,
                port=source.port or 3493,
                username=source.username,
                password=decrypt_secret(source.password_enc),
            )
            name = source.device_name
            if not name:
                listed = await client.list_ups()
                if not listed:
                    raise RuntimeError("NUT server has no UPS devices")
                name = listed[0][0]
            vars_ = await client.list_vars(name)
            snap = snapshot_from_vars(vars_)
            snap["device"] = name
        else:
            raise RuntimeError(f"Unknown UPS source kind: {source.kind}")

        snap["source_id"] = source.id
        snap["source_name"] = source.name
        snap["source_kind"] = source.kind
        snap["connected"] = True
        snap["error"] = ""
        snap["ts"] = utcnow().isoformat()
        return snap

    async def tick(self, session: AsyncSession) -> int:
        settings = await get_settings_map(session)
        sources = (
            (await session.execute(select(UpsSource).where(UpsSource.enabled.is_(True)).order_by(UpsSource.id)))
            .scalars()
            .all()
        )
        if not sources:
            self.last_error = "No enabled UPS source."
            self.last_status = {"connected": False, "error": self.last_error, "status": "unknown", "sources": []}
            await bus.publish("ups.sample", self.last_status)
            return 5

        interval = min(max(2, s.poll_interval_sec) for s in sources)
        now = time.monotonic()
        for src in sources:
            try:
                snap = await self.read_one(session, src)
            except Exception as exc:  # noqa: BLE001
                snap = {
                    "connected": False,
                    "error": str(exc),
                    "status": "unknown",
                    "on_battery": False,
                    "source_id": src.id,
                    "source_name": src.name,
                    "source_kind": src.kind,
                }
            self.snapshots[src.id] = snap
            if snap.get("connected"):
                session.add(
                    UpsSample(
                        source_id=src.id,
                        status=str(snap.get("status") or ""),
                        charge=snap.get("charge"),
                        runtime_sec=snap.get("runtime_sec"),
                        load_pct=snap.get("load_pct"),
                        watts=snap.get("watts"),
                        input_voltage=snap.get("input_voltage"),
                        raw_json=json.dumps(snap.get("raw") or {})[:8000],
                    )
                )

        await self._prune_samples(session, int(settings.get("sample_retention_hours") or 168))

        primary_id = settings.get("primary_ups_id")
        ids = {s.id for s in sources}
        if primary_id not in ids:
            primary_id = sources[0].id
        self.primary_id = int(primary_id)
        primary = dict(self.snapshots.get(self.primary_id) or {})
        self.last_status = {
            **primary,
            "primary_id": self.primary_id,
            "sources": [self.snapshots[s.id] for s in sources],
        }
        self.last_error = str(primary.get("error") or "")
        await bus.publish("ups.sample", self.last_status)

        if settings.get("engine_paused"):
            return interval

        policies = (
            (
                await session.execute(
                    select(Policy)
                    .where(Policy.enabled.is_(True))
                    .options(selectinload(Policy.steps))
                    .order_by(Policy.priority, Policy.id)
                )
            )
            .scalars()
            .unique()
            .all()
        )
        for src in sources:
            snap = self.snapshots.get(src.id) or {}
            bound = [p for p in policies if p.ups_source_id == src.id]
            if bound:
                await self._evaluate_source(session, settings, src.id, snap, bound, now)
        return interval

    async def _evaluate_source(
        self,
        session: AsyncSession,
        settings: dict[str, Any],
        source_id: int,
        snap: dict[str, Any],
        policies: list[Policy],
        now: float,
    ) -> None:
        if not snap.get("connected") and snap.get("error"):
            return
        on_battery = bool(snap.get("on_battery"))
        prev = self._prev_ob.get(source_id)
        cause = f"on_battery:{source_id}"

        if on_battery:
            if prev is False or prev is None:
                await self._open_episode(session, snap, cause=cause)
                self.conditions.reset_source(source_id)
                if source_id == self.primary_id:
                    self.power_mode = "on_battery"
                    await bus.publish("state.changed", {"mode": self.power_mode})
        else:
            if source_id == self.primary_id and self.power_mode not in {"restoring", "shedding"}:
                if prev is True or self.power_mode == "unknown":
                    self.power_mode = "online"
                    if prev is True:
                        await bus.publish("state.changed", {"mode": self.power_mode})
        self._prev_ob[source_id] = on_battery
        if source_id == self.primary_id:
            self._prev_on_battery = on_battery

        episode = (
            await session.execute(
                select(Episode)
                .where(Episode.status == "open", Episode.cause == cause)
                .order_by(Episode.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if episode and snap.get("charge") is not None:
            current = float(snap["charge"])
            if episode.min_charge is None or current < episode.min_charge:
                episode.min_charge = current

        due: list[tuple[Policy, PolicyStep]] = []
        for policy in policies:
            if policy.kind == "restore" and episode is None:
                continue
            prefix = f"{source_id}:{policy.kind}:"
            hold_default = (
                float(settings.get("default_shed_hold_sec") or 0)
                if policy.kind == "shed"
                else float(settings.get("default_restore_hold_sec") or 0)
            )
            for step in policy.steps:
                if not step.enabled:
                    continue
                trigger = _loads(step.trigger_json)
                key = f"{prefix}{step.id}"
                if trigger_ready(key, trigger, snap, now, self.conditions, default_hold=hold_default):
                    due.append((policy, step))

        if due:
            await self._run_due(session, due, settings, snap, episode)

        if (
            episode
            and not on_battery
            and episode.status == "open"
            and (source_id != self.primary_id or self.power_mode in {"online", "restoring"})
        ):
            pending = (
                await session.execute(
                    select(EpisodeAction).where(
                        EpisodeAction.episode_id == episode.id,
                        EpisodeAction.restore_pending.is_(True),
                    )
                )
            ).scalars().all()
            restore_policies = [p for p in policies if p.kind == "restore"]
            restore_done = all(
                f"{source_id}:restore:{s.id}" in self.conditions.fired
                for p in restore_policies
                for s in p.steps
                if s.enabled
            )
            if restore_done and not pending:
                episode.status = "closed"
                episode.ended_at = utcnow()
                await bus.publish("episode.closed", {"id": episode.id})

    async def _open_episode(self, session: AsyncSession, snap: dict[str, Any], cause: str = "on_battery") -> Episode:
        existing = (
            await session.execute(select(Episode).where(Episode.status == "open", Episode.cause == cause).limit(1))
        ).scalar_one_or_none()
        if existing:
            existing.status = "closed"
            existing.ended_at = utcnow()
            existing.notes = (existing.notes or "") + " | superseded by new outage"
        ep = Episode(
            cause=cause,
            status="open",
            min_charge=snap.get("charge"),
            notes=str(snap.get("status_raw") or ""),
        )
        session.add(ep)
        await session.flush()
        session.add(AuditLog(actor="engine", action="episode.open", detail=f"id={ep.id}"))
        await bus.publish("episode.opened", {"id": ep.id, "charge": snap.get("charge")})
        return ep

    async def _run_due(
        self,
        session: AsyncSession,
        due: list[tuple[Policy, PolicyStep]],
        settings: dict[str, Any],
        snap: dict[str, Any],
        episode: Episode | None,
    ) -> None:
        async with self._lock:
            # Group by policy to make a readable run
            by_policy: dict[int, list[PolicyStep]] = {}
            policy_map: dict[int, Policy] = {}
            for policy, step in due:
                policy_map[policy.id] = policy
                by_policy.setdefault(policy.id, []).append(step)

            for pid, steps in by_policy.items():
                policy = policy_map[pid]
                steps.sort(key=lambda s: s.order)
                await self._execute_steps(
                    session,
                    policy=policy,
                    steps=steps,
                    settings=settings,
                    snap=snap,
                    episode=episode,
                    trigger="auto",
                    dry_run=False,
                    mark=True,
                )

    async def run_policy(
        self,
        session: AsyncSession,
        policy: Policy,
        *,
        dry_run: bool,
        trigger: str,
        step_ids: list[int] | None = None,
    ) -> Run:
        settings = await get_settings_map(session)
        steps = [s for s in policy.steps if s.enabled]
        if step_ids:
            wanted = set(step_ids)
            steps = [s for s in steps if s.id in wanted]
        steps.sort(key=lambda s: s.order)
        episode = (
            await session.execute(select(Episode).where(Episode.status == "open").order_by(Episode.id.desc()).limit(1))
        ).scalar_one_or_none()
        async with self._lock:
            return await self._execute_steps(
                session,
                policy=policy,
                steps=steps,
                settings=settings,
                snap=self.last_status or {},
                episode=episode,
                trigger=trigger,
                dry_run=dry_run,
                mark=False,
            )

    async def run_restore_auto(self, session: AsyncSession, dry_run: bool = False) -> Run:
        settings = await get_settings_map(session)
        episode = (
            await session.execute(select(Episode).where(Episode.status == "open").order_by(Episode.id.desc()).limit(1))
        ).scalar_one_or_none()
        run = Run(
            policy_id=None,
            episode_id=episode.id if episode else None,
            kind="restore",
            trigger="restore_auto",
            dry_run=dry_run or not settings.get("live_actions"),
            status="running",
        )
        session.add(run)
        await session.flush()
        self.running_run_id = run.id
        try:
            if not episode:
                await self._log(session, run, "warn", "No open outage episode — nothing to auto-restore")
                run.status = "ok"
                run.finished_at = utcnow()
                return run
            actions = (
                (
                    await session.execute(
                        select(EpisodeAction)
                        .where(
                            EpisodeAction.episode_id == episode.id,
                            EpisodeAction.restore_pending.is_(True),
                            EpisodeAction.success.is_(True),
                        )
                        .order_by(EpisodeAction.id.desc())
                    )
                )
                .scalars()
                .all()
            )
            if not actions:
                await self._log(session, run, "info", "Nothing was shed this episode")
            for rec in actions:
                if self._abort:
                    run.status = "aborted"
                    break
                target = await session.get(Target, rec.target_id) if rec.target_id else None
                inverse = inverse_action(rec.action)
                host = None
                host_id = rec.host_id or (target.host_id if target else None)
                if host_id:
                    host = await session.get(HostModel, host_id)
                if not inverse or (not target and not host):
                    rec.restore_pending = False
                    continue
                label = target.name if target else host.name if host else inverse
                await self._log(session, run, "info", f"Auto-restore {label} via {inverse}")
                try:
                    result = await execute_action(
                        session,
                        {"type": inverse},
                        target=target,
                        host=host,
                        dry_run=run.dry_run,
                        live=bool(settings.get("live_actions")),
                        protect_critical=bool(settings.get("protect_critical")),
                        notify_cfg=settings,
                        episode_id=episode.id,
                    )
                    rec.restore_pending = False
                    await self._log(session, run, "info", f"Restored {label}", result)
                except Exception as exc:  # noqa: BLE001
                    await self._log(session, run, "error", f"Failed to restore {label}: {exc}")
            if run.status != "aborted":
                run.status = "ok"
        except Exception as exc:  # noqa: BLE001
            run.status = "error"
            await self._log(session, run, "error", str(exc))
        finally:
            run.finished_at = utcnow()
            self.running_run_id = None
            self._abort = False
            await bus.publish("run.finished", {"id": run.id, "status": run.status})
        return run

    async def _execute_steps(
        self,
        session: AsyncSession,
        policy: Policy,
        steps: list[PolicyStep],
        settings: dict[str, Any],
        snap: dict[str, Any],
        episode: Episode | None,
        trigger: str,
        dry_run: bool,
        mark: bool,
    ) -> Run:
        live = bool(settings.get("live_actions"))
        run = Run(
            policy_id=policy.id,
            episode_id=episode.id if episode else None,
            kind=policy.kind,
            trigger=trigger,
            dry_run=dry_run or not live,
            status="running",
        )
        session.add(run)
        await session.flush()
        self.running_run_id = run.id
        self.power_mode = "shedding" if policy.kind == "shed" else "restoring"
        await bus.publish("run.started", {"id": run.id, "policy": policy.name, "dry_run": run.dry_run})
        await self._log(
            session,
            run,
            "info",
            f"Starting {policy.kind} playbook '{policy.name}' ({len(steps)} steps)"
            + (" [DRY RUN]" if run.dry_run else ""),
        )
        try:
            for step in steps:
                if self._abort:
                    run.status = "aborted"
                    await self._log(session, run, "warn", "Aborted by operator")
                    break
                if mark:
                    mark_fired(f"{policy.kind}:{step.id}", self.conditions)
                if step.delay_before_sec:
                    await self._log(session, run, "info", f"Waiting {step.delay_before_sec}s before '{step.name}'")
                    await asyncio.sleep(min(step.delay_before_sec, 3600))

                action = _loads(step.action_json)
                if (action.get("type") or "") == "restore_auto":
                    await self._log(session, run, "info", "Running automatic reverse-restore of shed targets")
                    await self.run_restore_auto(session, dry_run=run.dry_run)
                    continue

                target = None
                host = None
                target_id = action.get("target_id")
                if target_id:
                    target = (
                        await session.execute(
                            select(Target).options(selectinload(Target.host)).where(Target.id == int(target_id))
                        )
                    ).scalar_one_or_none()
                    if target:
                        host = target.host
                host_id = action.get("host_id")
                if host is None and host_id:
                    host = await session.get(HostModel, int(host_id))

                await self._log(
                    session,
                    run,
                    "info",
                    f"Step: {step.name}",
                    {"action": action, "target": target.name if target else None},
                )
                try:
                    result = await asyncio.wait_for(
                        execute_action(
                            session,
                            action,
                            target=target,
                            host=host,
                            dry_run=run.dry_run,
                            live=live,
                            protect_critical=bool(settings.get("protect_critical")),
                            notify_cfg=settings,
                            episode_id=episode.id if episode else None,
                            step_id=step.id,
                        ),
                        timeout=max(5, step.timeout_sec or 120),
                    )
                    await self._log(session, run, "info", f"OK: {step.name}", result if isinstance(result, dict) else {})
                except Exception as exc:  # noqa: BLE001
                    await self._log(session, run, "error", f"FAIL: {step.name}: {exc}")
                    if not step.continue_on_error:
                        run.status = "error"
                        break
            if run.status == "running":
                run.status = "ok"
        finally:
            run.finished_at = utcnow()
            self.running_run_id = None
            self._abort = False
            if self.last_status.get("on_battery"):
                self.power_mode = "on_battery"
            else:
                self.power_mode = "online"
            await bus.publish("run.finished", {"id": run.id, "status": run.status})
        return run

    async def _log(
        self,
        session: AsyncSession,
        run: Run,
        level: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        ev = RunEvent(run_id=run.id, level=level, message=message, payload_json=json.dumps(payload or {}))
        session.add(ev)
        await session.flush()
        await bus.publish("run.event", {"run_id": run.id, "level": level, "message": message, "payload": payload or {}})

    async def _prune_samples(self, session: AsyncSession, hours: int) -> None:
        keep = max(1, hours) * 800
        newest = (
            await session.execute(select(UpsSample.id).order_by(UpsSample.id.desc()).limit(keep))
        ).scalars().all()
        if not newest:
            return
        oldest_keep = min(newest)
        await session.execute(delete(UpsSample).where(UpsSample.id < oldest_keep))


engine = Engine()
