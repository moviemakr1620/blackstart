from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

UPS_ICON = Path(__file__).resolve().parent.parent / "assets" / "ups.jpg"


def looks_like_discord(url: str) -> bool:
    u = (url or "").lower()
    return "discord.com/api/webhooks" in u or "discordapp.com/api/webhooks" in u


def _discord_color(title: str, message: str) -> int:
    blob = f"{title} {message}".lower()
    if any(w in blob for w in ("error", "fail", "critical", "low battery")):
        return 0xFF5C7A
    if any(w in blob for w in ("battery", "grid lost", "outage", "shed", "offline", "lost", "power lost")):
        return 0xF5B942
    return 0x5CE1C5


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

    if kind == "discord" or looks_like_discord(url):
        kind = "discord"

    text = f"**{title}**\n{message}".strip()
    async with httpx.AsyncClient(timeout=15.0) as client:
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
            payload = {
                "username": "Blackstart",
                "embeds": [
                    {
                        "author": {"name": "Blackstart", "icon_url": "attachment://ups.jpg"},
                        "title": title[:256],
                        "description": (message or "")[:4000],
                        "color": _discord_color(title, message),
                        "thumbnail": {"url": "attachment://ups.jpg"},
                        "footer": {"text": "Homelab power orchestrator"},
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                ],
            }
            files = None
            if UPS_ICON.is_file():
                files = {"files[0]": ("ups.jpg", UPS_ICON.read_bytes(), "image/jpeg")}
            resp = await client.post(url, data={"payload_json": json.dumps(payload)}, files=files)
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
