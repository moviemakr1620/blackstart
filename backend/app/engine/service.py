from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.nut import NutClient, snapshot_from_vars
from app.adapters.peanut import PeanutClient
from app.db import SessionLocal
from app.engine.bus import bus
from app.engine.evaluator import ConditionState, step_key, trigger_needs_outage, trigger_ready
from app.engine.executor import ALWAYS_REAL_ACTIONS, execute_action, inverse_action, send_to_channels
from app.engine.targets import expand_action_items, item_label
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
from app.redact import redact
from app.store import get_settings_map

log = logging.getLogger("blackstart.engine")

# An episode whose UPS has been back on grid this long is closed even if some
# restore steps never fired (for example a battery that never reached 80%).
EPISODE_IDLE_CLOSE_SEC = 12 * 3600
HEARTBEAT_EVERY_SEC = 60
HOUSEKEEPING_EVERY_SEC = 600


def _loads(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _cause(source_id: int) -> str:
    return f"on_battery:{source_id}"


@dataclass
class Job:
    """Steps that became due together for one playbook on one UPS."""

    policy_id: int
    step_ids: list[int]
    source_id: int
    episode_id: int | None
    snap: dict[str, Any] = field(default_factory=dict)


class Engine:
    """Polls every UPS source, decides which playbook steps are due, and hands
    them to a single worker so slow steps never stop the polling."""

    def __init__(self) -> None:
        self.simulator = Simulator()
        self.conditions = ConditionState()
        self.last_status: dict[str, Any] = {}
        self.snapshots: dict[int, dict[str, Any]] = {}
        self.primary_id: int | None = None
        self.last_error: str = ""
        self.power_mode: str = "unknown"  # online | on_battery | shedding | restoring
        self.running_run_id: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[Job] | None = None
        self._stop: asyncio.Event | None = None
        self._lock: asyncio.Lock | None = None
        self._abort = False
        self._prev_ob: dict[int, bool] = {}
        self._online_since: dict[int, float] = {}
        self._episode_loaded: dict[int, int] = {}
        self._last_ok: dict[int, float] = {}
        self._stale_alerted: set[int] = set()
        self._last_status_raw: dict[int, str] = {}
        self._started_at = time.monotonic()
        self._last_heartbeat = 0.0
        self._last_housekeeping = 0.0

    # ------------------------------------------------------------ lifecycle

    def _ensure_primitives(self) -> None:
        # Created lazily so they bind to the running event loop.
        if self._queue is None:
            self._queue = asyncio.Queue()
        if self._lock is None:
            self._lock = asyncio.Lock()
        if self._stop is None:
            self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        # Fresh primitives per start: tests and reloads may use a new loop.
        self._queue, self._lock, self._stop = None, None, None
        self._ensure_primitives()
        self._started_at = time.monotonic()
        self._task = asyncio.create_task(self._loop(), name="blackstart-engine")
        self._worker = asyncio.create_task(self._work(), name="blackstart-worker")

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        for task in (self._task, self._worker):
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._task = self._worker = None

    def reset_state(self) -> None:
        """Forget timers and firings (History was cleared)."""
        self.conditions.reset_episode()
        self._episode_loaded.clear()

    def abort(self) -> None:
        """Stop the running playbook after its current step and drop queued ones."""
        self._abort = True
        if self._queue is not None:
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except asyncio.QueueEmpty:
                    break

    async def drain(self) -> None:
        """Wait until every queued job has finished (used by tests and shutdown)."""
        self._ensure_primitives()
        assert self._queue is not None
        if self._worker is None or self._worker.done():
            while not self._queue.empty():
                job = self._queue.get_nowait()
                try:
                    await self._run_job(job)
                finally:
                    self._queue.task_done()
            return
        await self._queue.join()

    async def _loop(self) -> None:
        assert self._stop is not None
        while not self._stop.is_set():
            interval = 5
            try:
                async with SessionLocal() as session:
                    interval = await self.tick(session)
                    await session.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — engine must never die
                self.last_error = redact(str(exc))
                log.exception("engine tick failed")
                await bus.publish("engine.error", {"error": self.last_error})
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(2, interval))
            except asyncio.TimeoutError:
                pass

    async def _work(self) -> None:
        assert self._queue is not None
        while True:
            job = await self._queue.get()
            try:
                await self._run_job(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("playbook job failed")
                await bus.publish("engine.error", {"error": redact(str(exc))})
            finally:
                self._queue.task_done()

    # -------------------------------------------------------------- reading

    async def read_one(self, session: AsyncSession, source: UpsSource) -> dict[str, Any]:
        from app.security import decrypt_secret

        if source.kind == "simulator":
            snap = self.simulator.snapshot()
        elif source.kind == "peanut":
            client = PeanutClient(
                base_url=source.base_url or f"http://{source.host}:{source.port or 8080}",
                username=source.username,
                password=decrypt_secret(source.password_enc),
            )
            snap = await client.snapshot(source.device_name)
        elif source.kind == "nut":
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
                if len(listed) > 1:
                    raise RuntimeError("NUT has several UPSes; set the device name on this source")
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
        if (snap.get("status") or "unknown") == "unknown":
            # No ups.status means we cannot tell grid from battery. Do not guess.
            snap["usable"] = False
            snap["error"] = "UPS reported no status (ups.status missing)"
        else:
            snap["usable"] = True
        snap["ts"] = utcnow().isoformat()
        return snap

    # ----------------------------------------------------------------- tick

    async def tick(self, session: AsyncSession) -> int:
        settings = await get_settings_map(session)
        sources = (
            (await session.execute(select(UpsSource).where(UpsSource.enabled.is_(True)).order_by(UpsSource.id)))
            .scalars()
            .all()
        )
        now = time.monotonic()
        await self._maybe_heartbeat(settings, now)
        if not sources:
            self.last_error = "No enabled UPS source."
            self.last_status = {"connected": False, "error": self.last_error, "status": "unknown", "sources": []}
            await bus.publish("ups.sample", self.last_status)
            return 5

        interval = min(max(2, s.poll_interval_sec or 5) for s in sources)
        stale_after = float(settings.get("ups_stale_alert_sec") or 60)
        for src in sources:
            try:
                snap = await self.read_one(session, src)
            except Exception as exc:  # noqa: BLE001
                snap = {
                    "connected": False,
                    "usable": False,
                    "error": redact(str(exc)),
                    "status": "unknown",
                    "on_battery": False,
                    "source_id": src.id,
                    "source_name": src.name,
                    "source_kind": src.kind,
                }
            now = time.monotonic()
            self.snapshots[src.id] = snap
            await self._track_staleness(session, src, snap, now, stale_after)
            if snap.get("connected") and snap.get("usable"):
                status_raw = str(snap.get("status_raw") or snap.get("status") or "")
                changed = self._last_status_raw.get(src.id) != status_raw
                self._last_status_raw[src.id] = status_raw
                session.add(
                    UpsSample(
                        source_id=src.id,
                        status=str(snap.get("status") or ""),
                        charge=snap.get("charge"),
                        runtime_sec=snap.get("runtime_sec"),
                        load_pct=snap.get("load_pct"),
                        watts=snap.get("watts"),
                        input_voltage=snap.get("input_voltage"),
                        # Full raw variables only when the status changes; the
                        # columns above already hold the numbers we chart.
                        raw_json=json.dumps(snap.get("raw") or {}, default=str)[:8000] if changed else "{}",
                    )
                )

        if now - self._last_housekeeping > HOUSEKEEPING_EVERY_SEC:
            self._last_housekeeping = now
            await self._housekeeping(session, settings)

        primary_id = settings.get("primary_ups_id")
        ids = {s.id for s in sources}
        if primary_id not in ids:
            primary_id = sources[0].id
        self.primary_id = int(primary_id)
        primary = dict(self.snapshots.get(self.primary_id) or {})
        self.last_status = {
            **primary,
            "primary_id": self.primary_id,
            "sources": [{k: v for k, v in self.snapshots[s.id].items() if k != "raw"} for s in sources],
        }
        self.last_error = str(primary.get("error") or "")
        await bus.publish("ups.sample", {k: v for k, v in self.last_status.items() if k != "raw"})

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
            if not (snap.get("connected") and snap.get("usable")):
                continue
            bound = [p for p in policies if p.ups_source_id == src.id]
            await self._evaluate_source(session, settings, src.id, snap, bound, time.monotonic())
        return interval

    async def _track_staleness(
        self, session: AsyncSession, src: UpsSource, snap: dict[str, Any], now: float, stale_after: float
    ) -> None:
        ok = bool(snap.get("connected") and snap.get("usable"))
        if ok:
            self._last_ok[src.id] = now
            if src.id in self._stale_alerted:
                self._stale_alerted.discard(src.id)
                await self._alert(session, f"UPS feed '{src.name}' is reporting again ({snap.get('status')}).")
            return
        last = self._last_ok.get(src.id, self._started_at)
        if now - last < stale_after or src.id in self._stale_alerted:
            return
        self._stale_alerted.add(src.id)
        # Old hold timers are meaningless after a gap; start them fresh when data returns.
        self.conditions.reset_timers(src.id)
        was = "ON BATTERY" if self._prev_ob.get(src.id) else "on grid"
        await self._alert(
            session,
            f"Blackstart has had no usable reading from UPS feed '{src.name}' for {int(now - last)}s "
            f"(last known: {was}). Playbooks for it are paused until it reports again. "
            f"Error: {snap.get('error') or 'unknown'}",
        )

    async def _alert(self, session: AsyncSession, message: str) -> None:
        log.warning(message)
        session.add(AuditLog(actor="engine", action="alert", detail=message[:2000]))
        await bus.publish("engine.alert", {"message": message})
        try:
            await send_to_channels(session, message, title="Blackstart alert")
        except Exception as exc:  # noqa: BLE001
            log.warning("alert not delivered: %s", redact(str(exc)))

    async def _maybe_heartbeat(self, settings: dict[str, Any], now: float) -> None:
        url_enc = settings.get("heartbeat_url") or ""
        if not url_enc or now - self._last_heartbeat < HEARTBEAT_EVERY_SEC:
            return
        self._last_heartbeat = now
        from app.security import decrypt_secret

        try:
            url = decrypt_secret(str(url_enc))
        except Exception:  # noqa: BLE001
            return

        async def ping() -> None:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.get(url)
            except Exception as exc:  # noqa: BLE001
                log.info("heartbeat failed: %s", type(exc).__name__)

        asyncio.create_task(ping())

    async def _housekeeping(self, session: AsyncSession, settings: dict[str, Any]) -> None:
        hours = int(settings.get("sample_retention_hours") or 168)
        await session.execute(delete(UpsSample).where(UpsSample.ts < utcnow() - timedelta(hours=max(1, hours))))
        days = int(settings.get("run_retention_days") or 90)
        old_runs = select(Run.id).where(Run.started_at < utcnow() - timedelta(days=max(1, days)), Run.status != "running")
        await session.execute(delete(RunEvent).where(RunEvent.run_id.in_(old_runs)))
        await session.execute(delete(Run).where(Run.id.in_(old_runs)))

    # ------------------------------------------------------------ evaluate

    async def _current_episode(self, session: AsyncSession, source_id: int) -> Episode | None:
        return (
            await session.execute(
                select(Episode)
                .where(Episode.status == "open", Episode.cause == _cause(source_id))
                .order_by(Episode.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    def _load_fired(self, source_id: int, episode: Episode) -> None:
        if self._episode_loaded.get(source_id) == episode.id:
            return
        self._episode_loaded[source_id] = episode.id
        try:
            keys = json.loads(episode.fired_json or "[]")
        except json.JSONDecodeError:
            keys = []
        prefix = f"{source_id}:"
        self.conditions.fired.update(k for k in keys if isinstance(k, str) and k.startswith(prefix))

    def _mark_fired(self, source_id: int, key: str, episode: Episode | None) -> None:
        self.conditions.fired.add(key)
        if episode is not None:
            episode.fired_json = json.dumps(self.conditions.fired_for(source_id))

    async def _evaluate_source(
        self,
        session: AsyncSession,
        settings: dict[str, Any],
        source_id: int,
        snap: dict[str, Any],
        policies: list[Policy],
        now: float,
    ) -> None:
        on_battery = bool(snap.get("on_battery"))
        prev = self._prev_ob.get(source_id)
        episode = await self._current_episode(session, source_id)

        if on_battery and (prev is not True or episode is None):
            if prev is None and episode is not None:
                # Blackstart restarted during an outage: carry on with it.
                self._load_fired(source_id, episode)
                session.add(AuditLog(actor="engine", action="episode.resume", detail=f"id={episode.id}"))
            else:
                if episode is not None:
                    # Power came back and dropped again before restore finished.
                    episode.status = "superseded"
                    episode.ended_at = utcnow()
                    episode.notes = (episode.notes or "") + " | superseded by new outage"
                episode = await self._open_episode(session, snap, source_id)
                self.conditions.reset_source(source_id)
                self._episode_loaded[source_id] = episode.id
            if source_id == self.primary_id:
                self.power_mode = "on_battery"
                await bus.publish("state.changed", {"mode": self.power_mode})
            self._online_since.pop(source_id, None)
        elif not on_battery:
            if prev is not False:
                self._online_since[source_id] = now
            if source_id == self.primary_id and self.power_mode not in {"restoring", "shedding"}:
                if prev is True or self.power_mode == "unknown":
                    self.power_mode = "online"
                    if prev is True:
                        await bus.publish("state.changed", {"mode": self.power_mode})
        self._prev_ob[source_id] = on_battery

        if episode is not None:
            self._load_fired(source_id, episode)
            if snap.get("charge") is not None:
                current = float(snap["charge"])
                if episode.min_charge is None or current < episode.min_charge:
                    episode.min_charge = current

        due: list[tuple[Policy, PolicyStep]] = []
        for policy in policies:
            hold_default = (
                float(settings.get("default_shed_hold_sec") or 0)
                if policy.kind == "shed"
                else float(settings.get("default_restore_hold_sec") or 0)
            )
            for step in policy.steps:
                if not step.enabled:
                    continue
                trigger = _loads(step.trigger_json)
                key = step_key(source_id, policy.kind, step.id)
                outage_only = policy.kind == "restore" or trigger_needs_outage(trigger)
                if outage_only and episode is None:
                    # "Grid is back" means nothing unless there was an outage.
                    self.conditions.true_since.pop(key, None)
                    continue
                if trigger_ready(key, trigger, snap, now, self.conditions, default_hold=hold_default):
                    due.append((policy, step))

        if due:
            by_policy: dict[int, list[PolicyStep]] = {}
            policy_map: dict[int, Policy] = {}
            for policy, step in due:
                policy_map[policy.id] = policy
                by_policy.setdefault(policy.id, []).append(step)
                # Mark now, before it runs, so the next poll cannot queue it again.
                self._mark_fired(source_id, step_key(source_id, policy.kind, step.id), episode)
            await session.commit()
            self._ensure_primitives()
            assert self._queue is not None
            for pid, steps in by_policy.items():
                steps.sort(key=lambda s: s.order)
                await self._queue.put(
                    Job(
                        policy_id=pid,
                        step_ids=[s.id for s in steps],
                        source_id=source_id,
                        episode_id=episode.id if episode else None,
                        snap=dict(snap),
                    )
                )

        if episode is not None and not on_battery and episode.status == "open":
            await self._maybe_close(session, source_id, episode, policies, now)

    async def _maybe_close(
        self, session: AsyncSession, source_id: int, episode: Episode, policies: list[Policy], now: float
    ) -> None:
        pending = (
            await session.execute(
                select(EpisodeAction.id)
                .join(Episode, Episode.id == EpisodeAction.episode_id)
                .where(
                    Episode.cause == _cause(source_id),
                    Episode.status.in_(("open", "superseded")),
                    EpisodeAction.restore_pending.is_(True),
                    EpisodeAction.success.is_(True),
                )
                .limit(1)
            )
        ).first()
        outage_steps = [
            step_key(source_id, p.kind, s.id)
            for p in policies
            for s in p.steps
            if s.enabled and (p.kind == "restore" or trigger_needs_outage(_loads(s.trigger_json)))
        ]
        restore_done = all(k in self.conditions.fired for k in outage_steps)
        busy = self._queue is not None and (self._queue.qsize() > 0 or self.running_run_id is not None)
        online_for = now - self._online_since.get(source_id, now)
        if (restore_done and not pending and not busy) or online_for > EPISODE_IDLE_CLOSE_SEC:
            episode.status = "closed"
            episode.ended_at = utcnow()
            if online_for > EPISODE_IDLE_CLOSE_SEC and (pending or not restore_done):
                episode.notes = (episode.notes or "") + " | closed after 12h back on grid with restore unfinished"
            await bus.publish("episode.closed", {"id": episode.id})

    async def _open_episode(self, session: AsyncSession, snap: dict[str, Any], source_id: int) -> Episode:
        ep = Episode(
            cause=_cause(source_id),
            status="open",
            min_charge=snap.get("charge"),
            notes=str(snap.get("status_raw") or ""),
            fired_json="[]",
        )
        session.add(ep)
        await session.flush()
        session.add(AuditLog(actor="engine", action="episode.open", detail=f"id={ep.id}"))
        await session.commit()
        await bus.publish("episode.opened", {"id": ep.id, "charge": snap.get("charge")})
        return ep

    # ------------------------------------------------------------- running

    async def _run_job(self, job: Job) -> None:
        self._ensure_primitives()
        assert self._lock is not None
        async with self._lock:
            async with SessionLocal() as session:
                policy = (
                    await session.execute(
                        select(Policy).options(selectinload(Policy.steps)).where(Policy.id == job.policy_id)
                    )
                ).scalar_one_or_none()
                if policy is None:
                    return
                wanted = set(job.step_ids)
                steps = sorted((s for s in policy.steps if s.id in wanted and s.enabled), key=lambda s: s.order)
                episode = await session.get(Episode, job.episode_id) if job.episode_id else None
                settings = await get_settings_map(session)
                try:
                    await self._execute_steps(
                        session,
                        policy=policy,
                        steps=steps,
                        settings=settings,
                        snap=job.snap,
                        episode=episode,
                        trigger="auto",
                        dry_run=False,
                        source_id=job.source_id,
                        automatic=True,
                    )
                finally:
                    await session.commit()

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
        source_id = policy.ups_source_id
        query = select(Episode).where(Episode.status == "open")
        if source_id is not None:
            query = query.where(Episode.cause == _cause(source_id))
        episode = (await session.execute(query.order_by(Episode.id.desc()).limit(1))).scalar_one_or_none()
        self._ensure_primitives()
        assert self._lock is not None
        async with self._lock:
            return await self._execute_steps(
                session,
                policy=policy,
                steps=steps,
                settings=settings,
                snap=self.snapshots.get(source_id or -1) or self.last_status or {},
                episode=episode,
                trigger=trigger,
                dry_run=dry_run,
                source_id=source_id,
                automatic=False,
            )

    async def run_restore_manual(self, session: AsyncSession, dry_run: bool = True) -> Run:
        self._ensure_primitives()
        assert self._lock is not None
        async with self._lock:
            return await self.run_restore_auto(session, dry_run=dry_run, source_id=None)

    async def run_restore_auto(
        self,
        session: AsyncSession,
        dry_run: bool = False,
        source_id: int | None = None,
        nested: bool = False,
    ) -> Run:
        """Undo what Blackstart stopped, newest first.

        Covers every open or superseded outage for this UPS (or all UPSes when
        source_id is None), so a flicker mid-restore does not strand anything.
        Only what Blackstart itself stopped is started again.
        """
        settings = await get_settings_map(session)
        live = bool(settings.get("live_actions"))
        query = select(Episode).where(Episode.status.in_(("open", "superseded")))
        if source_id is not None:
            query = query.where(Episode.cause == _cause(source_id))
        episodes = (await session.execute(query.order_by(Episode.id))).scalars().all()
        newest = episodes[-1] if episodes else None
        run = Run(
            policy_id=None,
            episode_id=newest.id if newest else None,
            kind="restore",
            trigger="restore_auto",
            dry_run=dry_run or not live,
            status="running",
        )
        session.add(run)
        await session.flush()
        outer_run = self.running_run_id
        if not nested:
            self.running_run_id = run.id
        try:
            if not episodes:
                await self._log(session, run, "warn", "No open outage — nothing to restore")
                run.status = "ok"
                return run
            actions = (
                (
                    await session.execute(
                        select(EpisodeAction)
                        .where(
                            EpisodeAction.episode_id.in_([e.id for e in episodes]),
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
                await self._log(session, run, "info", "Nothing was stopped during this outage")
            done: dict[tuple[str, int | None, int | None], bool] = {}
            failed = False
            for rec in actions:
                if self._abort:
                    run.status = "aborted"
                    await self._log(session, run, "warn", "Aborted by operator")
                    break
                target = await session.get(Target, rec.target_id) if rec.target_id else None
                inverse = inverse_action(rec.action)
                host = None
                host_id = rec.host_id or (target.host_id if target else None)
                if host_id:
                    host = await session.get(HostModel, host_id)
                if not inverse or (not target and not host):
                    if not run.dry_run:
                        rec.restore_pending = False
                    continue
                ident = (inverse, rec.target_id, host_id)
                if ident in done:
                    # The same thing was recorded twice; starting it once is enough.
                    if not run.dry_run and done[ident]:
                        rec.restore_pending = False
                    continue
                label = target.name if target else host.name if host else inverse
                if run.dry_run:
                    await self._log(session, run, "info", f"Would restore {label} via {inverse} [DRY RUN]")
                    done[ident] = False
                    continue
                await self._log(session, run, "info", f"Restore {label} via {inverse}")
                try:
                    result = await execute_action(
                        session,
                        {"type": inverse},
                        target=target,
                        host=host,
                        dry_run=False,
                        live=live,
                        protect_critical=bool(settings.get("protect_critical")),
                        episode_id=rec.episode_id,
                    )
                    rec.restore_pending = False
                    done[ident] = True
                    await self._log(session, run, "info", f"Restored {label}", result)
                except Exception as exc:  # noqa: BLE001
                    failed = True
                    done[ident] = False
                    await self._log(session, run, "error", f"Failed to restore {label}: {redact(str(exc))}")
                await session.commit()
            if not run.dry_run:
                for ep in episodes:
                    if ep.status != "superseded":
                        continue
                    left = (
                        await session.execute(
                            select(EpisodeAction.id).where(
                                EpisodeAction.episode_id == ep.id, EpisodeAction.restore_pending.is_(True)
                            )
                        )
                    ).first()
                    if left is None:
                        ep.status = "closed"
            if run.status == "running":
                run.status = "error" if failed else "ok"
        except Exception as exc:  # noqa: BLE001
            run.status = "error"
            await self._log(session, run, "error", redact(str(exc)))
        finally:
            run.finished_at = utcnow()
            if not nested:
                self.running_run_id = outer_run
                self._abort = False
            await session.commit()
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
        source_id: int | None,
        automatic: bool,
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
        note = ""
        if run.dry_run and not dry_run:
            note = " [live actions OFF: machines are not touched; notifications and waits still happen]"
        elif run.dry_run:
            note = " [DRY RUN: nothing is sent or changed]"
        await self._log(
            session,
            run,
            "info",
            f"Starting {policy.kind} playbook '{policy.name}' ({len(steps)} steps){note}",
        )
        await session.commit()
        try:
            for step in steps:
                if self._abort:
                    run.status = "aborted"
                    await self._log(session, run, "warn", "Aborted by operator")
                    break
                if step.delay_before_sec:
                    await self._log(session, run, "info", f"Waiting {step.delay_before_sec}s before '{step.name}'")
                    await session.commit()
                    await asyncio.sleep(min(step.delay_before_sec, 3600))

                action = _loads(step.action_json)
                atype = str(action.get("type") or "").lower()
                if atype == "restore_auto":
                    await self._log(session, run, "info", "Starting everything this outage stopped")
                    sub = await self.run_restore_auto(
                        session, dry_run=run.dry_run, source_id=source_id, nested=True
                    )
                    await self._log(
                        session, run, "info" if sub.status == "ok" else "error", f"Restore run #{sub.id}: {sub.status}"
                    )
                    await session.commit()
                    continue

                # Notifications and waits are real in automatic runs even with
                # live actions off; only an explicit manual dry run skips them.
                always_real = atype in ALWAYS_REAL_ACTIONS
                step_dry = dry_run if always_real else run.dry_run
                step_live = True if always_real else live

                items = expand_action_items(action)
                suffix = ""
                if always_real and run.dry_run and not step_dry:
                    suffix = " (sent for real; live actions only gate machine changes)"
                many = len(items) > 1
                if many:
                    await self._log(
                        session,
                        run,
                        "info",
                        f"Step: {step.name} ({len(items)} targets){suffix}",
                        {"action": {k: v for k, v in action.items() if k not in {"url"}}},
                    )
                failed_labels: list[str] = []
                abort_playbook = False
                for item in items:
                    if self._abort:
                        break
                    target = None
                    host = None
                    target_id = item.get("target_id")
                    if target_id:
                        target = (
                            await session.execute(
                                select(Target).options(selectinload(Target.host)).where(Target.id == int(target_id))
                            )
                        ).scalar_one_or_none()
                        if target:
                            host = target.host
                    host_id = item.get("host_id")
                    if host is None and host_id:
                        host = await session.get(HostModel, int(host_id))
                    label = item_label(item, target.name if target else None, host.name if host else None)
                    step_tag = f"{step.name}: {label}" if many else step.name
                    if not many:
                        await self._log(
                            session,
                            run,
                            "info",
                            f"Step: {step.name}{suffix}",
                            {
                                "action": {k: v for k, v in item.items() if k not in {"url"}},
                                "target": target.name if target else None,
                            },
                        )
                    try:
                        result = await asyncio.wait_for(
                            execute_action(
                                session,
                                item,
                                target=target,
                                host=host,
                                dry_run=step_dry,
                                live=step_live,
                                protect_critical=bool(settings.get("protect_critical")),
                                episode_id=episode.id if episode else None,
                                step_id=step.id,
                            ),
                            timeout=max(5, step.timeout_sec or 120),
                        )
                        result = result if isinstance(result, dict) else {}
                        level = "warn" if result.get("errors") or result.get("skipped") else "info"
                        if step_dry or result.get("dry_run"):
                            await self._log(session, run, "info", f"Would {atype} {label} [DRY RUN]", result)
                        else:
                            await self._log(session, run, level, f"OK: {step_tag}", result)
                    except Exception as exc:  # noqa: BLE001
                        msg = "timed out" if isinstance(exc, asyncio.TimeoutError) else redact(str(exc))
                        await self._log(session, run, "error", f"FAIL: {step_tag}: {msg}")
                        failed_labels.append(label)
                    await session.commit()
                if failed_labels:
                    if many:
                        await self._log(
                            session,
                            run,
                            "error",
                            f"Step '{step.name}' finished with {len(failed_labels)} failure(s): {', '.join(failed_labels)}",
                        )
                    if not step.continue_on_error:
                        run.status = "error"
                        abort_playbook = True
                        await session.commit()
                if abort_playbook or self._abort:
                    if self._abort and run.status == "running":
                        run.status = "aborted"
                        await self._log(session, run, "warn", "Aborted by operator")
                    break
            if run.status == "running":
                run.status = "ok"
        finally:
            run.finished_at = utcnow()
            self.running_run_id = None
            self._abort = False
            primary = self.snapshots.get(self.primary_id or -1) or self.last_status
            self.power_mode = "on_battery" if primary.get("on_battery") else "online"
            await session.commit()
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
        ev = RunEvent(run_id=run.id, level=level, message=message, payload_json=json.dumps(payload or {}, default=str))
        session.add(ev)
        await session.flush()
        await bus.publish("run.event", {"run_id": run.id, "level": level, "message": message, "payload": payload or {}})


engine = Engine()
