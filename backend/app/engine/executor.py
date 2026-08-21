from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.docker_host import DockerAdapter
from app.adapters.k3s import K3sAdapter
from app.adapters.notify import send_notification
from app.adapters.omv import OmvAdapter
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


def synology_from(host: Host, address: str | None = None) -> SynologyAdapter:
    c = _creds(host)
    extra = _extra(host)
    return SynologyAdapter(
        address=address or host.address,
        port=host.port or 5001,
        username=c.get("username") or "",
        password=c.get("password") or "",
        otp=c.get("otp") or "",
        verify_tls=host.verify_tls,
        use_https=extra.get("https", True),
    )


def collect_macs(action: dict[str, Any], target: Target | None, host: Host | None) -> list[str]:
    found: list[str] = []

    def add(value: Any) -> None:
        if not value:
            return
        if isinstance(value, list):
            for v in value:
                add(v)
            return
        for part in str(value).replace(";", ",").split(","):
            m = part.strip()
            if m and m.lower() not in {x.lower() for x in found}:
                found.append(m)

    add(action.get("mac"))
    add(action.get("macs"))
    if target:
        extra = _extra(target)
        add(extra.get("mac"))
        add(extra.get("mac_passive"))
        add(extra.get("mac_active"))
        add(extra.get("macs"))
    if host:
        extra = _extra(host)
        add(extra.get("mac"))
        add(extra.get("mac_passive"))
        add(extra.get("mac_active"))
        add(extra.get("macs"))
    return found


def is_synology_ha(host: Host | None, target: Target | None) -> bool:
    extras = []
    if host:
        extras.append(_extra(host))
    if target:
        extras.append(_extra(target))
    return any(bool(e.get("ha") or e.get("mac_passive") or e.get("node_a") or e.get("node_b")) for e in extras)


def uses_ssh_docker(host: Host) -> bool:
    extra = _extra(host)
    access = str(extra.get("access") or extra.get("method") or "").lower()
    return access in {"ssh", "docker-ssh"}


def docker_from(host: Host) -> DockerAdapter:
    extra = _extra(host)
    address = extra.get("socket") or extra.get("url") or host.address
    if not address:
        address = "unix:///var/run/docker.sock"
    if host.kind == "docker" and host.address and not host.address.startswith("unix") and "://" not in host.address:
        port = host.port or 2375
        address = f"http://{host.address}:{port}"
    return DockerAdapter(address=address)


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


async def docker_via_ssh(host: Host, command: str, timeout: float = 60) -> dict[str, Any]:
    result = await ssh_from(host).run(command, timeout=timeout)
    if result.get("exit_code") not in (0, None):
        raise ActionError(f"SSH docker failed ({result.get('exit_code')}): {result.get('stderr') or result.get('stdout')}")
    return result


async def discover_docker_ssh(host: Host) -> list[dict[str, Any]]:
    listing = await docker_via_ssh(
        host,
        "docker ps -a --format '{{.Names}}\t{{.State}}\t{{.Label \"com.docker.compose.project\"}}\t{{.Image}}'",
    )
    items: list[dict[str, Any]] = []
    seen_stacks: set[str] = set()
    for line in (listing.get("stdout") or "").splitlines():
        parts = line.split("\t")
        if not parts or not parts[0].strip():
            continue
        name = parts[0].strip()
        state = parts[1].strip() if len(parts) > 1 else "unknown"
        project = parts[2].strip() if len(parts) > 2 else ""
        image = parts[3].strip() if len(parts) > 3 else ""
        items.append(
            {
                "kind": "container",
                "name": name,
                "external_id": name,
                "extra": {"image": image, "state": state, "stack": project, "access": "ssh"},
                "last_status": state,
            }
        )
        if project and project not in seen_stacks:
            seen_stacks.add(project)
            items.append(
                {
                    "kind": "stack",
                    "name": f"stack:{project}",
                    "external_id": project,
                    "extra": {"stack": project, "access": "ssh"},
                    "last_status": "unknown",
                }
            )
    return items


def k3s_from(host: Host) -> K3sAdapter:
    extra = _extra(host)
    return K3sAdapter(ssh_from(host), kubeconfig=str(extra.get("kubeconfig") or "/etc/rancher/k3s/k3s.yaml"))


def omv_from(host: Host) -> OmvAdapter:
    extra = _extra(host)
    c = _creds(host)
    ssh = None
    if c.get("username") or c.get("private_key") or c.get("key"):
        ssh = ssh_from(host)
        if (ssh.port or 22) == (host.port or 443):
            ssh.port = int(extra.get("ssh_port") or 22)
    return OmvAdapter(
        address=host.address,
        port=host.port or 443,
        username=c.get("username") or "",
        password=c.get("password") or "",
        verify_tls=host.verify_tls,
        use_https=extra.get("https", True),
        ssh=ssh,
    )


def ssh_from(host: Host) -> SshAdapter:
    c = _creds(host)
    return SshAdapter(
        address=host.address,
        port=host.port or 22,
        username=c.get("username") or "",
        password=c.get("password") or "",
        key_data=c.get("private_key") or c.get("key") or "",
    )


async def _record_k3s_cluster_restore(
    session: AsyncSession,
    host: Host,
    episode_id: int,
    step_id: int | None,
) -> None:
    """Queue each discovered node so restore_auto can WOL (if MAC) then uncordon.

    Workers are recorded first, control-plane last. restore_auto walks newest-first,
    so the server is woken before the workers.
    """
    from sqlalchemy import select as sql_select

    rows = (
        (await session.execute(sql_select(Target).where(Target.host_id == host.id, Target.kind == "k3s_node")))
        .scalars()
        .all()
    )
    workers = [t for t in rows if not _extra(t).get("control_plane")]
    servers = [t for t in rows if _extra(t).get("control_plane")]
    ordered = workers + servers if (workers or servers) else list(rows)
    if ordered:
        for node in ordered:
            session.add(
                EpisodeAction(
                    episode_id=episode_id,
                    target_id=node.id,
                    host_id=host.id,
                    step_id=step_id,
                    action="k3s_shutdown_node",
                    success=True,
                    restore_pending=True,
                    detail="from cluster shutdown",
                )
            )
        return
    session.add(
        EpisodeAction(
            episode_id=episode_id,
            target_id=None,
            host_id=host.id,
            step_id=step_id,
            action="k3s_shutdown_cluster",
            success=True,
            restore_pending=True,
            detail="cluster shutdown (no node targets discovered)",
        )
    )


STOP_ACTIONS = {
    "proxmox_shutdown",
    "proxmox_stop",
    "docker_stop",
    "docker_stop_stack",
    "synology_shutdown",
    "synology_ha_shutdown",
    "synology_stop_container",
    "k3s_shutdown_node",
    "k3s_shutdown_cluster",
    "k3s_drain",
    "omv_shutdown",
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
        "synology_ha_shutdown",
        "k3s_shutdown_node",
        "k3s_shutdown_cluster",
        "omv_shutdown",
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
        from sqlalchemy import select as sql_select

        from app.models import NotifyChannel
        from app.security import decrypt_secret

        cfg = notify_cfg or {}
        message = action.get("message") or "Blackstart event"
        title = action.get("title") or ""
        channels: list[dict[str, Any]] = []
        rows = (await session.execute(sql_select(NotifyChannel).where(NotifyChannel.enabled.is_(True)))).scalars().all()
        cid = action.get("channel_id")
        if rows:
            if cid in (None, "", "all"):
                chosen = list(rows)
            else:
                chosen = [r for r in rows if r.id == int(cid)]
                if not chosen:
                    raise ActionError(f"Notification channel {cid} not found or disabled")
            for row in chosen:
                channels.append(
                    {
                        "kind": row.kind,
                        "url": row.url,
                        "topic": row.topic,
                        "token": decrypt_secret(row.token_enc),
                        "name": row.name,
                    }
                )
        elif cfg.get("notify_url"):
            channels.append(
                {
                    "kind": cfg.get("notify_kind") or "webhook",
                    "url": cfg.get("notify_url") or "",
                    "topic": cfg.get("ntfy_topic") or "",
                    "token": cfg.get("gotify_token") or "",
                    "name": "default",
                }
            )
        if not channels:
            raise ActionError("No notification channels configured. Add one in Settings.")
        sent = []
        errors = []
        for ch in channels:
            try:
                sent.append(
                    await send_notification(
                        kind=ch["kind"],
                        url=ch["url"],
                        message=message,
                        title=title,
                        topic=ch.get("topic") or "",
                        token=ch.get("token") or "",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{ch.get('name')}: {exc}")
        if errors and not sent:
            raise ActionError("; ".join(errors))
        result = {"sent": sent, "errors": errors}
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
        macs = collect_macs(action, target, host)
        if not macs:
            raise ActionError("WOL requires a MAC address (HA clusters need both node MACs)")
        broadcast = action.get("broadcast") or "255.255.255.255"
        notes = []
        for mac in macs:
            notes.append(await wake_on_lan(str(mac), str(broadcast)))
        result = {"messages": notes, "macs": macs}
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
        if not host:
            raise ActionError("Docker action needs a host")
        verb = atype.split("_", 1)[1]
        ident = (
            action.get("container")
            or action.get("name")
            or (target.external_id if target else "")
            or (target.name if target else "")
        )
        if not ident:
            raise ActionError("Set a container name or pick a container target")
        if uses_ssh_docker(host):
            result = await docker_via_ssh(host, f"docker {verb} {_sh_quote(str(ident))}")
        else:
            if not target and not ident:
                raise ActionError("Docker action needs a host and target")
            result = {"data": await docker_from(host).container_action(str(ident), verb)}
    elif atype in {"docker_stop_stack", "docker_start_stack"}:
        if not host:
            raise ActionError("Stack action needs a host")
        verb = "stop" if "stop" in atype else "start"
        project = (
            action.get("stack")
            or action.get("name")
            or (target.external_id if target else "")
            or ((target and _extra(target).get("stack")) or "")
            or ((target.name if target else "").removeprefix("stack:"))
        )
        if not project:
            raise ActionError("Set a compose project name or pick a stack target")
        if uses_ssh_docker(host):
            # Prefer compose project; fall back to labelled containers.
            cmd = (
                f"(docker compose -p {_sh_quote(str(project))} {verb} "
                f"|| docker ps -aq --filter label=com.docker.compose.project={_sh_quote(str(project))} "
                f"| xargs -r docker {verb})"
            )
            result = await docker_via_ssh(host, cmd)
        else:
            names = await docker_from(host).stack_action(str(project), verb)
            result = {"containers": names}
    elif atype in {"synology_shutdown", "synology_ha_shutdown"}:
        if not host:
            raise ActionError("Need a Synology host")
        extra = _extra(host)
        if target:
            extra = {**extra, **_extra(target)}
        ha = is_synology_ha(host, target) or atype == "synology_ha_shutdown"
        if ha:
            notes: list[Any] = []
            try:
                notes.append(await synology_from(host).shutdown_cluster())
            except Exception as exc:  # noqa: BLE001
                notes.append({"cluster_api": str(exc)})
            # Physical nodes: shut both so the passive cannot take over.
            for key in ("node_a", "node_b", "passive_address"):
                addr = extra.get(key)
                if not addr or str(addr) == host.address:
                    continue
                try:
                    notes.append({"node": addr, "data": await synology_from(host, str(addr)).shutdown()})
                except Exception as exc:  # noqa: BLE001
                    notes.append({"node": addr, "error": str(exc)})
            result = {"ha": True, "steps": notes}
        else:
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
    elif atype in {"k3s_drain", "k3s_uncordon", "k3s_shutdown_node"}:
        if not host:
            raise ActionError("k3s action needs the control-plane host")
        adapter = k3s_from(host)
        node = action.get("node") or (target.external_id if target else "") or (target.name if target else "")
        if not node:
            raise ActionError("Pick a k3s node")
        if atype == "k3s_drain":
            result = await adapter.drain(str(node))
        elif atype == "k3s_uncordon":
            result = await adapter.uncordon(str(node))
        else:
            await adapter.drain(str(node))
            addr = (target and _extra(target).get("address")) or action.get("address") or node
            result = await adapter.shutdown_address(str(addr))
    elif atype == "k3s_shutdown_cluster":
        if not host:
            raise ActionError("k3s cluster shutdown needs the control-plane host")
        adapter = k3s_from(host)
        nodes = await adapter.nodes()
        notes = []
        workers = [n for n in nodes if not n.get("control_plane")]
        # Drain everyone, shut workers first, then remaining (server last).
        ordered = workers + [n for n in nodes if n not in workers]
        for n in ordered:
            try:
                await adapter.drain(n["name"])
            except Exception as exc:  # noqa: BLE001
                notes.append({"drain": n["name"], "error": str(exc)})
            addr = n.get("address") or n["name"]
            try:
                notes.append({"shutdown": addr, "data": await adapter.shutdown_address(str(addr))})
            except Exception as exc:  # noqa: BLE001
                notes.append({"shutdown": addr, "error": str(exc)})
        result = {"nodes": notes}
        if episode_id:
            await _record_k3s_cluster_restore(session, host, episode_id, step_id)
    elif atype == "k3s_wake_node":
        if not host:
            raise ActionError("k3s wake needs the control-plane host")
        notes: list[Any] = []
        macs = collect_macs(action, target, None)
        for mac in macs:
            notes.append(await wake_on_lan(str(mac)))
        node = action.get("node") or (target.external_id if target else "") or (target.name if target else "")
        if not node:
            raise ActionError("Pick a k3s node")
        try:
            notes.append(await k3s_from(host).uncordon(str(node)))
        except Exception as exc:  # noqa: BLE001
            raise ActionError(
                f"Uncordon failed ({exc}). For PoE Pis wait until the node is up, then run restore again."
            ) from exc
        result = {"steps": notes}
    elif atype == "k3s_restore_cluster":
        if not host:
            raise ActionError("k3s cluster restore needs the control-plane host")
        notes = []
        macs = collect_macs(action, target, host)
        from sqlalchemy import select as sql_select

        node_targets = (
            (await session.execute(sql_select(Target).where(Target.host_id == host.id, Target.kind == "k3s_node")))
            .scalars()
            .all()
        )
        for row in node_targets:
            for mac in collect_macs({}, row, None):
                if mac.lower() not in {m.lower() for m in macs}:
                    macs.append(mac)
        for mac in macs:
            notes.append(await wake_on_lan(str(mac)))
        try:
            adapter = k3s_from(host)
            live_nodes = await adapter.nodes()
            for n in live_nodes:
                try:
                    notes.append({"uncordon": n["name"], "data": await adapter.uncordon(n["name"])})
                except Exception as exc:  # noqa: BLE001
                    notes.append({"uncordon": n["name"], "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            notes.append({"kubectl": str(exc)})
            if not macs:
                raise ActionError(
                    f"Control plane is not up yet ({exc}). Wait for the Pis/PoE switch, then run restore again."
                ) from exc
        result = {"steps": notes}
    elif atype == "omv_shutdown":
        if not host:
            raise ActionError("Need an OpenMediaVault host")
        result = {"data": await omv_from(host).shutdown()}
    elif atype == "omv_reboot":
        if not host:
            raise ActionError("Need an OpenMediaVault host")
        result = {"data": await omv_from(host).reboot()}
    elif atype == "webhook":
        url = action.get("url")
        if not url:
            raise ActionError("Webhook missing url — use Settings → Notifications instead")
        result = await send_notification(
            kind="discord" if "discord" in url else "webhook",
            url=url,
            message=action.get("message") or "Blackstart event",
            title=action.get("title") or "",
        )
    else:
        raise ActionError(f"Unknown action type: {atype}")

    if episode_id and atype in STOP_ACTIONS and atype != "k3s_shutdown_cluster" and (target or host):
        session.add(
            EpisodeAction(
                episode_id=episode_id,
                target_id=target.id if target else None,
                host_id=(host.id if host else None) or (target.host_id if target else None),
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
        "synology_shutdown": "wol",
        "synology_ha_shutdown": "wol",
        "k3s_drain": "k3s_uncordon",
        "k3s_shutdown_node": "k3s_wake_node",
        "k3s_shutdown_cluster": "k3s_restore_cluster",
        "omv_shutdown": "wol",
        "stop": "start",
    }.get(action_type)
