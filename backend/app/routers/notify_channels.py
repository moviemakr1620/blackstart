from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.notify import send_notification
from app.db import get_session
from app.models import NotifyChannel
from app.security import decrypt_secret, encrypt_secret, get_current_user

router = APIRouter(prefix="/api/notify-channels", tags=["notify"])


class ChannelIn(BaseModel):
    name: str
    kind: str = Field(pattern="^(discord|ntfy|gotify|webhook)$")
    enabled: bool = True
    url: str = ""
    topic: str = ""
    token: str | None = None


class ChannelOut(BaseModel):
    id: int
    name: str
    kind: str
    enabled: bool
    url: str
    topic: str
    has_token: bool


def _out(row: NotifyChannel) -> ChannelOut:
    return ChannelOut(
        id=row.id,
        name=row.name,
        kind=row.kind,
        enabled=row.enabled,
        url=row.url,
        topic=row.topic,
        has_token=bool(row.token_enc),
    )


@router.get("", response_model=list[ChannelOut])
async def list_channels(
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ChannelOut]:
    rows = (await session.execute(select(NotifyChannel).order_by(NotifyChannel.id))).scalars().all()
    return [_out(r) for r in rows]


@router.post("", response_model=ChannelOut)
async def create_channel(
    body: ChannelIn,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ChannelOut:
    row = NotifyChannel(
        name=body.name,
        kind=body.kind,
        enabled=body.enabled,
        url=body.url,
        topic=body.topic,
        token_enc=encrypt_secret(body.token) if body.token else "",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _out(row)


@router.put("/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: int,
    body: ChannelIn,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ChannelOut:
    row = await session.get(NotifyChannel, channel_id)
    if not row:
        raise HTTPException(404, "Channel not found")
    row.name = body.name
    row.kind = body.kind
    row.enabled = body.enabled
    row.url = body.url
    row.topic = body.topic
    if body.token:
        row.token_enc = encrypt_secret(body.token)
    await session.commit()
    await session.refresh(row)
    return _out(row)


@router.delete("/{channel_id}")
async def delete_channel(
    channel_id: int,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(NotifyChannel, channel_id)
    if not row:
        raise HTTPException(404, "Channel not found")
    await session.delete(row)
    await session.commit()
    return {"ok": True}


@router.post("/{channel_id}/test")
async def test_channel(
    channel_id: int,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(NotifyChannel, channel_id)
    if not row:
        raise HTTPException(404, "Channel not found")
    try:
        result = await send_notification(
            kind=row.kind,
            url=row.url,
            message="If you see this, notifications are working.",
            title="",
            topic=row.topic,
            token=decrypt_secret(row.token_enc),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    if result.get("skipped"):
        raise HTTPException(400, result.get("reason") or "skipped")
    return {"ok": True, "result": result}
