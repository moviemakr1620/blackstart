from __future__ import annotations

from typing import Any

import httpx


class ProxmoxAdapter:
    def __init__(
        self,
        address: str,
        port: int = 8006,
        token_id: str = "",
        token_secret: str = "",
        verify_tls: bool = False,
        timeout: float = 15.0,
    ) -> None:
        host = address if address.startswith("http") else f"https://{address}"
        if "://" in host and ":" not in host.split("://", 1)[1].split("/")[0] and port:
            host = f"{host}:{port}"
        elif "://" not in address:
            host = f"https://{address}:{port or 8006}"
        self.base = host.rstrip("/")
        self.token = f"PVEAPIToken={token_id}={token_secret}" if token_id else ""
        self.verify = verify_tls
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = self.token
        return headers

    async def _request(self, method: str, path: str, data: dict | None = None) -> Any:
        url = f"{self.base}{path}"
        async with httpx.AsyncClient(timeout=self.timeout, verify=self.verify) as client:
            resp = await client.request(method, url, headers=self._headers(), data=data)
            resp.raise_for_status()
            if not resp.content:
                return {}
            body = resp.json()
            return body.get("data", body)

    async def version(self) -> Any:
        return await self._request("GET", "/api2/json/version")

    async def resources(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api2/json/cluster/resources")
        return data if isinstance(data, list) else []

    async def nodes(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api2/json/nodes")
        return data if isinstance(data, list) else []

    def _kind_path(self, kind: str) -> str:
        return "qemu" if kind in {"vm", "qemu"} else "lxc"

    async def status(self, node: str, kind: str, vmid: str | int) -> dict[str, Any]:
        data = await self._request(
            "GET",
            f"/api2/json/nodes/{node}/{self._kind_path(kind)}/{vmid}/status/current",
        )
        return data if isinstance(data, dict) else {}

    async def power(
        self,
        node: str,
        kind: str,
        vmid: str | int,
        action: str,
    ) -> Any:
        """action: start | shutdown | stop | reboot | reset"""
        return await self._request(
            "POST",
            f"/api2/json/nodes/{node}/{self._kind_path(kind)}/{vmid}/status/{action}",
        )

    async def shutdown_node(self, node: str) -> Any:
        return await self._request("POST", f"/api2/json/nodes/{node}/status/shutdown")

    async def discover(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for res in await self.resources():
            typ = res.get("type")
            if typ not in {"qemu", "lxc"}:
                continue
            kind = "vm" if typ == "qemu" else "lxc"
            vmid = str(res.get("vmid", ""))
            items.append(
                {
                    "kind": kind,
                    "name": str(res.get("name") or f"{kind}-{vmid}"),
                    "external_id": vmid,
                    "extra": {
                        "node": res.get("node"),
                        "status": res.get("status"),
                        "maxmem": res.get("maxmem"),
                        "maxcpu": res.get("maxcpu"),
                        "template": res.get("template"),
                    },
                    "last_status": res.get("status") or "unknown",
                }
            )
        return items
