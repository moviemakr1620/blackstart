from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.engine.service import engine
from app.models import UpsSample, UpsSource
from app.schemas import SimulateIn, UpsSourceIn, UpsSourceOut, UpsStatus
from app.security import encrypt_secret, get_current_user
from app.serialize import source_out

router = APIRouter(prefix="/api/ups", tags=["ups"])


@router.get("/status")
async def ups_status(_: str = Depends(get_current_user)) -> dict:
    snap = dict(engine.last_status or {})
    snap.setdefault("connected", bool(engine.last_status))
    snap.setdefault("error", engine.last_error)
    snap["power_mode"] = engine.power_mode
    snap["running_run_id"] = engine.running_run_id
    return snap


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


@router.get("/history")
async def ups_history(
    hours: int = 24,
    source_id: int | None = None,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    # SQLite datetime comparisons with tz-aware cutoffs can match zero rows.
    # Pull recent samples by id, then filter in Python.
    hours = max(1, min(hours, 168))
    rows = (
        (await session.execute(select(UpsSample).order_by(UpsSample.id.desc()).limit(hours * 800)))
        .scalars()
        .all()
    )
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    kept = []
    for r in reversed(rows):
        ts = _aware(r.ts)
        if ts is None or ts < cutoff:
            continue
        if source_id is not None and r.source_id != source_id:
            continue
        kept.append(
            {
                "ts": ts.isoformat(),
                "status": r.status,
                "charge": r.charge,
                "runtime_sec": r.runtime_sec,
                "load_pct": r.load_pct,
                "watts": r.watts,
                "input_voltage": r.input_voltage,
            }
        )
    return kept


@router.get("/sources", response_model=list[UpsSourceOut])
async def list_sources(
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[UpsSourceOut]:
    rows = (await session.execute(select(UpsSource).order_by(UpsSource.id))).scalars().all()
    return [source_out(r) for r in rows]


@router.post("/sources", response_model=UpsSourceOut)
async def create_source(
    body: UpsSourceIn,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UpsSourceOut:
    row = UpsSource(
        name=body.name,
        kind=body.kind,
        enabled=body.enabled,
        host=body.host,
        port=body.port,
        base_url=body.base_url,
        device_name=body.device_name,
        username=body.username,
        password_enc=encrypt_secret(body.password) if body.password else "",
        poll_interval_sec=body.poll_interval_sec,
        extra_json=json.dumps(body.extra or {}),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return source_out(row)


@router.put("/sources/{source_id}", response_model=UpsSourceOut)
async def update_source(
    source_id: int,
    body: UpsSourceIn,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UpsSourceOut:
    row = await session.get(UpsSource, source_id)
    if not row:
        raise HTTPException(404, "UPS source not found")
    row.name = body.name
    row.kind = body.kind
    row.enabled = body.enabled
    row.host = body.host
    row.port = body.port
    row.base_url = body.base_url
    row.device_name = body.device_name
    row.username = body.username
    if body.password:
        row.password_enc = encrypt_secret(body.password)
    row.poll_interval_sec = body.poll_interval_sec
    row.extra_json = json.dumps(body.extra or {})
    await session.commit()
    await session.refresh(row)
    return source_out(row)


@router.delete("/sources/{source_id}")
async def delete_source(
    source_id: int,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(UpsSource, source_id)
    if not row:
        raise HTTPException(404, "UPS source not found")
    await session.delete(row)
    await session.commit()
    return {"ok": True}


@router.post("/sources/{source_id}/test")
async def test_source(
    source_id: int,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(UpsSource, source_id)
    if not row:
        raise HTTPException(404, "UPS source not found")
    prev = row.enabled
    # Temporarily read using this source by flipping enabled in-memory for the reader
    others = (await session.execute(select(UpsSource))).scalars().all()
    saved = [(o.id, o.enabled) for o in others]
    for o in others:
        o.enabled = o.id == source_id
    await session.flush()
    try:
        snap, _src = await engine.read_ups(session)
        return {"ok": True, "snapshot": snap}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    finally:
        lookup = dict(saved)
        for o in others:
            o.enabled = lookup.get(o.id, False)
        row.enabled = prev
        await session.commit()


@router.post("/simulate")
async def simulate(body: SimulateIn, _: str = Depends(get_current_user)) -> dict:
    event = body.event
    if event == "pull_plug":
        engine.simulator.pull_plug()
    elif event == "restore_grid":
        engine.simulator.restore_grid()
    elif event == "set_charge":
        if body.charge is None:
            raise HTTPException(400, "charge is required")
        engine.simulator.set_charge(body.charge)
    elif event == "set_load":
        if body.load_watts is None:
            raise HTTPException(400, "load_watts is required")
        engine.simulator.set_load(body.load_watts)
    else:
        raise HTTPException(400, f"unknown event {event}")
    snap = engine.simulator.snapshot()
    return {"ok": True, "snapshot": snap}
