from __future__ import annotations

from typing import Any

import httpx

from app.adapters.nut import snapshot_from_vars


class PeanutAuthError(RuntimeError):
    pass


class PeanutClient:
    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        timeout: float = 8.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.auth = (username, password) if username else None
        self.timeout = timeout

    async def _get(self, path: str) -> Any:
        async with httpx.AsyncClient(timeout=self.timeout, auth=self.auth, follow_redirects=True) as client:
            resp = await client.get(f"{self.base_url}{path}")
            if resp.status_code == 401:
                raise PeanutAuthError(
                    "PeaNUT returned 401 Unauthorized. Enter the same username and password "
                    "you use to sign into the PeaNUT web UI (Settings → edit the PeaNUT source). "
                    "The API is separate from the dashboard cookie — Blackstart must send Basic Auth."
                )
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            if "json" in ctype:
                return resp.json()
            text = resp.text
            if text.strip().startswith("{") or text.strip().startswith("["):
                return resp.json()
            return text

    async def ping(self) -> bool:
        try:
            body = await self._get("/api/ping")
            return "pong" in str(body).lower()
        except PeanutAuthError:
            raise
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

    def _device_id(self, raw: dict[str, Any], fallback: str) -> str:
        peanut = raw.get("peanut")
        if isinstance(peanut, dict) and peanut.get("device_id"):
            return str(peanut["device_id"])
        return str(raw.get("device.serial") or raw.get("ups.id") or fallback or "ups")

    async def snapshot(self, device_name: str = "") -> dict[str, Any]:
        raw: dict[str, Any] | None = None
        chosen = device_name.strip()
        if chosen:
            try:
                raw = await self.device(chosen)
            except PeanutAuthError:
                raise
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {404, 400}:
                    raise
                raw = None
        if not raw:
            devices = await self.devices()
            if not devices:
                raise RuntimeError("PeaNUT returned no UPS devices. Check the NUT server inside PeaNUT.")
            if chosen:
                lower = chosen.lower()
                raw = next(
                    (
                        d
                        for d in devices
                        if lower in self._device_id(d, "").lower() or lower in str(d.get("device.model", "")).lower()
                    ),
                    devices[0],
                )
            else:
                raw = devices[0]
            chosen = self._device_id(raw, chosen)
        snap = snapshot_from_vars(raw)
        snap["device"] = chosen or snap.get("device") or ""
        return snap
