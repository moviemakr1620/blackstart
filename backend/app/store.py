from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Setting

DEFAULTS: dict[str, Any] = {
    "live_actions": False,
    "engine_paused": False,
    "restore_only_what_we_stopped": True,
    "sample_retention_hours": 168,
    "notify_url": "",
    "notify_kind": "none",
    "ntfy_topic": "",
    "gotify_token": "",
    "default_restore_hold_sec": 120,
    "default_shed_hold_sec": 15,
    "protect_critical": True,
    "primary_ups_id": None,
}


async def get_settings_map(session: AsyncSession) -> dict[str, Any]:
    rows = (await session.execute(select(Setting))).scalars().all()
    out = dict(DEFAULTS)
    for row in rows:
        try:
            out[row.key] = json.loads(row.value_json)
        except json.JSONDecodeError:
            out[row.key] = row.value_json
    return out


async def get_setting(session: AsyncSession, key: str, default: Any = None) -> Any:
    row = await session.get(Setting, key)
    if row is None:
        return DEFAULTS.get(key, default)
    try:
        return json.loads(row.value_json)
    except json.JSONDecodeError:
        return row.value_json


async def put_setting(session: AsyncSession, key: str, value: Any) -> None:
    row = await session.get(Setting, key)
    payload = json.dumps(value)
    if row is None:
        session.add(Setting(key=key, value_json=payload))
    else:
        row.value_json = payload
