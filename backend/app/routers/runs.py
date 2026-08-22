from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.engine.service import engine
from app.models import AuditLog, Episode, EpisodeAction, Run, RunEvent
from app.schemas import EpisodeOut, RunDetailOut, RunEventOut, RunOut
from app.security import get_current_user

router = APIRouter(prefix="/api", tags=["runs"])


@router.get("/runs", response_model=list[RunOut])
async def list_runs(
    limit: int = 50,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[RunOut]:
    rows = (
        (await session.execute(select(Run).order_by(Run.id.desc()).limit(min(limit, 200))))
        .scalars()
        .all()
    )
    return [
        RunOut(
            id=r.id,
            policy_id=r.policy_id,
            episode_id=r.episode_id,
            kind=r.kind,
            trigger=r.trigger,
            dry_run=r.dry_run,
            status=r.status,
            started_at=r.started_at,
            finished_at=r.finished_at,
        )
        for r in rows
    ]


@router.get("/runs/{run_id}", response_model=RunDetailOut)
async def get_run(
    run_id: int,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RunDetailOut:
    row = (
        await session.execute(select(Run).options(selectinload(Run.events)).where(Run.id == run_id))
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Run not found")
    return RunDetailOut(
        id=row.id,
        policy_id=row.policy_id,
        episode_id=row.episode_id,
        kind=row.kind,
        trigger=row.trigger,
        dry_run=row.dry_run,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        events=[
            RunEventOut(
                id=e.id,
                ts=e.ts,
                level=e.level,
                message=e.message,
                payload=json.loads(e.payload_json or "{}"),
            )
            for e in row.events
        ],
    )


@router.post("/runs/abort")
async def abort_run(_: str = Depends(get_current_user)) -> dict:
    engine.abort()
    return {"ok": True, "running_run_id": engine.running_run_id}


@router.delete("/history")
async def clear_history(
    actor: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Wipe History: runs, step logs, and outage episodes. Keeps UPS samples and inventory."""
    engine.abort()
    engine.conditions.reset_episode()
    events = (await session.execute(sql_delete(RunEvent))).rowcount or 0
    runs = (await session.execute(sql_delete(Run))).rowcount or 0
    actions = (await session.execute(sql_delete(EpisodeAction))).rowcount or 0
    episodes = (await session.execute(sql_delete(Episode))).rowcount or 0
    session.add(
        AuditLog(
            actor=actor,
            action="history.clear",
            detail=f"runs={runs} events={events} episodes={episodes} actions={actions}",
        )
    )
    await session.commit()
    return {"ok": True, "runs": runs, "events": events, "episodes": episodes, "actions": actions}


@router.get("/episodes", response_model=list[EpisodeOut])
async def list_episodes(
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[EpisodeOut]:
    rows = (await session.execute(select(Episode).order_by(Episode.id.desc()).limit(100))).scalars().all()
    return [
        EpisodeOut(
            id=r.id,
            started_at=r.started_at,
            ended_at=r.ended_at,
            cause=r.cause,
            status=r.status,
            min_charge=r.min_charge,
        )
        for r in rows
    ]


@router.get("/audit")
async def audit(
    limit: int = 100,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (await session.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 300)))).scalars().all()
    return [
        {"id": r.id, "ts": r.ts.isoformat(), "actor": r.actor, "action": r.action, "detail": r.detail}
        for r in rows
    ]


@router.get("/events/recent")
async def recent_events(
    limit: int = 80,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (await session.execute(select(RunEvent).order_by(RunEvent.id.desc()).limit(min(limit, 200)))).scalars().all()
    return [
        {
            "id": r.id,
            "run_id": r.run_id,
            "ts": r.ts.isoformat(),
            "level": r.level,
            "message": r.message,
        }
        for r in reversed(rows)
    ]
