from __future__ import annotations

from typing import Any

import httpx

from app.tlsutil import explain_tls_error, is_tls_error

from app.adapters.ssh import SshAdapter


class OmvAdapter:
    """OpenMediaVault RPC (OMV 6/7) with SSH fallback."""

    def __init__(
        self,
        address: str,
        port: int = 443,
        username: str = "",
        password: str = "",
        verify_tls: Any = True,
        use_https: bool = True,
        timeout: float = 15.0,
        ssh: SshAdapter | None = None,
    ) -> None:
        scheme = "https" if use_https else "http"
        if address.startswith("http"):
            self.base = address.rstrip("/")
        else:
            self.base = f"{scheme}://{address}:{port or (443 if use_https else 80)}"
        self.username = username
        self.password = password
        self.verify = verify_tls
        self.timeout = timeout
        self.ssh = ssh
        self._session = ""

    async def _rpc(self, service: str, method: str, params: Any = None, session: str | None = None) -> Any:
        headers = {"Content-Type": "application/json"}
        sid = session if session is not None else self._session
        if sid:
            headers["Cookie"] = f"X-OPENMEDIAVAULT-SESSIONID={sid}"
        payload = {"service": service, "method": method, "params": params}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, verify=self.verify) as client:
                resp = await client.post(f"{self.base}/rpc.php", json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            if is_tls_error(exc):
                raise RuntimeError(explain_tls_error(exc, address=self.base, verify=self.verify)) from exc
            raise
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(str(data.get("error")))
        return data.get("response", data)

    async def login(self) -> str:
        data = await self._rpc("Session", "login", {"username": self.username, "password": self.password}, session="")
        sid = ""
        if isinstance(data, dict):
            sid = str(data.get("sessionid") or data.get("sessionId") or "")
        if not sid:
            raise RuntimeError(f"OMV login failed: {data}")
        self._session = sid
        return sid

    async def ping(self) -> dict[str, Any]:
        try:
            await self.login()
            info = await self._rpc("System", "getInformation")
            return {"ok": True, "info": info}
        except Exception as rpc_err:
            if self.ssh:
                res = await self.ssh.run("hostname && cat /etc/issue | head -1")
                return {"ok": True, "via": "ssh", "rpc_error": str(rpc_err), **res}
            raise

    async def shutdown(self) -> Any:
        try:
            await self.login()
            try:
                return await self._rpc("System", "halt")
            except Exception:
                return await self._rpc("System", "shutdown")
        except Exception:
            if not self.ssh:
                raise
            return await self.ssh.run("sudo shutdown -h now || sudo systemctl poweroff")

    async def reboot(self) -> Any:
        try:
            await self.login()
            return await self._rpc("System", "reboot")
        except Exception:
            if not self.ssh:
                raise
            return await self.ssh.run("sudo reboot")

    async def discover(self) -> list[dict[str, Any]]:
        return [
            {
                "kind": "nas",
                "name": "OpenMediaVault",
                "external_id": "omv",
                "extra": {},
                "last_status": "unknown",
            }
        ]
