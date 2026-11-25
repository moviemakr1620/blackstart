from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.docker_host import DockerAdapter, PartialStackError
from app.adapters.k3s import K3sAdapter
from app.adapters.notify import looks_like_discord, send_notification
from app.adapters.omv import OmvAdapter
from app.adapters.pbs import PbsAdapter
from app.adapters.proxmox import ProxmoxAdapter, _node_id
from app.adapters.ssh import SshAdapter
from app.adapters.synology import SynologyAdapter
from app.adapters.wol import wake_on_lan
from app.models import EpisodeAction, Host, Target
from app.netguard import check_host
from app.redact import redact
from app.security import SecretDecryptError, decrypt_json, decrypt_secret


class ActionError(RuntimeError):
    pass


def _creds(host: Host) -> dict[str, Any]:
    try:
        return decrypt_json(host.credentials_enc)
    except SecretDecryptError as exc:
        raise ActionError(f"Credentials for '{host.name}' can't be read: {exc}") from exc


def tls_verify(host: Host) -> Any:
    """False, True, or an SSL context trusting the host's own CA / self-signed cert.

    Paste the PEM of a self-signed certificate (or your lab CA) into the host's
    "ca_pem" extra field to verify it instead of turning verification off.
    """
    if not host.verify_tls:
        return False
    pem = str(_extra(host).get("ca_pem") or "").strip()
    if pem:
        import ssl

        ctx = ssl.create_default_context(cadata=pem)
        # Proxmox / DSM self-signed certs are often issued to a name, not the IP.
        if _extra(host).get("ca_skip_hostname"):
            ctx.check_hostname = False
        return ctx
    return True


def _extra(obj: Host | Target) -> dict[str, Any]:
    try:
        data = json.loads(obj.extra_json or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def proxmox_from(host: Host) -> ProxmoxAdapter:
    c = _creds(host)
    return ProxmoxAdapter(
        address=host.address,
        port=host.port or 8006,
        token_id=c.get("token_id") or c.get("token") or "",
        token_secret=c.get("token_secret") or c.get("secret") or "",
        verify_tls=tls_verify(host),
        timeout=30.0,
    )


def pbs_from(host: Host) -> PbsAdapter:
    c = _creds(host)
    return PbsAdapter(
        address=host.address,
        port=host.port or 8007,
        token_id=c.get("token_id") or c.get("token") or "",
        token_secret=c.get("token_secret") or c.get("secret") or "",
        verify_tls=tls_verify(host),
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
        verify_tls=tls_verify(host),
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
        verify_tls=tls_verify(host),
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
        key_passphrase=c.get("key_passphrase") or "",
        host_key=str(_extra(host).get("ssh_host_key") or ""),
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


PROTECTED_ACTIONS = {
    "proxmox_shutdown",
    "proxmox_stop",
    "proxmox_reboot",
    "proxmox_shutdown_node",
    "pbs_shutdown",
    "docker_stop",
    "docker_stop_stack",
    "docker_restart",
    "synology_shutdown",
    "synology_ha_shutdown",
    "synology_stop_container",
    "k3s_shutdown_node",
    "k3s_shutdown_cluster",
    "k3s_drain",
    "omv_shutdown",
    "omv_reboot",
    "ssh",
    "shutdown",
    "stop",
    "host_shutdown",
}

# Actions that run for real in automatic runs even while live actions are off.
# They only tell you things or wait; they never change a machine.
ALWAYS_REAL_ACTIONS = {"notify", "webhook", "wait"}

STOP_ACTIONS = {
    "proxmox_shutdown",
    "proxmox_stop",
    "proxmox_shutdown_node",
    "pbs_shutdown",
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

    if target and target.critical and protect_critical and atype in PROTECTED_ACTIONS:
        return {"skipped": True, "reason": f"protected critical target '{target.name}'"}
    if host and protect_critical and atype in PROTECTED_ACTIONS and _extra(host).get("critical"):
        return {"skipped": True, "reason": f"protected critical host '{host.name}'"}

    if dry_run or not live:
        return {
            "dry_run": True,
            "would": atype,
            "target": target.name if target else None,
            "live_disabled": not live and not dry_run,
        }

    result: dict[str, Any]
    if atype == "notify":
        result = await send_to_channels(
            session,
            action.get("message") or "Blackstart event",
            title=action.get("title") or "",
            channel_id=action.get("channel_id"),
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
        try:
            await asyncio.to_thread(check_host, str(address))
        except ValueError as exc:
            raise ActionError(str(exc)) from exc
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
        broadcast = _broadcast(action.get("broadcast"))
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
        params: dict[str, Any] | None = None
        if verb == "shutdown":
            # Ask nicely, then force it off if the guest ignores ACPI.
            params = {"timeout": int(action.get("shutdown_timeout") or 90), "forceStop": 1}
        upid = await adapter.power(str(node), target.kind, target.external_id, verb, params=params)
        wait_for = int(action.get("shutdown_timeout") or 90) + 30 if verb == "shutdown" else 90
        status = await adapter.wait_task(str(node), upid, timeout=wait_for) if isinstance(upid, str) else {}
        result = {"task": upid, "status": status}
    elif atype == "proxmox_shutdown_node":
        if not host:
            raise ActionError("Need a Proxmox host")
        adapter = proxmox_from(host)
        node = action.get("node") or _extra(host).get("node")
        if not node:
            listed = await adapter.nodes()
            if len(listed) == 1:
                node = _node_id(listed[0])
        if not node:
            raise ActionError("Missing Proxmox node name (cluster has more than one node)")
        result = {"data": await adapter.shutdown_node(str(node))}
    elif atype == "pbs_shutdown":
        if not host:
            raise ActionError("Need a Proxmox Backup Server host")
        node = action.get("node") or _extra(host).get("node") or (target and _extra(target).get("node"))
        result = {"data": await pbs_from(host).shutdown(str(node) if node else None)}
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
            try:
                names = await docker_from(host).stack_action(str(project), verb)
            except PartialStackError as exc:
                if verb == "stop" and exc.done and episode_id:
                    # Some containers did stop: remember them so restore starts the stack.
                    _record_stop(session, atype, target, host, episode_id, step_id, {"partial": exc.done})
                raise ActionError(redact(str(exc))) from exc
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
            result = _ok_exit(await adapter.drain(str(node)), f"drain {node}")
        elif atype == "k3s_uncordon":
            result = _ok_exit(await adapter.uncordon(str(node)), f"uncordon {node}")
        else:
            _ok_exit(await adapter.drain(str(node)), f"drain {node}")
            addr = (target and _extra(target).get("address")) or action.get("address") or node
            result = _ok_exit(await adapter.shutdown_address(str(addr)), f"shutdown {addr}", shutdown=True)
    elif atype == "k3s_shutdown_cluster":
        if not host:
            raise ActionError("k3s cluster shutdown needs the control-plane host")
        adapter = k3s_from(host)
        nodes = await adapter.nodes()
        notes = []
        failures: list[str] = []
        workers = [n for n in nodes if not n.get("control_plane")]
        # Drain everyone, shut workers first, then remaining (server last).
        ordered = workers + [n for n in nodes if n not in workers]
        for n in ordered:
            try:
                _ok_exit(await adapter.drain(n["name"]), f"drain {n['name']}")
            except Exception as exc:  # noqa: BLE001
                notes.append({"drain": n["name"], "error": redact(str(exc))})
            addr = n.get("address") or n["name"]
            try:
                notes.append(
                    {"shutdown": addr, "data": _ok_exit(await adapter.shutdown_address(str(addr)), "shutdown", True)}
                )
            except Exception as exc:  # noqa: BLE001
                notes.append({"shutdown": addr, "error": redact(str(exc))})
                failures.append(addr)
        result = {"nodes": notes}
        if episode_id:
            await _record_k3s_cluster_restore(session, host, episode_id, step_id)
        if failures:
            raise ActionError(f"k3s nodes did not shut down: {', '.join(failures)}")
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
            notes.append(_ok_exit(await k3s_from(host).uncordon(str(node)), f"uncordon {node}"))
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
            kind="discord" if looks_like_discord(str(url)) else "webhook",
            url=url,
            message=action.get("message") or "Blackstart event",
            title=action.get("title") or "",
        )
    else:
        raise ActionError(f"Unknown action type: {atype}")

    if episode_id and atype in STOP_ACTIONS and atype != "k3s_shutdown_cluster" and (target or host):
        _record_stop(session, atype, target, host, episode_id, step_id, result)
    return result


def _record_stop(
    session: AsyncSession,
    atype: str,
    target: Target | None,
    host: Host | None,
    episode_id: int,
    step_id: int | None,
    result: Any,
) -> None:
    session.add(
        EpisodeAction(
            episode_id=episode_id,
            target_id=target.id if target else None,
            host_id=(host.id if host else None) or (target.host_id if target else None),
            step_id=step_id,
            action=atype,
            success=True,
            restore_pending=True,
            detail=json.dumps(result, default=str)[:2000],
        )
    )


def _ok_exit(result: dict[str, Any], what: str, shutdown: bool = False) -> dict[str, Any]:
    code = result.get("exit_code")
    # A shutdown often drops the SSH session before an exit code arrives (-1).
    if code in (0, None) or (shutdown and code == -1):
        return result
    raise ActionError(redact(f"{what} failed (exit {code}): {result.get('stderr') or result.get('stdout') or ''}"))


def _broadcast(value: Any) -> str:
    import ipaddress

    text = str(value or "255.255.255.255").strip()
    try:
        ip = ipaddress.IPv4Address(text)
    except ValueError as exc:
        raise ActionError(f"Wake-on-LAN broadcast must be an IPv4 address, not '{text}'") from exc
    if ip.is_link_local or ip.is_loopback:
        raise ActionError(f"Wake-on-LAN broadcast {text} is not allowed")
    return text


async def send_to_channels(
    session: AsyncSession,
    message: str,
    title: str = "",
    channel_id: Any = None,
) -> dict[str, Any]:
    """Send to the chosen channel, or every enabled one. Raises if nothing got through.

    Any channel that fails is reported in the result (and raised if all failed),
    so a broken channel never looks like a successful step.
    """
    from sqlalchemy import select as sql_select

    from app.models import NotifyChannel
    from app.routers.notify_channels import channel_url

    rows = (await session.execute(sql_select(NotifyChannel).where(NotifyChannel.enabled.is_(True)))).scalars().all()
    if channel_id not in (None, "", "all"):
        try:
            wanted = int(channel_id)
        except (TypeError, ValueError) as exc:
            raise ActionError(f"Bad notification channel id {channel_id!r}") from exc
        rows = [r for r in rows if r.id == wanted]
        if not rows:
            raise ActionError(f"Notification channel {channel_id} not found or turned off")
    if not rows:
        raise ActionError("No notification channels are turned on. Add one in Settings → Notifications.")
    sent: list[dict[str, Any]] = []
    errors: list[str] = []
    for row in rows:
        try:
            sent.append(
                {
                    "channel": row.name,
                    **await send_notification(
                        kind=row.kind,
                        url=channel_url(row),
                        message=message,
                        title=title,
                        topic=row.topic or "",
                        token=decrypt_secret(row.token_enc),
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{row.name}: {redact(str(exc))}")
    if errors and not sent:
        raise ActionError("; ".join(errors))
    return {"sent": sent, "errors": errors}


def inverse_action(action_type: str) -> str | None:
    return {
        "proxmox_shutdown": "proxmox_start",
        "proxmox_stop": "proxmox_start",
        "pbs_shutdown": "wol",
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
