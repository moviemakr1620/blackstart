from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.engine.executor import send_to_channels
from app.netguard import check_outbound_url
from app.schemas import AppSettingsIn, AppSettingsOut
from app.security import encrypt_secret, get_current_user
from app.store import DEFAULTS, get_settings_map, put_setting

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _out(data: dict[str, Any]) -> AppSettingsOut:
    values = {k: data.get(k, v) for k, v in DEFAULTS.items() if k != "heartbeat_url"}
    return AppSettingsOut(**values, heartbeat_url_set=bool(data.get("heartbeat_url")))


@router.get("", response_model=AppSettingsOut)
async def get_settings(
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AppSettingsOut:
    return _out(await get_settings_map(session))


@router.put("", response_model=AppSettingsOut)
async def update_settings(
    body: AppSettingsIn,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AppSettingsOut:
    incoming = body.model_dump(exclude_unset=True)
    hb = incoming.pop("heartbeat_url", None)
    if hb is not None:
        hb = hb.strip()
        if hb:
            try:
                check_outbound_url(hb)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        await put_setting(session, "heartbeat_url", encrypt_secret(hb) if hb else "")
    for key, value in incoming.items():
        await put_setting(session, key, value)
    await session.commit()
    return _out(await get_settings_map(session))


class TestNotifyIn(BaseModel):
    message: str = "If you see this, notifications are working."


@router.post("/test-notify")
async def test_notify(
    body: TestNotifyIn,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Send a test message to every enabled notification channel."""
    try:
        result = await send_to_channels(session, body.message, title="Blackstart test")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "result": result}
