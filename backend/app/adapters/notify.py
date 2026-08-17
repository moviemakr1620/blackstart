from __future__ import annotations

from typing import Any

import httpx


async def send_notification(
    kind: str,
    url: str,
    message: str,
    title: str = "Blackstart",
    topic: str = "",
    token: str = "",
) -> dict[str, Any]:
    kind = (kind or "webhook").lower()
    if not url and kind != "none":
        return {"skipped": True, "reason": "no notify url"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        if kind == "ntfy":
            target = url.rstrip("/")
            if topic:
                target = f"{target}/{topic}"
            resp = await client.post(
                target,
                content=message.encode(),
                headers={"Title": title, "Tags": "zap"},
            )
            resp.raise_for_status()
            return {"ok": True, "status": resp.status_code}
        if kind == "gotify":
            resp = await client.post(
                f"{url.rstrip('/')}/message",
                params={"token": token} if token else None,
                json={"title": title, "message": message, "priority": 8},
            )
            resp.raise_for_status()
            return {"ok": True}
        if kind in {"webhook", "generic", ""}:
            resp = await client.post(url, json={"title": title, "message": message, "source": "blackstart"})
            resp.raise_for_status()
            return {"ok": True, "status": resp.status_code}
        if kind == "none":
            return {"skipped": True}
    return {"skipped": True, "reason": f"unknown kind {kind}"}
