"""End-to-end engine behaviour against the built-in simulator."""

import json
import time

import pytest
from sqlalchemy import delete, select

import app.engine.executor as executor
import app.engine.service as service
from app import db
from app.db import SessionLocal, init_db
from app.engine.service import Engine
from app.models import (
    Episode,
    EpisodeAction,
    Host,
    NotifyChannel,
    Policy,
    PolicyStep,
    Run,
    RunEvent,
    Setting,
    Target,
    UpsSource,
)
from app.security import encrypt_secret
from app.store import put_setting

T = lambda **k: json.dumps(k)  # noqa: E731
GRID_BACK = {"all": [{"type": "on_online"}, {"type": "battery_gte", "value": 80, "require_online": True}]}


@pytest.fixture
async def world(monkeypatch):
    await db.engine.dispose()
    await init_db()
    sent: list[str] = []

    async def fake_send(**kwargs):
        sent.append(kwargs["message"])
        return {"ok": True}

    monkeypatch.setattr(executor, "send_notification", fake_send)
    async with SessionLocal() as s:
        for model in (RunEvent, Run, EpisodeAction, Episode, PolicyStep, Policy, Target, Host, NotifyChannel, UpsSource, Setting):
            await s.execute(delete(model))
        src = UpsSource(name="sim", kind="simulator", enabled=True, poll_interval_sec=2)
        s.add(src)
        s.add(NotifyChannel(name="hook", kind="webhook", url_enc=encrypt_secret("http://192.0.2.1/hook"), enabled=True))
        await put_setting(s, "live_actions", False)
        await put_setting(s, "default_shed_hold_sec", 0)
        await put_setting(s, "default_restore_hold_sec", 0)
        await s.commit()
        pol = Policy(name="Jason shed", kind="shed", enabled=True, ups_source_id=src.id, priority=1)
        s.add(pol)
        await s.flush()
        s.add_all(
            [
                PolicyStep(policy_id=pol.id, order=10, name="lost", trigger_json=T(type="on_battery", hold_seconds=0),
                           action_json=T(type="notify", message="GRID LOST")),
                PolicyStep(policy_id=pol.id, order=20, name="back", trigger_json=json.dumps({**GRID_BACK, "hold_seconds": 0}),
                           action_json=T(type="notify", message="GRID BACK")),
                PolicyStep(policy_id=pol.id, order=30, name="restore", trigger_json=json.dumps({**GRID_BACK, "hold_seconds": 0}),
                           action_json=T(type="restore_auto")),
            ]
        )
        await s.commit()
        src_id = src.id
    eng = Engine()
    yield eng, sent, src_id
    await db.engine.dispose()


async def tick(eng: Engine, n: int = 1) -> None:
    for _ in range(n):
        async with SessionLocal() as s:
            await eng.tick(s)
            await s.commit()
        await eng.drain()


async def test_grid_lost_notifies_once_with_live_off(world):
    eng, sent, _ = world
    await tick(eng, 3)
    assert sent == [], "nothing should fire while the grid is fine"
    eng.simulator.pull_plug()
    await tick(eng, 6)
    assert sent.count("GRID LOST") == 1
    assert "GRID BACK" not in sent


async def test_no_grid_back_before_any_outage(world):
    eng, sent, _ = world
    eng.simulator.set_charge(100)
    await tick(eng, 5)
    assert "GRID BACK" not in sent


async def test_grid_back_fires_once_after_outage_and_rearms_on_flicker(world):
    eng, sent, _ = world
    eng.simulator.pull_plug()
    await tick(eng, 2)
    eng.simulator.restore_grid()
    eng.simulator.set_charge(50)
    await tick(eng, 2)
    assert "GRID BACK" not in sent, "battery below 80% yet"
    eng.simulator.set_charge(90)
    await tick(eng, 4)
    assert sent.count("GRID LOST") == 1
    assert sent.count("GRID BACK") == 1
    # Flicker: a second outage re-arms both notifications.
    eng.simulator.pull_plug()
    await tick(eng, 2)
    eng.simulator.restore_grid()
    eng.simulator.set_charge(90)
    await tick(eng, 3)
    assert sent.count("GRID LOST") == 2
    assert sent.count("GRID BACK") == 2
    async with SessionLocal() as s:
        statuses = [e.status for e in (await s.execute(select(Episode))).scalars()]
    assert statuses and all(st == "closed" for st in statuses)


async def test_restart_mid_outage_resumes_without_repeating(world):
    eng, sent, _ = world
    eng.simulator.pull_plug()
    await tick(eng, 2)
    assert sent.count("GRID LOST") == 1
    restarted = Engine()
    restarted.simulator = eng.simulator
    await tick(restarted, 3)
    assert sent.count("GRID LOST") == 1
    async with SessionLocal() as s:
        episodes = (await s.execute(select(Episode))).scalars().all()
    assert len(episodes) == 1 and episodes[0].status == "open"


async def test_manual_dry_run_does_not_send(world):
    eng, sent, _ = world
    async with SessionLocal() as s:
        pol = (await s.execute(select(Policy).options(service.selectinload(Policy.steps)))).scalars().first()
        run = await eng.run_policy(s, pol, dry_run=True, trigger="manual")
        await s.commit()
    assert run.dry_run is True
    assert sent == []


async def test_restore_covers_superseded_and_dry_run_keeps_pending(world, monkeypatch):
    eng, sent, src_id = world
    calls: list[str] = []

    async def fake_exec(session, action, **kw):
        calls.append(action["type"])
        return {"ok": True}

    monkeypatch.setattr(service, "execute_action", fake_exec)
    async with SessionLocal() as s:
        host = Host(name="docker", kind="docker", address="192.0.2.9")
        s.add(host)
        await s.flush()
        tgt = Target(host_id=host.id, kind="container", name="plex", external_id="plex")
        s.add(tgt)
        old = Episode(cause=f"on_battery:{src_id}", status="superseded")
        cur = Episode(cause=f"on_battery:{src_id}", status="open")
        s.add_all([old, cur])
        await s.flush()
        s.add(EpisodeAction(episode_id=old.id, target_id=tgt.id, host_id=host.id, action="docker_stop",
                            success=True, restore_pending=True))
        await s.commit()
        dry = await eng.run_restore_auto(s, dry_run=True, source_id=src_id)
        assert dry.dry_run and calls == []
        pending = (await s.execute(select(EpisodeAction).where(EpisodeAction.restore_pending.is_(True)))).scalars().all()
        assert len(pending) == 1, "a dry run must not mark anything restored"
        await put_setting(s, "live_actions", True)
        await s.commit()
        live = await eng.run_restore_auto(s, dry_run=False, source_id=src_id)
        assert live.status == "ok"
        assert calls == ["docker_start"]
        pending = (await s.execute(select(EpisodeAction).where(EpisodeAction.restore_pending.is_(True)))).scalars().all()
        assert pending == []
        await s.refresh(old)
        assert old.status == "closed"


async def test_stale_ups_feed_alerts_once(world):
    eng, sent, src_id = world
    async with SessionLocal() as s:
        src = await s.get(UpsSource, src_id)
        src.kind = "nut"
        src.host = "127.0.0.1"
        src.port = 1  # nothing listens here
        await put_setting(s, "ups_stale_alert_sec", 10)
        await s.commit()
    eng._started_at = time.monotonic() - 100
    await tick(eng, 3)
    alerts = [m for m in sent if "no usable reading" in m]
    assert len(alerts) == 1
