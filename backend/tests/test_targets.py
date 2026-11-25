"""Multi-target action expansion and step execution."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.engine.executor import ActionError, _record_stop
from app.engine.service import Engine
import app.engine.service as service
from app.engine.targets import expand_action_items, item_label
from app.models import Episode, EpisodeAction, Host, Policy, PolicyStep, RunEvent, Target
from app.store import put_setting
from tests.test_engine import world  # noqa: F401  (fixture)

T = lambda **k: json.dumps(k)  # noqa: E731


def test_expand_keeps_legacy_single_target():
    items = expand_action_items({"type": "docker_stop", "target_id": 7, "host_id": 1, "container": "plex"})
    assert len(items) == 1
    assert items[0]["target_id"] == 7
    assert items[0]["container"] == "plex"


def test_expand_target_ids_and_keeps_order():
    items = expand_action_items({"type": "docker_stop", "target_id": 1, "target_ids": [4, 2, 9]})
    assert [i["target_id"] for i in items] == [4, 2, 9]
    assert all(i["type"] == "docker_stop" for i in items)


def test_expand_accepts_only_singular_still():
    items = expand_action_items({"type": "proxmox_shutdown", "target_id": 3})
    assert [i["target_id"] for i in items] == [3]


def test_expand_container_name_list_and_comma_string():
    items = expand_action_items({"type": "docker_stop", "host_id": 5, "containers": ["plex", "sonarr"]})
    assert [i["container"] for i in items] == ["plex", "sonarr"]
    items = expand_action_items({"type": "docker_stop_stack", "host_id": 5, "stacks": "media, arr"})
    assert [i["stack"] for i in items] == ["media", "arr"]


def test_expand_single_name_stays_one_item():
    items = expand_action_items({"type": "docker_stop", "container": "plex", "name": "plex"})
    assert len(items) == 1
    assert items[0]["container"] == "plex"


def test_expand_host_ids():
    items = expand_action_items({"type": "wol", "host_ids": [8, 9], "host_id": 8})
    assert [i["host_id"] for i in items] == [8, 9]


def test_item_label_prefers_target_name():
    assert item_label({"type": "docker_stop", "container": "x"}, target_name="plex") == "plex"
    assert item_label({"type": "docker_stop_stack", "stack": "media"}) == "media"


@pytest.mark.asyncio
async def test_multi_target_dry_run_logs_each(world, monkeypatch):
    eng, _sent, src_id = world
    calls: list[int | None] = []

    async def fake_exec(session, action, **kw):
        calls.append(action.get("target_id"))
        return {"dry_run": True, "would": "docker_stop", "target": str(action.get("target_id"))}

    monkeypatch.setattr(service, "execute_action", fake_exec)
    async with SessionLocal() as s:
        host = Host(name="docker", kind="docker", address="192.0.2.9")
        s.add(host)
        await s.flush()
        a = Target(host_id=host.id, kind="container", name="plex", external_id="plex")
        b = Target(host_id=host.id, kind="container", name="sonarr", external_id="sonarr")
        s.add_all([a, b])
        await s.flush()
        ids = [a.id, b.id]
        pol = Policy(name="dry multi", kind="shed", enabled=False, ups_source_id=src_id, priority=8)
        s.add(pol)
        await s.flush()
        s.add(
            PolicyStep(
                policy_id=pol.id,
                order=10,
                name="stop boxes",
                trigger_json=T(type="on_battery", hold_seconds=0),
                action_json=T(type="docker_stop", target_ids=ids, host_id=host.id),
            )
        )
        await s.commit()
        pol = (
            await s.execute(select(Policy).options(selectinload(Policy.steps)).where(Policy.id == pol.id))
        ).scalar_one()
        run = await eng.run_policy(s, pol, dry_run=True, trigger="manual")
        events = (await s.execute(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id))).scalars().all()
        messages = [e.message for e in events]
    assert calls == ids
    assert any("2 targets" in m for m in messages)
    assert any("Would docker_stop plex" in m for m in messages)
    assert any("Would docker_stop sonarr" in m for m in messages)


@pytest.mark.asyncio
async def test_multi_target_partial_failure_records_success_and_restore_each(world, monkeypatch):
    eng, _sent, src_id = world

    async def fake_exec(session, action, *, target, host, dry_run, live, episode_id=None, step_id=None, **kw):
        name = target.name if target else "?"
        if dry_run or not live:
            return {"dry_run": True, "would": "docker_stop", "target": name}
        if name == "sonarr":
            raise ActionError("sonarr refused to stop")
        _record_stop(session, "docker_stop", target, host, episode_id, step_id, {"stopped": name})
        return {"stopped": name}

    monkeypatch.setattr(service, "execute_action", fake_exec)
    async with SessionLocal() as s:
        host = Host(name="docker", kind="docker", address="192.0.2.9")
        s.add(host)
        await s.flush()
        plex = Target(host_id=host.id, kind="container", name="plex", external_id="plex")
        sonarr = Target(host_id=host.id, kind="container", name="sonarr", external_id="sonarr")
        radarr = Target(host_id=host.id, kind="container", name="radarr", external_id="radarr")
        s.add_all([plex, sonarr, radarr])
        ep = Episode(cause=f"on_battery:{src_id}", status="open")
        s.add(ep)
        pol = Policy(name="multi", kind="shed", enabled=False, ups_source_id=src_id, priority=9)
        s.add(pol)
        await s.flush()
        await s.commit()
        ids = [plex.id, sonarr.id, radarr.id]
        s.add(
            PolicyStep(
                policy_id=pol.id,
                order=10,
                name="stop three",
                continue_on_error=True,
                trigger_json=T(type="on_battery", hold_seconds=0),
                action_json=T(type="docker_stop", target_ids=ids, host_id=host.id),
            )
        )
        await put_setting(s, "live_actions", True)
        await s.commit()
        pol = (
            await s.execute(select(Policy).options(selectinload(Policy.steps)).where(Policy.id == pol.id))
        ).scalar_one()
        run = await eng.run_policy(s, pol, dry_run=False, trigger="manual")
        events = (await s.execute(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id))).scalars().all()
        messages = [e.message for e in events]
        recorded = (
            await s.execute(select(EpisodeAction).where(EpisodeAction.episode_id == ep.id).order_by(EpisodeAction.id))
        ).scalars().all()
        pending_ids = [r.target_id for r in recorded if r.restore_pending]
    assert run.status == "ok"
    assert any("FAIL: stop three: sonarr" in m for m in messages)
    assert any("1 failure(s): sonarr" in m for m in messages)
    assert any("OK: stop three: plex" in m for m in messages)
    assert any("OK: stop three: radarr" in m for m in messages)
    assert pending_ids == [plex.id, radarr.id]

    async def fake_start(session, action, *, target, **kw):
        return {"started": target.name if target else None}

    monkeypatch.setattr(service, "execute_action", fake_start)
    async with SessionLocal() as s:
        restore = await eng.run_restore_auto(s, dry_run=False, source_id=src_id)
        pending = (
            await s.execute(select(EpisodeAction).where(EpisodeAction.restore_pending.is_(True)))
        ).scalars().all()
        events = (await s.execute(select(RunEvent).where(RunEvent.run_id == restore.id).order_by(RunEvent.id))).scalars().all()
        messages = [e.message for e in events]
    assert restore.status == "ok"
    assert pending == []
    # Newest recorded first: radarr then plex.
    assert [m for m in messages if m.startswith("Restore ")] == ["Restore radarr via docker_start", "Restore plex via docker_start"]
