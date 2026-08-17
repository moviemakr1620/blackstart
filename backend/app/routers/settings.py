from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

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
