from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx


class SynologyAdapter:
    """DSM 6/7 WebAPI: auth, shutdown (including SHA cluster), Docker."""

    def __init__(
        self,
        address: str,
        port: int = 5001,
        username: str = "",
        password: str = "",
        otp: str = "",
        verify_tls: bool = False,
        use_https: bool = True,
        timeout: float = 20.0,
    ) -> None:
        scheme = "https" if use_https else "http"
        if address.startswith("http"):
            self.base = address.rstrip("/")
        else:
            self.base = f"{scheme}://{address}:{port or (5001 if use_https else 5000)}"
        self.username = username
        self.password = password
        self.otp = otp
        self.verify = verify_tls
        self.timeout = timeout
        self._sid = ""

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base}{path}?{urlencode(params)}"
        async with httpx.AsyncClient(timeout=self.timeout, verify=self.verify) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success", True) and data.get("error"):
                raise RuntimeError(f"Synology API error: {data.get('error')}")
            return data

    async def login(self) -> str:
        params = {
            "api": "SYNO.API.Auth",
            "version": "6",
            "method": "login",
            "account": self.username,
            "passwd": self.password,
            "session": "Blackstart",
            "format": "sid",
        }
        if self.otp:
            params["otp_code"] = self.otp
        try:
            data = await self._get("/webapi/auth.cgi", params)
        except Exception:
            params["version"] = "3"
            data = await self._get("/webapi/auth.cgi", params)
        sid = (data.get("data") or {}).get("sid")
        if not sid:
            raise RuntimeError(f"Synology login failed: {data}")
        self._sid = sid
        return sid

    async def logout(self) -> None:
        if not self._sid:
            return
        try:
            await self._get(
                "/webapi/auth.cgi",
                {
                    "api": "SYNO.API.Auth",
                    "version": "1",
                    "method": "logout",
                    "session": "Blackstart",
                    "_sid": self._sid,
                },
            )
        except Exception:
            pass
        self._sid = ""

    async def _entry(self, api: str, method: str, version: int = 1, **extra: Any) -> dict[str, Any]:
        params = {"api": api, "version": str(version), "method": method, "_sid": self._sid, **extra}
        return await self._get("/webapi/entry.cgi", params)

    async def ha_info(self) -> dict[str, Any]:
        """Best-effort SHA/HA detection. Never raises — empty dict if not a cluster."""
        await self.login()
        try:
            for api, method, version in (
                ("SYNO.Core.HA", "get", 1),
                ("SYNO.Core.HA", "status", 1),
                ("SYNO.HA.Cluster", "get", 1),
                ("SYNO.HA.Cluster", "status", 1),
                ("SYNO.SHA.Cluster", "get", 1),
            ):
                try:
                    data = await self._entry(api, method, version)
                    payload = data.get("data") if isinstance(data, dict) else None
                    if payload:
                        return {"ha": True, "api": api, "info": payload}
                    if data.get("success"):
                        return {"ha": True, "api": api, "info": data}
                except Exception:
                    continue
            return {"ha": False}
        finally:
            await self.logout()

    async def ping(self) -> dict[str, Any]:
        info: dict[str, Any] = {}
        await self.login()
        try:
            data = await self._entry("SYNO.Core.System", "info", 1)
            info = data.get("data") or data
        finally:
            await self.logout()
        ha = await self.ha_info()
        info["ha"] = bool(ha.get("ha"))
        info["ha_detail"] = ha
        return info

    async def shutdown(self) -> Any:
        """Standalone DSM shutdown of whatever host this adapter points at."""
        await self.login()
        try:
            return await self._entry("SYNO.Core.System", "shutdown", 1)
        finally:
            await self.logout()

    async def shutdown_cluster(self) -> dict[str, Any]:
        """
        Prefer SHA cluster shutdown so the passive node does not take over.
        Falls back to a normal DSM shutdown of this address.
        """
        await self.login()
        attempts: list[tuple[str, str, int]] = [
            ("SYNO.HA.Cluster", "shutdown", 1),
            ("SYNO.HA.Cluster", "poweroff", 1),
            ("SYNO.Core.HA", "shutdown", 1),
            ("SYNO.Core.HA", "shutdown_cluster", 1),
            ("SYNO.SHA.Cluster", "shutdown", 1),
            ("SYNO.Core.System", "shutdown", 1),
        ]
        last_error = "no HA shutdown method succeeded"
        try:
            for api, method, version in attempts:
                try:
                    data = await self._entry(api, method, version)
                    return {"ok": True, "api": api, "method": method, "data": data}
                except Exception as exc:  # noqa: BLE001
                    last_error = f"{api}.{method}: {exc}"
                    continue
        finally:
            await self.logout()
        raise RuntimeError(last_error)

    async def reboot(self) -> Any:
        await self.login()
        try:
            return await self._entry("SYNO.Core.System", "reboot", 1)
        finally:
            await self.logout()

    async def list_containers(self) -> list[dict[str, Any]]:
        await self.login()
        try:
            try:
                data = await self._entry("SYNO.Docker.Container", "list", 1, limit=-1, offset=0)
                containers = (data.get("data") or {}).get("containers") or data.get("data") or []
                if isinstance(containers, list):
                    return containers
            except Exception:
                pass
            return []
        finally:
            await self.logout()

    async def container(self, name: str, method: str) -> Any:
        await self.login()
        try:
            return await self._entry("SYNO.Docker.Container", method, 1, name=name)
        finally:
            await self.logout()

    async def discover(self) -> list[dict[str, Any]]:
        ha = await self.ha_info()
        label = "Synology HA cluster" if ha.get("ha") else "Synology DSM"
        items: list[dict[str, Any]] = [
            {
                "kind": "nas",
                "name": label,
                "external_id": "dsm",
                "extra": {"ha": bool(ha.get("ha"))},
                "last_status": "ha" if ha.get("ha") else "unknown",
            }
        ]
        try:
            for c in await self.list_containers():
                name = str(c.get("name") or c.get("id") or "")
                if not name:
                    continue
                items.append(
                    {
                        "kind": "container",
                        "name": name,
                        "external_id": name,
                        "extra": {"status": c.get("status") or c.get("State")},
                        "last_status": str(c.get("status") or c.get("State") or "unknown"),
                    }
                )
        except Exception:
            pass
        return items
