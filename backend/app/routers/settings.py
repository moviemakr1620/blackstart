from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.notify import send_notification
from app.db import get_session
from app.schemas import AppSettingsIn, AppSettingsOut
from app.security import get_current_user
from app.store import DEFAULTS, get_settings_map, put_setting

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=AppSettingsOut)
async def get_settings(
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AppSettingsOut:
    data = await get_settings_map(session)
    return AppSettingsOut(**{k: data.get(k, v) for k, v in DEFAULTS.items()})


@router.put("", response_model=AppSettingsOut)
async def update_settings(
    body: AppSettingsIn,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AppSettingsOut:
    incoming = body.model_dump(exclude_unset=True)
    for key, value in incoming.items():
        await put_setting(session, key, value)
    await session.commit()
    data = await get_settings_map(session)
    return AppSettingsOut(**{k: data.get(k, v) for k, v in DEFAULTS.items()})


class TestNotifyIn(BaseModel):
    message: str = "Blackstart test: if you see this, notifications are working."


@router.post("/test-notify")
async def test_notify(
    body: TestNotifyIn,
    _: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    cfg = await get_settings_map(session)
    try:
        result = await send_notification(
            kind=str(cfg.get("notify_kind") or "none"),
            url=str(cfg.get("notify_url") or ""),
            message=body.message,
            title="Blackstart test",
            topic=str(cfg.get("ntfy_topic") or ""),
            token=str(cfg.get("gotify_token") or ""),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    if result.get("skipped"):
        raise HTTPException(400, result.get("reason") or "notification skipped")
    return {"ok": True, "result": result}
