from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx


class SynologyAdapter:
    """DSM 6/7 WebAPI: auth, shutdown, Docker/Container Manager."""

    def __init__(
        self,
        address: str,
        port: int = 5001,
        username: str = "",
        password: str = "",
        otp: str = "",
        verify_tls: bool = False,
        use_https: bool = True,
        timeout: float = 15.0,
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

    async def ping(self) -> dict[str, Any]:
        await self.login()
        try:
            data = await self._get(
                "/webapi/entry.cgi",
                {
                    "api": "SYNO.Core.System",
                    "version": "1",
                    "method": "info",
                    "_sid": self._sid,
                },
            )
            return data.get("data") or data
        finally:
            await self.logout()

    async def shutdown(self) -> Any:
        await self.login()
        try:
            return await self._get(
                "/webapi/entry.cgi",
                {
                    "api": "SYNO.Core.System",
                    "version": "1",
                    "method": "shutdown",
                    "_sid": self._sid,
                },
            )
        finally:
            await self.logout()

    async def reboot(self) -> Any:
        await self.login()
        try:
            return await self._get(
                "/webapi/entry.cgi",
                {
                    "api": "SYNO.Core.System",
                    "version": "1",
                    "method": "reboot",
                    "_sid": self._sid,
                },
            )
        finally:
            await self.logout()

    async def list_containers(self) -> list[dict[str, Any]]:
        await self.login()
        try:
            for api in ("SYNO.Docker.Container", "SYNO.Core.Terminal"):
                try:
                    data = await self._get(
                        "/webapi/entry.cgi",
                        {
                            "api": "SYNO.Docker.Container",
                            "version": "1",
                            "method": "list",
                            "limit": -1,
                            "offset": 0,
                            "_sid": self._sid,
                        },
                    )
                    containers = (data.get("data") or {}).get("containers") or data.get("data") or []
                    if isinstance(containers, list):
                        return containers
                except Exception:
                    continue
            return []
        finally:
            await self.logout()

    async def container(self, name: str, method: str) -> Any:
        """method: start | stop"""
        await self.login()
        try:
            return await self._get(
                "/webapi/entry.cgi",
                {
                    "api": "SYNO.Docker.Container",
                    "version": "1",
                    "method": method,
                    "name": name,
                    "_sid": self._sid,
                },
            )
        finally:
            await self.logout()

    async def discover(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = [
            {
                "kind": "nas",
                "name": "Synology DSM",
                "external_id": "dsm",
                "extra": {},
                "last_status": "unknown",
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
