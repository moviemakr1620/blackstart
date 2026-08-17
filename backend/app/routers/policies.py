from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.engine.service import engine
from app.models import Policy, PolicyStep, utcnow
from app.schemas import ManualRunIn, PolicyIn, PolicyOut
from app.security import get_current_user
from app.serialize import policy_out

router = APIRouter(prefix="/api/policies", tags=["policies"])


async def _load(session: AsyncSession, policy_id: int) -> Policy:
    row = (
        await session.execute(
            select(Policy).options(selectinload(Policy.steps)).where(Policy.id == policy_id)
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Playbook not found")
    return row


@router.get("", response_model=list[PolicyOut])
async def list_policies(
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[PolicyOut]:
    rows = (
        (await session.execute(select(Policy).options(selectinload(Policy.steps)).order_by(Policy.kind, Policy.priority)))
        .scalars()
        .unique()
        .all()
    )
    return [policy_out(r) for r in rows]


@router.post("", response_model=PolicyOut)
async def create_policy(
    body: PolicyIn,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PolicyOut:
    row = Policy(
        name=body.name,
        kind=body.kind,
        description=body.description,
        enabled=body.enabled,
        priority=body.priority,
    )
    session.add(row)
    await session.flush()
    for step in body.steps:
        session.add(
            PolicyStep(
                policy_id=row.id,
                order=step.order,
                name=step.name,
                enabled=step.enabled,
                trigger_json=json.dumps(step.trigger or {}),
                action_json=json.dumps(step.action or {}),
                delay_before_sec=step.delay_before_sec,
                timeout_sec=step.timeout_sec,
                continue_on_error=step.continue_on_error,
            )
        )
    await session.commit()
    return policy_out(await _load(session, row.id))


@router.put("/{policy_id}", response_model=PolicyOut)
async def update_policy(
    policy_id: int,
    body: PolicyIn,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PolicyOut:
    row = await _load(session, policy_id)
    row.name = body.name
    row.kind = body.kind
    row.description = body.description
    row.enabled = body.enabled
    row.priority = body.priority
    row.updated_at = utcnow()

    incoming_ids = {s.id for s in body.steps if s.id}
    for existing in list(row.steps):
        if existing.id not in incoming_ids:
            await session.delete(existing)

    for step in body.steps:
        if step.id:
            current = next((s for s in row.steps if s.id == step.id), None)
            if current is None:
                continue
            current.order = step.order
            current.name = step.name
            current.enabled = step.enabled
            current.trigger_json = json.dumps(step.trigger or {})
            current.action_json = json.dumps(step.action or {})
            current.delay_before_sec = step.delay_before_sec
            current.timeout_sec = step.timeout_sec
            current.continue_on_error = step.continue_on_error
        else:
            session.add(
                PolicyStep(
                    policy_id=row.id,
                    order=step.order,
                    name=step.name,
                    enabled=step.enabled,
                    trigger_json=json.dumps(step.trigger or {}),
                    action_json=json.dumps(step.action or {}),
                    delay_before_sec=step.delay_before_sec,
                    timeout_sec=step.timeout_sec,
                    continue_on_error=step.continue_on_error,
                )
            )
    await session.commit()
    return policy_out(await _load(session, policy_id))


@router.delete("/{policy_id}")
async def delete_policy(
    policy_id: int,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(Policy, policy_id)
    if not row:
        raise HTTPException(404, "Playbook not found")
    await session.delete(row)
    await session.commit()
    return {"ok": True}


@router.post("/{policy_id}/run")
async def run_policy(
    policy_id: int,
    body: ManualRunIn,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await _load(session, policy_id)
    run = await engine.run_policy(
        session,
        row,
        dry_run=body.dry_run,
        trigger="manual",
        step_ids=body.step_ids,
    )
    await session.commit()
    return {"ok": True, "run_id": run.id, "status": run.status, "dry_run": run.dry_run}


@router.post("/restore-auto")
async def restore_auto(
    body: ManualRunIn,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    run = await engine.run_restore_auto(session, dry_run=body.dry_run)
    await session.commit()
    return {"ok": True, "run_id": run.id, "status": run.status, "dry_run": run.dry_run}
