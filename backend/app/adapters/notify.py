from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.netguard import check_outbound_url
from app.redact import redact

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


def _embed_title(title: str, message: str) -> str | None:
    """Drop titles that just repeat the author name or the body."""
    t = (title or "").strip()
    body = (message or "").strip()
    if not t:
        return None
    if t.lower() in {"blackstart", "blackstart test"}:
        return None
    if t.lower() == body.lower():
        return None
    if body.lower().startswith(t.lower()):
        return None
    return t[:256]


class NotifyError(RuntimeError):
    pass


RETRY_DELAYS = (2.0, 5.0)


async def send_notification(
    kind: str,
    url: str,
    message: str,
    title: str = "",
    topic: str = "",
    token: str = "",
    retries: tuple[float, ...] = RETRY_DELAYS,
) -> dict[str, Any]:
    """Send one message. Raises NotifyError if it could not be delivered.

    A channel with no URL or an unknown kind is an error, not a silent skip, so a
    misconfigured channel shows up as a failed step instead of a fake "OK".
    Network errors and 5xx/429 answers are retried a couple of times.
    """
    kind = (kind or "webhook").lower()
    if kind == "none":
        raise NotifyError("This channel is set to 'none'")
    if not url:
        raise NotifyError("This channel has no URL")
    try:
        await asyncio.to_thread(check_outbound_url, url)
    except ValueError as exc:
        raise NotifyError(str(exc)) from exc
    if kind == "discord" or looks_like_discord(url):
        kind = "discord"
    if kind not in {"ntfy", "gotify", "discord", "webhook", "generic", ""}:
        raise NotifyError(f"Unknown channel kind '{kind}'")

    last: Exception | None = None
    for attempt in range(len(retries) + 1):
        try:
            return await _send_once(kind, url, message, title, topic, token)
        except _Retryable as exc:
            last = exc
        except httpx.TransportError as exc:
            last = exc
        if attempt < len(retries):
            await asyncio.sleep(retries[attempt])
    raise NotifyError(redact(f"{kind} send failed after {len(retries) + 1} tries: {last}"))


class _Retryable(RuntimeError):
    pass


def _check(resp: httpx.Response, kind: str) -> None:
    if resp.status_code == 429 or resp.status_code >= 500:
        raise _Retryable(f"{kind} answered {resp.status_code}")
    if resp.status_code >= 400:
        raise NotifyError(redact(f"{kind} rejected the message ({resp.status_code}): {resp.text[:200]}"))


async def _send_once(kind: str, url: str, message: str, title: str, topic: str, token: str) -> dict[str, Any]:
    heading = _embed_title(title, message)
    ntfy_title = heading or "Blackstart"
    text = message.strip() if message.strip() else (heading or "Blackstart")
    async with httpx.AsyncClient(timeout=15.0) as client:
        if kind == "ntfy":
            target = url.rstrip("/")
            if topic:
                target = f"{target}/{topic}"
            resp = await client.post(target, content=text.encode(), headers={"Title": ntfy_title, "Tags": "zap"})
            _check(resp, "ntfy")
            return {"ok": True, "status": resp.status_code, "kind": "ntfy"}
        if kind == "gotify":
            resp = await client.post(
                f"{url.rstrip('/')}/message",
                headers={"X-Gotify-Key": token} if token else None,
                json={"title": ntfy_title, "message": text, "priority": 8},
            )
            _check(resp, "gotify")
            return {"ok": True, "kind": "gotify"}
        if kind == "discord":
            embed: dict[str, Any] = {
                "author": {"name": "Blackstart", "icon_url": "attachment://ups.jpg"},
                "description": text[:4000],
                "color": _discord_color(title, message),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            if heading:
                embed["title"] = heading
            payload = {"embeds": [embed]}
            files = None
            if UPS_ICON.is_file():
                files = {"files[0]": ("ups.jpg", UPS_ICON.read_bytes(), "image/jpeg")}
            resp = await client.post(url, data={"payload_json": json.dumps(payload)}, files=files)
            _check(resp, "Discord")
            return {"ok": True, "status": resp.status_code, "kind": "discord"}
        resp = await client.post(
            url,
            json={"title": title, "message": message, "source": "blackstart", "content": text},
        )
        _check(resp, "webhook")
        return {"ok": True, "status": resp.status_code, "kind": "webhook"}
