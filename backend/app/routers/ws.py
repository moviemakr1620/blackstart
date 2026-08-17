from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.engine.bus import bus
from app.engine.service import engine
from app.security import decode_token

router = APIRouter()


@router.websocket("/api/ws")
async def websocket(ws: WebSocket) -> None:
    token = ws.query_params.get("token") or ""
    try:
        decode_token(token)
    except Exception:
        await ws.close(code=4401)
        return
    await ws.accept()
    await ws.send_json({"event": "hello", "payload": {"mode": engine.power_mode, "ups": engine.last_status}})
    try:
        async for message in bus.stream():
            await ws.send_text(message)
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await ws.close()
        except Exception:
            pass
