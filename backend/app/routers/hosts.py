from __future__ import annotations

import json

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.engine.executor import (
    discover_docker_ssh,
    docker_from,
    docker_via_ssh,
    k3s_from,
    omv_from,
    pbs_from,
    proxmox_from,
    ssh_from,
    synology_from,
    uses_ssh_docker,
)
from app.models import Host, Target, utcnow
from app.schemas import HostIn, HostOut, HostTestIn, TargetOut
from app.redact import redact
from app.security import SecretDecryptError, decrypt_json, encrypt_json, get_current_user
from app.secrets_update import HOST_CREDENTIAL_FIELDS, extra_without, merge_secrets
from app.serialize import host_out, target_out

router = APIRouter(prefix="/api/hosts", tags=["hosts"])


@router.get("", response_model=list[HostOut])
async def list_hosts(
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[HostOut]:
    rows = (await session.execute(select(Host).order_by(Host.id))).scalars().all()
    return [host_out(r) for r in rows]


@router.post("", response_model=HostOut)
async def create_host(
    body: HostIn,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> HostOut:
    row = Host(
        name=body.name,
        kind=body.kind,
        enabled=body.enabled,
        address=body.address,
        port=body.port,
        verify_tls=body.verify_tls,
        credentials_enc=encrypt_json(body.credentials),
        extra_json=json.dumps(body.extra or {}),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return host_out(row)


@router.put("/{host_id}", response_model=HostOut)
async def update_host(
    host_id: int,
    body: HostIn,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> HostOut:
    row = await session.get(Host, host_id)
    if not row:
        raise HTTPException(404, "Host not found")
    row.name = body.name
    row.kind = body.kind
    row.enabled = body.enabled
    row.address = body.address
    row.port = body.port
    row.verify_tls = body.verify_tls
    row.extra_json = json.dumps(extra_without(body.extra, body.clear_fields))
    if body.credentials or body.clear_fields:
        try:
            existing = decrypt_json(row.credentials_enc)
        except SecretDecryptError:
            existing = {}  # unreadable old value: replace it with what was entered
        row.credentials_enc = encrypt_json(
            merge_secrets(existing, body.credentials, body.clear_fields, HOST_CREDENTIAL_FIELDS)
        )
    await session.commit()
    await session.refresh(row)
    return host_out(row)


@router.delete("/{host_id}")
async def delete_host(
    host_id: int,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(Host, host_id)
    if not row:
        raise HTTPException(404, "Host not found")
    await session.delete(row)
    await session.commit()
    return {"ok": True}



def _restore_unsaved_tls(row: Host, saved_verify: bool, saved_extra: str | None) -> None:
    """Drop unsaved verify/ca_pem overrides, but keep other extra written during Test (HA, host key)."""
    row.verify_tls = saved_verify
    try:
        current = json.loads(row.extra_json or "{}")
        if not isinstance(current, dict):
            current = {}
    except json.JSONDecodeError:
        current = {}
    try:
        saved = json.loads(saved_extra or "{}")
        if not isinstance(saved, dict):
            saved = {}
    except json.JSONDecodeError:
        saved = {}
    if "ca_pem" in saved:
        current["ca_pem"] = saved["ca_pem"]
    else:
        current.pop("ca_pem", None)
    row.extra_json = json.dumps(current)


@router.post("/{host_id}/test")
async def test_host(
    host_id: int,
    body: HostTestIn | None = Body(default=None),
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Test uses the saved host. Optional body (verify_tls, ca_pem) overrides for this call only
    — blank ca_pem keeps the stored certificate (same as the form).
    """
    from app.tlsutil import TLSVerificationError, explain_tls_error, is_tls_error

    row = await session.get(Host, host_id)
    if not row:
        raise HTTPException(404, "Host not found")
    saved_verify, saved_extra = row.verify_tls, row.extra_json
    if body is not None:
        if body.verify_tls is not None:
            row.verify_tls = body.verify_tls
        extra = json.loads(row.extra_json or "{}")
        if not isinstance(extra, dict):
            extra = {}
        if "ca_pem" in (body.clear_fields or []):
            extra.pop("ca_pem", None)
            row.extra_json = json.dumps(extra)
        else:
            pem = (body.ca_pem or "").strip()
            if pem:
                extra["ca_pem"] = pem
                row.extra_json = json.dumps(extra)
    try:
        info: dict
        if row.kind == "proxmox":
            adapter = proxmox_from(row)
            version = await adapter.version()
            guests = await adapter.discover()
            info = {
                "version": version,
                "vms": sum(1 for g in guests if g["kind"] == "vm"),
                "lxcs": sum(1 for g in guests if g["kind"] == "lxc"),
                "nodes": adapter.stats.get("nodes") or [],
                "warnings": adapter.warnings,
            }
        elif row.kind == "pbs":
            adapter = pbs_from(row)
            info = {"version": await adapter.version(), "node": await adapter.node_name()}
        elif row.kind == "synology":
            ping = await synology_from(row).ping()
            extra = json.loads(row.extra_json or "{}")
            if not isinstance(extra, dict):
                extra = {}
            extra["ha"] = bool(ping.get("ha"))
            row.extra_json = json.dumps(extra)
            info = {"info": ping}
        elif row.kind == "docker":
            if uses_ssh_docker(row):
                info = await docker_via_ssh(row, "docker info --format '{{.ServerVersion}}'")
            else:
                ok = await docker_from(row).ping()
                ver = await docker_from(row).version() if ok else {}
                info = {"ping": ok, "version": ver.get("Version")}
        elif row.kind == "ssh":
            info = await ssh_from(row).ping()
        elif row.kind == "k3s":
            probe = await ssh_from(row).run("true")
            info = {"nodes": await k3s_from(row).nodes(), "host_key": probe.get("host_key")}
        elif row.kind == "omv":
            info = await omv_from(row).ping()
        else:
            raise HTTPException(400, f"unknown host kind {row.kind}")
        row.last_ok_at = utcnow()
        _pin_host_key(row, info)
        if row.kind == "proxmox" and isinstance(info, dict) and info.get("warnings"):
            row.last_error = " | ".join(str(w) for w in info["warnings"][:6])
        else:
            row.last_error = ""
        _restore_unsaved_tls(row, saved_verify, saved_extra)
        await session.commit()
        return {"ok": True, "info": info}
    except HTTPException:
        _restore_unsaved_tls(row, saved_verify, saved_extra)
        raise
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        if isinstance(exc, TLSVerificationError):
            message = str(exc)
        elif is_tls_error(exc):
            from app.engine.executor import tls_verify
            message = explain_tls_error(exc, address=row.address, verify=tls_verify(row))
        _restore_unsaved_tls(row, saved_verify, saved_extra)
        row.last_error = redact(message, limit=2500)
        await session.commit()
        raise HTTPException(400, row.last_error) from exc


def _pin_host_key(row: Host, info: object) -> None:
    """Trust-on-first-use: remember the SSH host key seen during Test."""
    seen = info.get("host_key") if isinstance(info, dict) else None
    if not seen:
        return
    extra = json.loads(row.extra_json or "{}")
    if not isinstance(extra, dict):
        extra = {}
    if not extra.get("ssh_host_key"):
        extra["ssh_host_key"] = seen
        row.extra_json = json.dumps(extra)


@router.post("/{host_id}/forget-host-key", response_model=HostOut)
async def forget_host_key(
    host_id: int,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> HostOut:
    """Clear the pinned SSH host key (after rebuilding that machine)."""
    row = await session.get(Host, host_id)
    if not row:
        raise HTTPException(404, "Host not found")
    extra = json.loads(row.extra_json or "{}")
    if isinstance(extra, dict):
        extra.pop("ssh_host_key", None)
        row.extra_json = json.dumps(extra)
    await session.commit()
    await session.refresh(row)
    return host_out(row)


@router.post("/{host_id}/discover", response_model=list[TargetOut])
async def discover_host(
    host_id: int,
    import_new: bool = True,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[TargetOut]:
    row = await session.get(Host, host_id)
    if not row:
        raise HTTPException(404, "Host not found")
    try:
        warnings = ""
        if row.kind == "proxmox":
            adapter = proxmox_from(row)
            found = await adapter.discover()
            warnings = " | ".join(adapter.warnings[:6])
        elif row.kind == "pbs":
            found = await pbs_from(row).discover()
        elif row.kind == "synology":
            found = await synology_from(row).discover()
        elif row.kind == "docker":
            found = await discover_docker_ssh(row) if uses_ssh_docker(row) else await docker_from(row).discover()
        elif row.kind == "ssh":
            found = await discover_docker_ssh(row)
        elif row.kind == "k3s":
            found = await k3s_from(row).discover()
        elif row.kind == "omv":
            found = await omv_from(row).discover()
            try:
                found.extend(await discover_docker_ssh(row))
            except Exception:
                pass
        else:
            raise HTTPException(400, "Discover is not supported for this host kind")
        row.last_ok_at = utcnow()
        row.last_error = warnings
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        row.last_error = redact(str(exc))
        await session.commit()
        raise HTTPException(400, row.last_error) from exc

    existing = (
        (await session.execute(select(Target).where(Target.host_id == host_id).options(selectinload(Target.host))))
        .scalars()
        .all()
    )
    index = {(t.kind, t.external_id): t for t in existing}
    created: list[Target] = []
    for item in found:
        key = (item["kind"], str(item.get("external_id") or ""))
        if key in index:
            index[key].last_status = str(item.get("last_status") or index[key].last_status)
            index[key].last_checked = utcnow()
            incoming = item.get("extra") or {}
            if incoming:
                try:
                    current = json.loads(index[key].extra_json or "{}")
                except json.JSONDecodeError:
                    current = {}
                if not isinstance(current, dict):
                    current = {}
                for extra_key, extra_val in incoming.items():
                    if extra_key in {"mac", "mac_passive", "mac_active", "macs"} and current.get(extra_key):
                        continue
                    current[extra_key] = extra_val
                index[key].extra_json = json.dumps(current)
            continue
        if not import_new:
            continue
        extra = item.get("extra") or {}
        target = Target(
            host_id=host_id,
            kind=item["kind"],
            name=item["name"],
            external_id=str(item.get("external_id") or ""),
            extra_json=json.dumps(extra),
            last_status=str(item.get("last_status") or "unknown"),
            last_checked=utcnow(),
        )
        session.add(target)
        created.append(target)
    await session.commit()
    rows = (
        (await session.execute(select(Target).where(Target.host_id == host_id).options(selectinload(Target.host))))
        .scalars()
        .all()
    )
    return [target_out(t) for t in rows]
