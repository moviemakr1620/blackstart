from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx


class DockerAdapter:
    """Talk to the Docker Engine API over a unix socket or TCP."""

    def __init__(
        self,
        address: str = "unix:///var/run/docker.sock",
        timeout: float = 20.0,
    ) -> None:
        self.address = address
        self.timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        if self.address.startswith("unix://"):
            path = self.address.removeprefix("unix://")
            transport = httpx.AsyncHTTPTransport(uds=path)
            return httpx.AsyncClient(
                transport=transport,
                base_url="http://docker",
                timeout=self.timeout,
            )
        base = self.address if self.address.startswith("http") else f"http://{self.address}"
        return httpx.AsyncClient(base_url=base, timeout=self.timeout)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        async with self._client() as client:
            resp = await client.request(method, path, **kwargs)
            if resp.status_code == 204 or not resp.content:
                return {}
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            if "json" in ctype:
                return resp.json()
            return resp.text

    async def ping(self) -> bool:
        try:
            await self._request("GET", "/_ping")
            return True
        except Exception:
            return False

    async def version(self) -> dict[str, Any]:
        data = await self._request("GET", "/version")
        return data if isinstance(data, dict) else {}

    async def containers(self, all_: bool = True) -> list[dict[str, Any]]:
        data = await self._request("GET", "/containers/json", params={"all": "1" if all_ else "0"})
        return data if isinstance(data, list) else []

    async def container_action(self, ident: str, action: str, timeout: int = 20) -> Any:
        ident = quote(ident, safe="")
        if action == "stop":
            return await self._request("POST", f"/containers/{ident}/stop", params={"t": str(timeout)})
        if action == "start":
            return await self._request("POST", f"/containers/{ident}/start")
        if action == "restart":
            return await self._request("POST", f"/containers/{ident}/restart", params={"t": str(timeout)})
        if action == "kill":
            return await self._request("POST", f"/containers/{ident}/kill")
        raise ValueError(f"unknown docker action {action}")

    def _project(self, container: dict[str, Any]) -> str:
        labels = container.get("Labels") or {}
        return str(labels.get("com.docker.compose.project") or "")

    async def stacks(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for c in await self.containers(True):
            project = self._project(c)
            if not project:
                continue
            grouped.setdefault(project, []).append(c)
        return grouped

    async def stack_action(self, project: str, action: str, timeout: int = 20) -> list[str]:
        stacks = await self.stacks()
        containers = stacks.get(project, [])
        # Stop dependents first-ish: reverse created order. Start in listed order.
        names: list[str] = []
        ordered = list(containers)
        if action == "stop":
            ordered = list(reversed(ordered))
        for c in ordered:
            name = (c.get("Names") or [c.get("Id", "")[:12]])[0].lstrip("/")
            ident = c.get("Id") or name
            running = str(c.get("State") or "").lower() == "running"
            if action == "stop" and not running:
                names.append(f"{name}:already-stopped")
                continue
            if action == "start" and running:
                names.append(f"{name}:already-running")
                continue
            await self.container_action(str(ident), action, timeout=timeout)
            names.append(name)
        return names

    async def discover(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen_stacks: set[str] = set()
        for c in await self.containers(True):
            names = c.get("Names") or []
            name = names[0].lstrip("/") if names else str(c.get("Id", ""))[:12]
            project = self._project(c)
            items.append(
                {
                    "kind": "container",
                    "name": name,
                    "external_id": name,
                    "extra": {
                        "image": (c.get("Image") or ""),
                        "state": c.get("State"),
                        "stack": project,
                    },
                    "last_status": c.get("State") or "unknown",
                }
            )
            if project and project not in seen_stacks:
                seen_stacks.add(project)
                items.append(
                    {
                        "kind": "stack",
                        "name": f"stack:{project}",
                        "external_id": project,
                        "extra": {"stack": project},
                        "last_status": "unknown",
                    }
                )
        return items
