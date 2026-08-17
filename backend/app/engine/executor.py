from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.docker_host import DockerAdapter
from app.adapters.notify import send_notification
from app.adapters.proxmox import ProxmoxAdapter
from app.adapters.ssh import SshAdapter
from app.adapters.synology import SynologyAdapter
from app.adapters.wol import wake_on_lan
from app.models import EpisodeAction, Host, Target
from app.security import decrypt_json


class ActionError(RuntimeError):
    pass


def _creds(host: Host) -> dict[str, Any]:
    return decrypt_json(host.credentials_enc)


def _extra(obj: Host | Target) -> dict[str, Any]:
    try:
        data = json.loads(obj.extra_json or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def proxmox_from(host: Host) -> ProxmoxAdapter:
    c = _creds(host)
    extra = _extra(host)
    return ProxmoxAdapter(
        address=host.address,
        port=host.port or 8006,
        token_id=c.get("token_id") or c.get("token") or "",
        token_secret=c.get("token_secret") or c.get("secret") or "",
        verify_tls=host.verify_tls,
    )


def synology_from(host: Host) -> SynologyAdapter:
    c = _creds(host)
    extra = _extra(host)
    return SynologyAdapter(
        address=host.address,
        port=host.port or 5001,
        username=c.get("username") or "",
        password=c.get("password") or "",
        otp=c.get("otp") or "",
        verify_tls=host.verify_tls,
        use_https=extra.get("https", True),
    )


def docker_from(host: Host) -> DockerAdapter:
    extra = _extra(host)
    address = extra.get("socket") or extra.get("url") or host.address
    if not address:
        address = "unix:///var/run/docker.sock"
    if host.kind == "docker" and host.address and not host.address.startswith("unix") and "://" not in host.address:
        port = host.port or 2375
        address = f"http://{host.address}:{port}"
    return DockerAdapter(address=address)


def ssh_from(host: Host) -> SshAdapter:
    c = _creds(host)
    return SshAdapter(
        address=host.address,
        port=host.port or 22,
        username=c.get("username") or "",
        password=c.get("password") or "",
        key_data=c.get("private_key") or c.get("key") or "",
    )


STOP_ACTIONS = {
    "proxmox_shutdown",
    "proxmox_stop",
    "docker_stop",
    "docker_stop_stack",
    "synology_shutdown",
    "ssh",
    "shutdown",
    "stop",
}


async def execute_action(
    session: AsyncSession,
    action: dict[str, Any],
    *,
    target: Target | None,
    host: Host | None,
    dry_run: bool,
    live: bool,
    protect_critical: bool,
    notify_cfg: dict[str, Any] | None = None,
    episode_id: int | None = None,
    step_id: int | None = None,
) -> dict[str, Any]:
    atype = (action.get("type") or "").lower()
    if not atype:
        raise ActionError("Action is missing a type")

    if target and target.critical and protect_critical and atype in {
        "proxmox_shutdown",
        "proxmox_stop",
        "docker_stop",
        "docker_stop_stack",
        "synology_shutdown",
        "shutdown",
        "stop",
        "host_shutdown",
    }:
        return {"skipped": True, "reason": f"protected critical target '{target.name}'"}

    if dry_run or not live:
        return {
            "dry_run": True,
            "would": atype,
            "target": target.name if target else None,
            "live_disabled": not live and not dry_run,
        }

    result: dict[str, Any]
    if atype == "notify":
        cfg = notify_cfg or {}
        result = await send_notification(
            kind=action.get("kind") or cfg.get("notify_kind") or "webhook",
            url=action.get("url") or cfg.get("notify_url") or "",
            message=action.get("message") or "Blackstart event",
            title=action.get("title") or "Blackstart",
            topic=action.get("topic") or cfg.get("ntfy_topic") or "",
            token=action.get("token") or cfg.get("gotify_token") or "",
        )
    elif atype == "wait":
        seconds = float(action.get("seconds") or 5)
        await asyncio.sleep(min(seconds, 3600))
        result = {"waited": seconds}
    elif atype in {"wait_tcp", "wait_host"}:
        address = action.get("address") or (host.address if host else "")
        port = int(action.get("port") or (host.port if host else 0) or 0)
        if not address or not port:
            raise ActionError("wait_tcp needs address and port")
        timeout = float(action.get("timeout") or 180)
        interval = float(action.get("interval") or 3)
        deadline = asyncio.get_event_loop().time() + timeout
        last_err = "timeout"
        reachable = False
        while asyncio.get_event_loop().time() < deadline:
            try:
                _reader, writer = await asyncio.wait_for(asyncio.open_connection(address, port), timeout=3)
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                reachable = True
                break
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
                await asyncio.sleep(interval)
        if not reachable:
            raise ActionError(f"{address}:{port} not reachable: {last_err}")
        result = {"reachable": f"{address}:{port}"}
    elif atype == "wol":
        mac = action.get("mac") or (target and _extra(target).get("mac")) or (host and _extra(host).get("mac"))
        if not mac:
            raise ActionError("WOL requires a MAC address")
        broadcast = action.get("broadcast") or "255.255.255.255"
        result = {"message": await wake_on_lan(str(mac), str(broadcast))}
    elif atype in {"proxmox_shutdown", "proxmox_stop", "proxmox_start", "proxmox_reboot"}:
        if not host or not target:
            raise ActionError("Proxmox action needs a host and target")
        node = _extra(target).get("node") or action.get("node")
        if not node:
            raise ActionError("Proxmox target is missing node")
        verb = {
            "proxmox_shutdown": "shutdown",
            "proxmox_stop": "stop",
            "proxmox_start": "start",
            "proxmox_reboot": "reboot",
        }[atype]
        adapter = proxmox_from(host)
        result = {"data": await adapter.power(str(node), target.kind, target.external_id, verb)}
    elif atype == "proxmox_shutdown_node":
        if not host:
            raise ActionError("Need a Proxmox host")
        node = action.get("node") or _extra(host).get("node")
        if not node:
            raise ActionError("Missing Proxmox node name")
        result = {"data": await proxmox_from(host).shutdown_node(str(node))}
    elif atype in {"docker_stop", "docker_start", "docker_restart"}:
        if not host or not target:
            raise ActionError("Docker action needs a host and target")
        verb = atype.split("_", 1)[1]
        ident = target.external_id or target.name
        result = {"data": await docker_from(host).container_action(ident, verb)}
    elif atype in {"docker_stop_stack", "docker_start_stack"}:
        if not host or not target:
            raise ActionError("Stack action needs a host and target")
        verb = "stop" if "stop" in atype else "start"
        project = target.external_id or _extra(target).get("stack") or target.name.removeprefix("stack:")
        names = await docker_from(host).stack_action(str(project), verb)
        result = {"containers": names}
    elif atype == "synology_shutdown":
        if not host:
            raise ActionError("Need a Synology host")
        result = {"data": await synology_from(host).shutdown()}
    elif atype in {"synology_start_container", "synology_stop_container"}:
        if not host or not target:
            raise ActionError("Need Synology host and container")
        method = "start" if "start" in atype else "stop"
        result = {"data": await synology_from(host).container(target.external_id or target.name, method)}
    elif atype == "ssh":
        if not host:
            raise ActionError("SSH action needs a host")
        command = action.get("command") or (target and _extra(target).get("command"))
        if not command:
            raise ActionError("SSH action is missing a command")
        result = await ssh_from(host).run(str(command), timeout=float(action.get("timeout") or 30))
        if result.get("exit_code") not in (0, None) and not action.get("ignore_exit"):
            raise ActionError(f"SSH exit {result.get('exit_code')}: {result.get('stderr')}")
    elif atype == "webhook":
        import httpx

        url = action.get("url")
        if not url:
            raise ActionError("Webhook missing url")
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.request(
                action.get("method") or "POST",
                url,
                json=action.get("body") or {"source": "blackstart", "message": action.get("message")},
                headers=action.get("headers") or {},
            )
            resp.raise_for_status()
            result = {"status": resp.status_code}
    else:
        raise ActionError(f"Unknown action type: {atype}")

    if episode_id and target and atype in STOP_ACTIONS:
        session.add(
            EpisodeAction(
                episode_id=episode_id,
                target_id=target.id,
                step_id=step_id,
                action=atype,
                success=True,
                restore_pending=True,
                detail=json.dumps(result)[:2000],
            )
        )
    return result


def inverse_action(action_type: str) -> str | None:
    return {
        "proxmox_shutdown": "proxmox_start",
        "proxmox_stop": "proxmox_start",
        "docker_stop": "docker_start",
        "docker_stop_stack": "docker_start_stack",
        "synology_stop_container": "synology_start_container",
        "stop": "start",
    }.get(action_type)
