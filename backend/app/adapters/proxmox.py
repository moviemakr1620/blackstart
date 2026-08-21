from __future__ import annotations

from typing import Any

import httpx


def split_api_token(token_id: str, token_secret: str) -> tuple[str, str]:
    """Accept id + secret, or a pasted `user@pam!name=uuid` / `user@pam!name:uuid` blob."""
    tid = (token_id or "").strip()
    sec = (token_secret or "").strip()
    for prefix in ("PBSAPIToken=", "PVEAPIToken="):
        if tid.startswith(prefix):
            tid = tid[len(prefix) :]
    if not sec and "!" in tid:
        rest = tid.split("!", 1)[1]
        if ":" in rest:
            tid, sec = tid.rsplit(":", 1)
        elif "=" in rest:
            tid, _, sec = tid.partition("=")
    return tid.strip(), sec.strip()


def _node_id(row: dict[str, Any]) -> str:
    raw = str(row.get("node") or row.get("name") or row.get("id") or "")
    return raw.split("/")[-1] if raw else ""


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
        token_id, token_secret = split_api_token(token_id, token_secret)
        self.token = f"PVEAPIToken={token_id}={token_secret}" if token_id and token_secret else ""
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
            if resp.status_code >= 400:
                detail = (resp.text or "")[:400]
                raise RuntimeError(f"PVE {resp.status_code} {path}: {detail or resp.reason_phrase}")
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

    def _guest(
        self,
        kind: str,
        vmid: Any,
        name: Any,
        node: Any,
        status: Any,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        ident = str(vmid or "").strip()
        if not ident:
            return None
        payload = {
            "kind": kind,
            "name": str(name or f"{kind}-{ident}"),
            "external_id": ident,
            "extra": {
                "node": node,
                "status": status,
                **(extra or {}),
            },
            "last_status": status or "unknown",
        }
        return payload

    async def discover(self) -> list[dict[str, Any]]:
        """Import every QEMU VM and LXC.

        `/cluster/resources` is convenient but incomplete when the token cannot
        see the whole cluster view. Walk each node’s qemu + lxc lists and merge.
        """
        found: dict[tuple[str, str], dict[str, Any]] = {}

        def take(item: dict[str, Any] | None) -> None:
            if not item:
                return
            key = (item["kind"], item["external_id"])
            prev = found.get(key)
            if prev:
                extra = {**(prev.get("extra") or {}), **(item.get("extra") or {})}
                name = item["name"] if item["name"] and not item["name"].startswith(item["kind"] + "-") else prev["name"]
                found[key] = {**prev, **item, "name": name, "extra": extra}
            else:
                found[key] = item

        try:
            for res in await self.resources():
                typ = str(res.get("type") or "")
                if typ not in {"qemu", "lxc", "openvz"}:
                    continue
                kind = "lxc" if typ in {"lxc", "openvz"} else "vm"
                take(
                    self._guest(
                        kind,
                        res.get("vmid"),
                        res.get("name"),
                        res.get("node"),
                        res.get("status"),
                        {
                            "maxmem": res.get("maxmem"),
                            "maxcpu": res.get("maxcpu"),
                            "template": res.get("template"),
                        },
                    )
                )
        except Exception:
            pass

        try:
            nodes = await self.nodes()
        except Exception:
            nodes = []

        for row in nodes:
            node = _node_id(row)
            if not node:
                continue
            for kind, path in (("vm", "qemu"), ("lxc", "lxc")):
                try:
                    guests = await self._request("GET", f"/api2/json/nodes/{node}/{path}")
                except Exception:
                    continue
                if not isinstance(guests, list):
                    continue
                for g in guests:
                    take(
                        self._guest(
                            kind,
                            g.get("vmid"),
                            g.get("name"),
                            node,
                            g.get("status"),
                            {
                                "template": g.get("template"),
                                "maxmem": g.get("maxmem"),
                                "maxcpu": g.get("cpus") or g.get("maxcpu"),
                            },
                        )
                    )

        return sorted(
            found.values(),
            key=lambda x: (x["kind"], int(x["external_id"]) if str(x["external_id"]).isdigit() else 0, x["name"]),
        )
