from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.engine.executor import execute_action
from app.models import Target
from app.schemas import TargetIn, TargetOut
from app.security import get_current_user
from app.serialize import target_out
from app.store import get_settings_map

router = APIRouter(prefix="/api/targets", tags=["targets"])


@router.get("", response_model=list[TargetOut])
async def list_targets(
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[TargetOut]:
    rows = (
        (await session.execute(select(Target).options(selectinload(Target.host)).order_by(Target.id)))
        .scalars()
        .all()
    )
    return [target_out(r) for r in rows]


@router.post("", response_model=TargetOut)
async def create_target(
    body: TargetIn,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TargetOut:
    row = Target(
        host_id=body.host_id,
        kind=body.kind,
        name=body.name,
        external_id=body.external_id,
        extra_json=json.dumps(body.extra or {}),
        critical=body.critical,
        enabled=body.enabled,
        notes=body.notes,
    )
    session.add(row)
    await session.commit()
    row = (
        await session.execute(select(Target).options(selectinload(Target.host)).where(Target.id == row.id))
    ).scalar_one()
    return target_out(row)


@router.put("/{target_id}", response_model=TargetOut)
async def update_target(
    target_id: int,
    body: TargetIn,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TargetOut:
    row = await session.get(Target, target_id)
    if not row:
        raise HTTPException(404, "Target not found")
    row.host_id = body.host_id
    row.kind = body.kind
    row.name = body.name
    row.external_id = body.external_id
    row.extra_json = json.dumps(body.extra or {})
    row.critical = body.critical
    row.enabled = body.enabled
    row.notes = body.notes
    await session.commit()
    row = (
        await session.execute(select(Target).options(selectinload(Target.host)).where(Target.id == target_id))
    ).scalar_one()
    return target_out(row)


@router.delete("/{target_id}")
async def delete_target(
    target_id: int,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(Target, target_id)
    if not row:
        raise HTTPException(404, "Target not found")
    await session.delete(row)
    await session.commit()
    return {"ok": True}


@router.post("/{target_id}/action")
async def target_action(
    target_id: int,
    action_type: str,
    dry_run: bool = True,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = (
        await session.execute(select(Target).options(selectinload(Target.host)).where(Target.id == target_id))
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Target not found")
    settings = await get_settings_map(session)
    try:
        result = await execute_action(
            session,
            {"type": action_type, "target_id": row.id},
            target=row,
            host=row.host,
            dry_run=dry_run,
            live=bool(settings.get("live_actions")),
            protect_critical=bool(settings.get("protect_critical")),
            notify_cfg=settings,
        )
        await session.commit()
        return {"ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
