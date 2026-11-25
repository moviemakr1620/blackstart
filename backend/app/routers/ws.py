from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.db import SessionLocal
from app.engine.bus import CLOSE, bus
from app.engine.service import engine
from app.security import user_from_token

router = APIRouter()


@router.websocket("/api/ws")
async def websocket(ws: WebSocket) -> None:
    """Live updates. The browser sends {"token": "..."} as its first message,
    so the token never sits in a URL (and so never in proxy access logs)."""
    await ws.accept()
    try:
        first = await asyncio.wait_for(ws.receive_text(), timeout=10)
        token = str(json.loads(first).get("token") or "")
        async with SessionLocal() as session:
            user = await user_from_token(session, token)
        if user.must_change_password:
            raise PermissionError("password change required")
    except Exception:  # noqa: BLE001
        await ws.close(code=4401)
        return
    await ws.send_json({"event": "hello", "payload": {"mode": engine.power_mode, "ups": engine.last_status}})
    try:
        async for message in bus.stream():
            if message is CLOSE:
                # This client fell too far behind; let it reconnect and resync.
                await ws.close(code=4408)
                return
            await ws.send_text(message)
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await ws.close()
        except Exception:
            pass
