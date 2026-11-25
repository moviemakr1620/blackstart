from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.notify import send_notification
from app.db import get_session
from app.models import NotifyChannel
from app.netguard import check_outbound_url
from app.redact import redact
from app.security import SecretDecryptError, decrypt_secret, encrypt_secret, get_current_user

router = APIRouter(prefix="/api/notify-channels", tags=["notify"])


class ChannelIn(BaseModel):
    name: str
    kind: str = Field(pattern="^(discord|ntfy|gotify|webhook)$")
    enabled: bool = True
    # Empty on update keeps the stored URL (it is never sent back to the browser).
    url: str = ""
    topic: str = ""
    token: str | None = None


class ChannelOut(BaseModel):
    id: int
    name: str
    kind: str
    enabled: bool
    url_hint: str
    has_url: bool
    topic: str
    has_token: bool
    error: str = ""


def channel_url(row: NotifyChannel) -> str:
    if row.url_enc:
        return decrypt_secret(row.url_enc)
    return row.url or ""


def _hint(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return f"{parts.scheme}://{host}/…" if parts.path not in ("", "/") else f"{parts.scheme}://{host}/"


def _out(row: NotifyChannel) -> ChannelOut:
    error = ""
    try:
        url = channel_url(row)
    except SecretDecryptError as exc:
        url, error = "", str(exc)
    return ChannelOut(
        id=row.id,
        name=row.name,
        kind=row.kind,
        enabled=row.enabled,
        url_hint=_hint(url),
        has_url=bool(url) or bool(error),
        topic=row.topic,
        has_token=bool(row.token_enc),
        error=error,
    )


def _check(url: str) -> None:
    if not url:
        return
    try:
        check_outbound_url(url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


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
    url = body.url.strip()
    if not url:
        raise HTTPException(400, "Enter the channel URL")
    _check(url)
    row = NotifyChannel(
        name=body.name,
        kind=body.kind,
        enabled=body.enabled,
        url="",
        url_enc=encrypt_secret(url),
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
    url = body.url.strip()
    if url:
        _check(url)
        row.url_enc = encrypt_secret(url)
        row.url = ""
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
            url=channel_url(row),
            message="If you see this, notifications are working.",
            title="",
            topic=row.topic,
            token=decrypt_secret(row.token_enc),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, redact(str(exc))) from exc
    return {"ok": True, "result": result}
