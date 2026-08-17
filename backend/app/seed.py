from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Host, Policy, PolicyStep, Target, UpsSource, User
from app.security import encrypt_secret, hash_password
from app.store import put_setting


def _trigger(**kwargs) -> str:
    return json.dumps(kwargs)


def _action(**kwargs) -> str:
    return json.dumps(kwargs)


async def seed_if_empty(session: AsyncSession) -> None:
    users = (await session.execute(select(func.count()).select_from(User))).scalar() or 0
    if users == 0:
        session.add(
            User(
                username=settings.admin_user,
                password_hash=hash_password(settings.admin_password),
            )
        )

    sources = (await session.execute(select(func.count()).select_from(UpsSource))).scalar() or 0
    if sources == 0:
        session.add(
            UpsSource(
                name="EcoFlow simulator",
                kind="simulator",
                enabled=True,
                device_name="ecoflow-sim",
                poll_interval_sec=3,
            )
        )
        if settings.peanut_url:
            session.add(
                UpsSource(
                    name="PeaNUT (EcoFlow Delta 3 Plus)",
                    kind="peanut",
                    enabled=False,
                    base_url=settings.peanut_url,
                    username=settings.peanut_user,
                    password_enc=encrypt_secret(settings.peanut_password) if settings.peanut_password else "",
                    device_name=settings.nut_device or "ecoflow",
                    poll_interval_sec=5,
                )
            )
        if settings.nut_host:
            session.add(
                UpsSource(
                    name="NUT (direct)",
                    kind="nut",
                    enabled=False,
                    host=settings.nut_host,
                    port=settings.nut_port,
                    device_name=settings.nut_device or "ecoflow",
                    username=settings.nut_user,
                    password_enc=encrypt_secret(settings.nut_password) if settings.nut_password else "",
                    poll_interval_sec=5,
                )
            )

    hosts = (await session.execute(select(func.count()).select_from(Host))).scalar() or 0
    if hosts == 0:
        pve = Host(
            name="Proxmox",
            kind="proxmox",
            enabled=False,
            address="192.168.1.10",
            port=8006,
            extra_json=json.dumps({"node": "pve"}),
        )
        nas = Host(
            name="Synology NAS",
            kind="synology",
            enabled=False,
            address="192.168.1.20",
            port=5001,
            extra_json=json.dumps({"https": True, "mac": "00:11:32:00:00:00"}),
        )
        dock = Host(
            name="Docker host",
            kind="docker",
            enabled=False,
            address="unix:///var/run/docker.sock",
            port=0,
            extra_json=json.dumps({"socket": "unix:///var/run/docker.sock"}),
        )
        session.add_all([pve, nas, dock])
        await session.flush()

        session.add_all(
            [
                Target(
                    host_id=pve.id,
                    kind="lxc",
                    name="peanut (keep up)",
                    external_id="100",
                    extra_json=json.dumps({"node": "pve"}),
                    critical=True,
                    notes="PeaNUT + NUT — the UPS feed. Never shed this.",
                ),
                Target(
                    host_id=pve.id,
                    kind="lxc",
                    name="blackstart (keep up)",
                    external_id="101",
                    extra_json=json.dumps({"node": "pve"}),
                    critical=True,
                    notes="This orchestrator. Never shed this.",
                ),
                Target(
                    host_id=pve.id,
                    kind="vm",
                    name="example-media-vm",
                    external_id="200",
                    extra_json=json.dumps({"node": "pve"}),
                    notes="Replace with a real VM after Discover.",
                ),
                Target(
                    host_id=dock.id,
                    kind="stack",
                    name="stack:media",
                    external_id="media",
                    extra_json=json.dumps({"stack": "media"}),
                    notes="Compose project to stop first.",
                ),
                Target(
                    host_id=nas.id,
                    kind="nas",
                    name="Synology DSM",
                    external_id="dsm",
                    extra_json=json.dumps({"mac": "00:11:32:00:00:00"}),
                    notes="Full NAS shutdown + WOL restore.",
                ),
            ]
        )
        await session.flush()

    policies = (await session.execute(select(func.count()).select_from(Policy))).scalar() or 0
    if policies == 0:
        targets = (await session.execute(select(Target))).scalars().all()
        by_name = {t.name: t for t in targets}
        media_stack = by_name.get("stack:media")
        media_vm = by_name.get("example-media-vm")
        nas = by_name.get("Synology DSM")

        shed = Policy(
            name="Default load shed",
            kind="shed",
            description="Progressive shutdown while the EcoFlow is on battery. Edit thresholds to match your lab.",
            enabled=True,
            priority=10,
        )
        restore = Policy(
            name="Default restore",
            kind="restore",
            description="Bring the lab back once the grid is up and the battery has recovered.",
            enabled=True,
            priority=10,
        )
        session.add_all([shed, restore])
        await session.flush()

        session.add_all(
            [
                PolicyStep(
                    policy_id=shed.id,
                    order=10,
                    name="Alert: grid lost",
                    trigger_json=_trigger(type="on_battery", hold_seconds=8),
                    action_json=_action(
                        type="notify",
                        message="Grid power lost. EcoFlow Delta 3 Plus is on battery. Load-shed playbook is armed.",
                    ),
                    continue_on_error=True,
                ),
                PolicyStep(
                    policy_id=shed.id,
                    order=20,
                    name="Stop media compose stack",
                    trigger_json=_trigger(type="battery_lte", value=70, require_on_battery=True, hold_seconds=20),
                    action_json=_action(
                        type="docker_stop_stack",
                        target_id=media_stack.id if media_stack else None,
                    ),
                    delay_before_sec=0,
                    timeout_sec=90,
                ),
                PolicyStep(
                    policy_id=shed.id,
                    order=30,
                    name="Shutdown media VM",
                    trigger_json=_trigger(type="battery_lte", value=45, require_on_battery=True, hold_seconds=20),
                    action_json=_action(
                        type="proxmox_shutdown",
                        target_id=media_vm.id if media_vm else None,
                    ),
                    timeout_sec=180,
                ),
                PolicyStep(
                    policy_id=shed.id,
                    order=40,
                    name="Shutdown Synology",
                    trigger_json=_trigger(type="battery_lte", value=25, require_on_battery=True, hold_seconds=30),
                    action_json=_action(
                        type="synology_shutdown",
                        target_id=nas.id if nas else None,
                        host_id=nas.host_id if nas else None,
                    ),
                    timeout_sec=120,
                ),
                PolicyStep(
                    policy_id=shed.id,
                    order=50,
                    name="Alert: critically low",
                    trigger_json=_trigger(type="battery_lte", value=15, require_on_battery=True, hold_seconds=5),
                    action_json=_action(
                        type="notify",
                        message="EcoFlow is critically low. Only peanut/blackstart should still be running.",
                    ),
                ),
                PolicyStep(
                    policy_id=restore.id,
                    order=10,
                    name="Alert: grid restored",
                    trigger_json=_trigger(
                        type="on_online",
                        hold_seconds=30,
                    ),
                    action_json=_action(
                        type="notify",
                        message="Grid power is back. Waiting for the battery to recover before starting workloads.",
                    ),
                ),
                PolicyStep(
                    policy_id=restore.id,
                    order=20,
                    name="Wake Synology",
                    trigger_json=_trigger(
                        all=[
                            {"type": "on_online"},
                            {"type": "battery_gte", "value": 40, "require_online": True},
                        ],
                        hold_seconds=60,
                    ),
                    action_json=_action(
                        type="wol",
                        target_id=nas.id if nas else None,
                    ),
                    timeout_sec=15,
                ),
                PolicyStep(
                    policy_id=restore.id,
                    order=25,
                    name="Wait for DSM to come up",
                    trigger_json=_trigger(
                        all=[
                            {"type": "on_online"},
                            {"type": "battery_gte", "value": 40, "require_online": True},
                        ],
                        hold_seconds=90,
                    ),
                    action_json=_action(
                        type="wait_tcp",
                        target_id=nas.id if nas else None,
                        host_id=nas.host_id if nas else None,
                        port=5001,
                        timeout=180,
                    ),
                    timeout_sec=200,
                    continue_on_error=True,
                ),
                PolicyStep(
                    policy_id=restore.id,
                    order=30,
                    name="Start everything we stopped",
                    trigger_json=_trigger(
                        all=[
                            {"type": "on_online"},
                            {"type": "battery_gte", "value": 55, "require_online": True},
                        ],
                        hold_seconds=90,
                    ),
                    action_json=_action(type="restore_auto"),
                    timeout_sec=600,
                ),
            ]
        )

    from app.models import Setting

    if await session.get(Setting, "live_actions") is None:
        await put_setting(session, "live_actions", bool(settings.live_actions))
    await session.commit()
