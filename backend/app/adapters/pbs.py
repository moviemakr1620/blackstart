from __future__ import annotations

from typing import Any

import httpx

from app.tlsutil import explain_tls_error, is_tls_error

from app.adapters.proxmox import split_api_token


class PbsAdapter:
    """Proxmox Backup Server REST API (port 8007). Not Proxmox VE."""

    def __init__(
        self,
        address: str,
        port: int = 8007,
        token_id: str = "",
        token_secret: str = "",
        verify_tls: Any = True,
        timeout: float = 20.0,
    ) -> None:
        if address.startswith("http"):
            host = address.rstrip("/")
        else:
            host = f"https://{address}:{port or 8007}"
        self.base = host
        token_id, token_secret = split_api_token(token_id, token_secret)
        # Official header is PBSAPIToken=USER@REALM!TOKENID:SECRET (colon, not equals).
        self.token = f"PBSAPIToken={token_id}:{token_secret}" if token_id and token_secret else ""
        self.verify = verify_tls
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = self.token
        return headers

    async def _request(self, method: str, path: str, data: dict | None = None) -> Any:
        url = f"{self.base}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout, verify=self.verify) as client:
                resp = await client.request(method, url, headers=self._headers(), data=data)
        except Exception as exc:
            if is_tls_error(exc):
                raise RuntimeError(explain_tls_error(exc, address=self.base, verify=self.verify)) from exc
            raise
        if resp.status_code >= 400:
            detail = (resp.text or "")[:400]
            raise RuntimeError(f"PBS {resp.status_code} {path}: {detail or resp.reason_phrase}")
        if not resp.content:
            return {}
        body = resp.json()
        return body.get("data", body)

    async def version(self) -> Any:
        return await self._request("GET", "/api2/json/version")

    async def nodes(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api2/json/nodes")
        return data if isinstance(data, list) else []

    async def node_name(self) -> str:
        try:
            for row in await self.nodes():
                raw = str(row.get("node") or row.get("name") or row.get("id") or "")
                name = raw.split("/")[-1]
                if name:
                    return name
        except Exception:
            pass
        return "localhost"

    async def shutdown(self, node: str | None = None) -> Any:
        name = (node or "").strip() or await self.node_name()
        return await self._request("POST", f"/api2/json/nodes/{name}/status", data={"command": "shutdown"})

    async def discover(self) -> list[dict[str, Any]]:
        ver = await self.version()
        node = await self.node_name()
        version = ""
        if isinstance(ver, dict):
            version = str(ver.get("version") or ver.get("release") or "")
        return [
            {
                "kind": "pbs",
                "name": f"PBS {node}",
                "external_id": node,
                "extra": {"node": node, "version": version},
                "last_status": "online",
            }
        ]
