from __future__ import annotations

from typing import Any

import httpx


def looks_like_discord(url: str) -> bool:
    u = (url or "").lower()
    return "discord.com/api/webhooks" in u or "discordapp.com/api/webhooks" in u


async def send_notification(
    kind: str,
    url: str,
    message: str,
    title: str = "Blackstart",
    topic: str = "",
    token: str = "",
) -> dict[str, Any]:
    kind = (kind or "webhook").lower()
    if kind == "none":
        return {"skipped": True, "reason": "notifications are set to none"}
    if not url:
        return {"skipped": True, "reason": "no notify URL in Settings"}

    # Discord rejects our generic {title,message} JSON with 400. Always use content.
    if kind == "discord" or looks_like_discord(url):
        kind = "discord"

    text = f"**{title}**\n{message}".strip()
    async with httpx.AsyncClient(timeout=12.0) as client:
        if kind == "ntfy":
            target = url.rstrip("/")
            if topic:
                target = f"{target}/{topic}"
            resp = await client.post(target, content=message.encode(), headers={"Title": title, "Tags": "zap"})
            resp.raise_for_status()
            return {"ok": True, "status": resp.status_code, "kind": "ntfy"}
        if kind == "gotify":
            resp = await client.post(
                f"{url.rstrip('/')}/message",
                params={"token": token} if token else None,
                json={"title": title, "message": message, "priority": 8},
            )
            resp.raise_for_status()
            return {"ok": True, "kind": "gotify"}
        if kind == "discord":
            resp = await client.post(url, json={"content": text[:1900]})
            if resp.status_code >= 400:
                raise RuntimeError(f"Discord webhook failed ({resp.status_code}): {resp.text[:300]}")
            return {"ok": True, "status": resp.status_code, "kind": "discord"}
        if kind in {"webhook", "generic", ""}:
            resp = await client.post(
                url,
                json={"title": title, "message": message, "source": "blackstart", "content": text},
            )
            resp.raise_for_status()
            return {"ok": True, "status": resp.status_code, "kind": "webhook"}
    return {"skipped": True, "reason": f"unknown kind {kind}"}
