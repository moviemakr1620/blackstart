from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
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
from app.schemas import HostIn, HostOut, TargetOut
from app.security import decrypt_json, encrypt_json, get_current_user
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
    row.extra_json = json.dumps(body.extra or {})
    if body.credentials:
        existing = decrypt_json(row.credentials_enc)
        existing.update({k: v for k, v in body.credentials.items() if v not in (None, "")})
        row.credentials_enc = encrypt_json(existing)
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


@router.post("/{host_id}/test")
async def test_host(
    host_id: int,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(Host, host_id)
    if not row:
        raise HTTPException(404, "Host not found")
    try:
        info: dict
        if row.kind == "proxmox":
            info = {"version": await proxmox_from(row).version()}
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
            info = {"nodes": await k3s_from(row).nodes()}
        elif row.kind == "omv":
            info = await omv_from(row).ping()
        else:
            raise HTTPException(400, f"unknown host kind {row.kind}")
        row.last_ok_at = utcnow()
        row.last_error = ""
        await session.commit()
        return {"ok": True, "info": info}
    except Exception as exc:  # noqa: BLE001
        row.last_error = str(exc)
        await session.commit()
        raise HTTPException(400, str(exc)) from exc


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
        if row.kind == "proxmox":
            found = await proxmox_from(row).discover()
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
        row.last_error = ""
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        row.last_error = str(exc)
        await session.commit()
        raise HTTPException(400, str(exc)) from exc

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
