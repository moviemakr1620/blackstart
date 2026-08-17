from __future__ import annotations

from typing import Any

import httpx

from app.adapters.nut import snapshot_from_vars


class PeanutClient:
    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        timeout: float = 8.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = (username, password) if username else None
        self.timeout = timeout

    async def _get(self, path: str) -> Any:
        async with httpx.AsyncClient(timeout=self.timeout, auth=self.auth, follow_redirects=True) as client:
            resp = await client.get(f"{self.base_url}{path}")
            resp.raise_for_status()
            if resp.headers.get("content-type", "").startswith("application/json"):
                return resp.json()
            return resp.text

    async def ping(self) -> bool:
        try:
            body = await self._get("/api/ping")
            return "pong" in str(body).lower()
        except Exception:
            return False

    async def devices(self) -> list[dict[str, Any]]:
        data = await self._get("/api/v1/devices?meta=true")
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        if isinstance(data, dict):
            keys = set(data.keys())
            if any(k.startswith("battery") or k.startswith("ups.") or k == "ups" for k in keys):
                return [data]
            return [v for v in data.values() if isinstance(v, dict)]
        return []

    async def device(self, name: str) -> dict[str, Any]:
        data = await self._get(f"/api/v1/devices/{name}?meta=true")
        return data if isinstance(data, dict) else {}

    async def snapshot(self, device_name: str = "") -> dict[str, Any]:
        if device_name:
            raw = await self.device(device_name)
        else:
            devices = await self.devices()
            if not devices:
                raise RuntimeError("PeaNUT returned no UPS devices")
            raw = devices[0]
            device_name = (
                str(raw.get("peanut", {}).get("device_id") or raw.get("device.serial") or "ups")
                if isinstance(raw.get("peanut"), dict)
                else "ups"
            )
        snap = snapshot_from_vars(raw)
        snap["device"] = device_name or snap.get("device") or ""
        return snap
