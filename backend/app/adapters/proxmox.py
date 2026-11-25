from __future__ import annotations

from typing import Any
from urllib.parse import quote

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
        verify_tls: Any = True,
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
        self.warnings: list[str] = []
        self.stats: dict[str, Any] = {}

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = self.token
        return headers

    async def _request(self, method: str, path: str, data: dict | None = None, params: dict | None = None) -> Any:
        url = f"{self.base}{path}"
        kwargs: dict[str, Any] = {"headers": self._headers()}
        if data is not None:
            kwargs["data"] = data
        if params:
            kwargs["params"] = params
        async with httpx.AsyncClient(timeout=self.timeout, verify=self.verify) as client:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code >= 400:
                detail = (resp.text or "")[:400]
                raise RuntimeError(f"PVE {resp.status_code} {path}: {detail or resp.reason_phrase}")
            if not resp.content:
                return {}
            body = resp.json()
            if isinstance(body, dict) and "data" in body:
                payload = body["data"]
                return [] if payload is None else payload
            return body

    async def version(self) -> Any:
        return await self._request("GET", "/api2/json/version")

    async def resources(self, typ: str | None = None) -> list[dict[str, Any]]:
        params = {"type": typ} if typ else None
        data = await self._request("GET", "/api2/json/cluster/resources", params=params)
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
        params: dict[str, Any] | None = None,
    ) -> Any:
        """action: start | shutdown | stop | reboot | reset. Returns the task UPID."""
        return await self._request(
            "POST",
            f"/api2/json/nodes/{quote(str(node), safe='')}/{self._kind_path(kind)}/{quote(str(vmid), safe='')}"
            f"/status/{action}",
            data=params,
        )

    async def wait_task(self, node: str, upid: str, timeout: float = 120, interval: float = 2.0) -> dict[str, Any]:
        """Poll a PVE task until it stops. Raises if it fails or runs past `timeout`."""
        import asyncio

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        path = f"/api2/json/nodes/{quote(str(node), safe='')}/tasks/{quote(upid, safe='')}/status"
        while True:
            data = await self._request("GET", path)
            if isinstance(data, dict) and data.get("status") == "stopped":
                if str(data.get("exitstatus") or "") != "OK":
                    raise RuntimeError(f"Proxmox task failed: {data.get('exitstatus')}")
                return data
            if loop.time() >= deadline:
                raise RuntimeError(f"Proxmox task still running after {int(timeout)}s ({upid})")
            await asyncio.sleep(interval)

    async def shutdown_node(self, node: str) -> Any:
        return await self._request(
            "POST", f"/api2/json/nodes/{quote(str(node), safe='')}/status", data={"command": "shutdown"}
        )

    def _vmid(self, row: dict[str, Any]) -> str:
        v = row.get("vmid")
        if v is None or v == "":
            ident = str(row.get("id") or "")
            if "/" in ident:
                v = ident.split("/", 1)[1]
        if v is None or v == "":
            return ""
        text = str(v).strip()
        return str(int(text)) if text.isdigit() else text

    def _guest(
        self,
        kind: str,
        vmid: Any,
        name: Any,
        node: Any,
        status: Any,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        ident = str(vmid).strip() if vmid is not None and vmid != "" else ""
        if ident.isdigit():
            ident = str(int(ident))
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

    def _guest_kind(self, typ: str) -> str | None:
        t = (typ or "").lower()
        if t in {"lxc", "openvz", "ct"}:
            return "lxc"
        if t in {"qemu", "kvm", "vm"}:
            return "vm"
        return None

    def _ingest_resource(self, res: dict[str, Any], take) -> None:
        typ = str(res.get("type") or "")
        kind = self._guest_kind(typ)
        if kind is None:
            ident = str(res.get("id") or "")
            if ident.startswith("qemu/"):
                kind = "vm"
            elif ident.startswith("lxc/") or ident.startswith("openvz/"):
                kind = "lxc"
            else:
                return
        take(
            self._guest(
                kind,
                self._vmid(res),
                res.get("name"),
                res.get("node") or _node_id(res),
                res.get("status"),
                {
                    "maxmem": res.get("maxmem"),
                    "maxcpu": res.get("maxcpu") or res.get("cpus"),
                    "template": res.get("template"),
                },
            )
        )

    async def discover(self) -> list[dict[str, Any]]:
        """Import every QEMU VM and LXC on every node."""
        self.warnings = []
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

        node_names: list[str] = []

        def remember_node(name: str) -> None:
            n = (name or "").strip()
            if n and n not in node_names:
                node_names.append(n)

        for typ in (None, "vm"):
            try:
                rows = await self.resources(typ)
            except Exception as exc:  # noqa: BLE001
                self.warnings.append(f"cluster/resources{f'?type={typ}' if typ else ''}: {exc}")
                continue
            for res in rows:
                if str(res.get("type") or "") == "node":
                    remember_node(_node_id(res) or str(res.get("node") or ""))
                remember_node(str(res.get("node") or ""))
                self._ingest_resource(res, take)

        try:
            for row in await self.nodes():
                remember_node(_node_id(row))
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"nodes: {exc}")

        if not node_names:
            self.warnings.append("No Proxmox node names found. The token may be privilege-separated with no ACL.")

        for node in node_names:
            encoded = quote(node, safe="")
            for kind, path in (("vm", "qemu"), ("lxc", "lxc")):
                try:
                    guests = await self._request("GET", f"/api2/json/nodes/{encoded}/{path}")
                except Exception as exc:  # noqa: BLE001
                    self.warnings.append(f"{node}/{path}: {exc}")
                    continue
                if not isinstance(guests, list):
                    self.warnings.append(f"{node}/{path}: expected a list, got {type(guests).__name__}")
                    continue
                for g in guests:
                    take(
                        self._guest(
                            kind,
                            self._vmid(g) or g.get("vmid"),
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

        vms = sum(1 for g in found.values() if g["kind"] == "vm")
        lxcs = sum(1 for g in found.values() if g["kind"] == "lxc")
        if self.warnings:
            hint = (
                "A root API token with Privilege Separation enabled starts with zero rights. "
                "Datacenter → Permissions → Add → User/Token = this token, Path /, Role Administrator "
                "(or uncheck Privilege Separation on the token)."
            )
            if not any("Privilege" in w for w in self.warnings):
                self.warnings.append(hint)

        self.stats = {"vms": vms, "lxcs": lxcs, "nodes": node_names, "total": len(found)}
        return sorted(
            found.values(),
            key=lambda x: (x["kind"], int(x["external_id"]) if str(x["external_id"]).isdigit() else 0, x["name"]),
        )
